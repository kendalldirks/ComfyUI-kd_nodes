import torch
import torch.nn.functional as F


MAX_DIM = 16384

FIT_MODES = ["fit", "fill", "stretch", "crop"]

# Fallback canvas when nothing is wired to `canvas`: black, fully transparent.
# Kept internal for now -- if these ever become widgets, they are the only two
# numbers that need to move.
CANVAS_RGB = 0.0
CANVAS_ALPHA = 0.0


# --- bbox ---

def normalize_bbox(bboxes):
    """
    Anything BBOX-shaped -> a flat list of (x, y, w, h) int tuples.

    Accepts KJNodes' tuples and ComfyUI's native BOUNDING_BOX dicts, single or
    nested, so the socket takes whatever the graph happens to be carrying.
    Deliberately reimplemented rather than imported from KJNodes: this pack does
    not depend on that one being installed.
    """
    if isinstance(bboxes, torch.Tensor):
        bboxes = bboxes.tolist()
    if isinstance(bboxes, dict):
        bboxes = [bboxes]
    elif isinstance(bboxes, (list, tuple)):
        # A bare 4-tuple is one box, not four boxes.
        if len(bboxes) == 4 and all(isinstance(v, (int, float)) for v in bboxes):
            bboxes = [bboxes]
    else:
        raise ValueError(f"ImageStitcherKD: unsupported bbox {type(bboxes).__name__}")

    out = []
    for b in bboxes:
        if isinstance(b, torch.Tensor):
            b = b.tolist()
        if isinstance(b, (list, tuple)) and b and \
                not all(isinstance(v, (int, float)) for v in b):
            out.extend(normalize_bbox(b))          # nested, e.g. per-frame boxes
            continue
        if isinstance(b, dict):
            out.append((int(b["x"]), int(b["y"]),
                        int(b["width"]), int(b["height"])))
        elif isinstance(b, (list, tuple)) and len(b) >= 4:
            out.append(tuple(int(v) for v in b[:4]))
        else:
            raise ValueError(f"ImageStitcherKD: invalid bbox {b!r}")

    if not out:
        raise ValueError("ImageStitcherKD: bbox is empty")
    return out


# --- tensor helpers ---

def split_alpha(img):
    """
    (B, H, W, C) -> (rgb, alpha) with rgb 3-channel and alpha (B, H, W, 1) or
    None when the image carries none.
    """
    c = int(img.shape[-1])
    if c >= 4:
        return img[..., :3], img[..., 3:4]
    if c == 3:
        return img, None
    if c == 2:                                  # grey + alpha
        return img[..., 0:1].repeat(1, 1, 1, 3), img[..., 1:2]
    if c == 1:                                  # grey
        return img.repeat(1, 1, 1, 3), None
    raise ValueError(f"ImageStitcherKD: cannot read a {c}-channel image")


def match_batch(t, n):
    """
    Broadcast a batch to n frames by holding the last one.

    A single frame is expanded rather than copied -- one canvas behind a
    289-frame patch batch should not cost 289 canvases. The result may be a
    view, so callers that write to it must clone first.
    """
    have = int(t.shape[0])
    if have == n:
        return t
    if have == 1:
        return t.expand(n, -1, -1, -1)
    idx = torch.clamp(torch.arange(n, device=t.device), max=have - 1)
    return t[idx]


def resample(img, h, w):
    """
    (B, H, W, C) resized to h x w, in whatever way suits the direction.

    area for a reduction: it is a box filter, which is both the right answer and
    cheaper than an antialiased bilinear. bicubic for an enlargement, since it
    keeps an upscaled crop from going soft -- clamped afterwards because bicubic
    overshoots outside [0, 1] on high-contrast edges.
    """
    sh, sw = int(img.shape[1]), int(img.shape[2])
    if (sh, sw) == (h, w):
        return img

    x = img.permute(0, 3, 1, 2)
    if h < sh or w < sw:
        x = F.interpolate(x, size=(h, w), mode="area")
    else:
        x = F.interpolate(x, size=(h, w), mode="bicubic", align_corners=False)
    return x.permute(0, 2, 3, 1).clamp(0.0, 1.0)


def plan(pw, ph, bw, bh, fit):
    """
    Where a pw x ph patch goes inside a bw x bh bbox, as
    (scaled_w, scaled_h, offset_x, offset_y) with the offset relative to the
    bbox's top-left corner. Offsets may be negative and the size may exceed the
    bbox -- the caller clips, which is what makes fill and crop discard edges.
    """
    if fit == "stretch":
        return bw, bh, 0, 0

    if fit == "crop":
        # No resampling at all: original pixels, anchored bottom-left, and
        # whatever runs past the bbox is discarded. Bottom-left because that is
        # where an image's origin is in the compositing world this feeds.
        return pw, ph, 0, bh - ph

    # fit contains the patch inside the bbox; fill covers the bbox with it.
    # Both keep the aspect ratio and centre what is left over.
    pick = min if fit == "fit" else max
    s = pick(bw / max(pw, 1), bh / max(ph, 1))
    sw = max(1, int(round(pw * s)))
    sh = max(1, int(round(ph * s)))
    return sw, sh, (bw - sw) // 2, (bh - sh) // 2


def composite(out, patch, box, fit):
    """
    Stitch `patch` into `out` at `box`, in place. Both are (B, H, W, C) with
    matching B; out is 3- or 4-channel, patch is whatever came in.
    """
    H, W = int(out.shape[1]), int(out.shape[2])
    ph, pw = int(patch.shape[1]), int(patch.shape[2])
    bx, by, bw, bh = box

    if bw < 1 or bh < 1:
        return

    sw, sh, ox, oy = plan(pw, ph, bw, bh, fit)

    # The visible rectangle: inside the bbox AND inside the canvas. Clipping to
    # the bbox as well as the canvas is what stops fill and crop painting
    # outside the region they were given.
    x0 = max(bx + ox, bx, 0)
    y0 = max(by + oy, by, 0)
    x1 = min(bx + ox + sw, bx + bw, W)
    y1 = min(by + oy + sh, by + bh, H)
    if x1 <= x0 or y1 <= y0:
        return                                  # entirely off-canvas

    rgb, alpha = split_alpha(patch)

    if (sh, sw) != (ph, pw):
        # Premultiply across the resize. Straight alpha lets the colour of a
        # fully transparent pixel bleed into its visible neighbours, which shows
        # up as a dark fringe wherever upstream left black under the alpha.
        if alpha is not None:
            scaled = resample(torch.cat([rgb * alpha, alpha], dim=-1), sh, sw)
            rgb, alpha = scaled[..., :3], scaled[..., 3:4]
            rgb = rgb / alpha.clamp(min=1e-6)
            rgb = torch.where(alpha > 0, rgb, torch.zeros_like(rgb)).clamp(0.0, 1.0)
        else:
            rgb = resample(rgb, sh, sw)

    # Same rectangle, expressed in the scaled patch's own coordinates.
    sx0, sy0 = x0 - (bx + ox), y0 - (by + oy)
    src_rgb = rgb[:, sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0), :]
    src_a = (alpha[:, sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0), :]
             if alpha is not None else None)

    dst = out[:, y0:y1, x0:x1, :]

    if src_a is None:
        # No alpha to respect: the patch replaces the region outright, and an
        # RGBA canvas becomes opaque there.
        dst[..., :3] = src_rgb
        if out.shape[-1] == 4:
            dst[..., 3:4] = 1.0
        return

    if out.shape[-1] == 4:
        # Source-over on straight alpha. Written out rather than lerped, because
        # a lerp against a transparent canvas mixes in its colour -- so a
        # half-transparent patch over transparent black would come back half
        # black instead of keeping its own colour.
        d_rgb, d_a = dst[..., :3], dst[..., 3:4]
        o_a = src_a + d_a * (1.0 - src_a)
        o_rgb = (src_rgb * src_a + d_rgb * d_a * (1.0 - src_a)) / o_a.clamp(min=1e-6)
        dst[..., :3] = torch.where(o_a > 0, o_rgb, torch.zeros_like(o_rgb)).clamp(0.0, 1.0)
        dst[..., 3:4] = o_a
    else:
        # An RGB canvas is opaque by definition, so source-over collapses to
        # exactly this.
        dst[...] = dst * (1.0 - src_a) + src_rgb * src_a


# --- node ---

class ImageStitcherKD:
    """
    Put a crop back where it came from.

    The tandem half of CropImageKD: feed it that node's bbox and the processed
    crop, and it composites the result into the original frame. With nothing
    wired to `canvas` it builds a transparent black one at width x height
    instead, so it doubles as a "place this image on a canvas" node.
    """

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "patch": ("IMAGE", {"tooltip": "The image to stitch in. Alpha, "
                                               "if present, is composited."}),
                "bbox": ("BBOX", {"tooltip": "Where to put it. Feed the bbox "
                                             "output of Crop Image KD."}),
                "fit": (FIT_MODES, {"default": "fit",
                                    "tooltip": "How to size the patch into the "
                                               "bbox when they disagree. fit: "
                                               "whole patch inside, centred. "
                                               "fill: cover the bbox, centred, "
                                               "edges discarded. stretch: to "
                                               "the bbox exactly, ratio ignored. "
                                               "crop: no scaling, anchored "
                                               "bottom-left."}),
                "width": ("INT", {"default": 1024, "min": 1, "max": MAX_DIM, "step": 1,
                                  "tooltip": "Canvas width. Ignored when `canvas` "
                                             "is connected."}),
                "height": ("INT", {"default": 1024, "min": 1, "max": MAX_DIM, "step": 1,
                                   "tooltip": "Canvas height. Ignored when "
                                              "`canvas` is connected."}),
            },
            "optional": {
                "canvas": ("IMAGE", {"tooltip": "The original image to stitch "
                                                "back into; sets the output "
                                                "size and keeps its own alpha. "
                                                "Leave unconnected for a "
                                                "transparent black canvas at "
                                                "width x height."}),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "stitch"
    CATEGORY = "KDNodes/image"

    def stitch(self, patch, bbox, fit, width, height, canvas=None):
        if patch.shape[0] == 0:
            raise ValueError("ImageStitcherKD: patch batch is empty")

        boxes = normalize_bbox(bbox)

        if canvas is None:
            # RGBA, so an unconnected canvas can be composited onto downstream
            # rather than baking the patch against black.
            base = torch.full((1, int(height), int(width), 4), CANVAS_RGB,
                              dtype=patch.dtype, device=patch.device)
            base[..., 3] = CANVAS_ALPHA
        else:
            if canvas.shape[0] == 0:
                raise ValueError("ImageStitcherKD: canvas batch is empty")
            c = int(canvas.shape[-1])
            if c not in (3, 4):
                # Grey or grey+alpha in, RGB or RGBA out.
                rgb, a = split_alpha(canvas)
                canvas = torch.cat([rgb, a], dim=-1) if a is not None else rgb
            base = canvas

        n = max(int(patch.shape[0]), int(base.shape[0]))

        # clone(): match_batch may hand back a view, and this is written to.
        out = match_batch(base, n).clone()
        src = match_batch(patch, n)

        # One bbox for the whole batch is the overwhelmingly common case and the
        # only one worth vectorising; a per-frame list falls back to a frame at
        # a time through the same code path.
        per_frame = [boxes[min(i, len(boxes) - 1)] for i in range(n)]
        if len(set(per_frame)) == 1:
            composite(out, src, per_frame[0], fit)
        else:
            for i in range(n):
                composite(out[i:i + 1], src[i:i + 1], per_frame[i], fit)

        return (out,)
