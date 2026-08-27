import os
import random

import torch
import torch.nn.functional as F
from PIL import Image

import folder_paths


BIGMAX = (2**53 - 1)

# Longest edge of the preview PNG handed to the frontend.  The crop itself is
# always taken from the full-resolution tensor and never from this file, so the
# proxy only has to be sharp enough to aim with -- but the graph zooms, so
# "enough" is however far in you tend to zoom.  Deliberately a constant and not
# a widget: it changes nothing about the output, and a control that only
# affects how a preview looks is clutter on every instance of the node.
#
# Never upscales, so anything already under this is written through untouched.
# The cost of raising it is PNG encode time and the size of the file the
# browser then fetches -- at 4096 a wide source lands around 20-30 MB, which is
# nothing over localhost but is not free on the encode side.  Drop to 2048 if
# writing the proxy ever feels slow on 8K plates.
PREVIEW_TARGET_SIZE = 4096

# Smallest crop the node will emit, in source pixels.  Mirrors MIN_CROP in
# kd_crop.js -- the frontend stops the box shrinking past it, and this is the
# backstop for a value typed straight into the widget.
MIN_CROP = 8


def clamp_rect(x, y, width, height, src_w, src_h, min_crop=MIN_CROP):
    """
    Crop rect clamped inside a src_w x src_h image, returned as (x, y, w, h).

    A width or height of 0 means "to the far edge", which is what lets the
    node's defaults crop the whole image before the box has ever been touched.
    The result is always at least min_crop on each axis (or the whole axis, on
    a source smaller than that), so the crop can never come back empty.
    """
    src_w, src_h = int(src_w), int(src_h)
    if src_w < 1 or src_h < 1:
        raise ValueError(f"clamp_rect: invalid source {src_w}x{src_h}")

    min_w = min(int(min_crop), src_w)
    min_h = min(int(min_crop), src_h)

    x = max(0, min(int(x), src_w - min_w))
    y = max(0, min(int(y), src_h - min_h))

    w = int(width) if int(width) > 0 else src_w - x
    h = int(height) if int(height) > 0 else src_h - y

    w = max(min_w, min(w, src_w - x))
    h = max(min_h, min(h, src_h - y))
    return x, y, w, h


def _preview_uint8(images, target_size=PREVIEW_TARGET_SIZE):
    """
    First frame of the batch as an (H, W, 3) uint8 array, downscaled so its
    longest edge is at most target_size.

    Deliberately not utils.frames_to_uint8: that rounds both axes to even
    numbers because yuv420p requires it, and a PNG does not.  The exact aspect
    ratio matters here -- the overlay maps source pixels onto the displayed
    image, so a one-pixel trim would leave the crop box slightly out of
    register with what the user is aiming at.
    """
    frame = images[0:1, ..., :3].float()
    h, w = int(frame.shape[1]), int(frame.shape[2])

    longest = max(h, w)
    if 0 < target_size < longest:
        scale = target_size / longest
        th = max(1, int(round(h * scale)))
        tw = max(1, int(round(w * scale)))
        # "area" is a box filter -- the right choice for a large reduction like
        # 3840 -> 1024, and cheaper than bilinear+antialias.
        frame = F.interpolate(frame.permute(0, 3, 1, 2), size=(th, tw),
                              mode="area").permute(0, 2, 3, 1)

    frame = frame.mul(255.0).round_().clamp_(0.0, 255.0).to(torch.uint8)
    return frame[0].cpu().numpy()


class CropImageKD:
    """
    Freeform crop driven by a draggable bounding box drawn over the image.

    On execution the first frame of the batch is written to the temp directory
    as a small PNG and handed to the frontend, which draws the box over it.
    Dragging is entirely client-side and executes nothing -- it only moves the
    x / y / width / height widgets, so the new crop lands on the next queue.
    That is the same arrangement as ExtractFrameKD, and for the same reason:
    ComfyUI has no reactive execution.

    The whole batch is cropped with one rect; the box is aimed using frame 0.
    """

    def __init__(self):
        self.output_dir = folder_paths.get_temp_directory()
        self.type = "temp"
        self.prefix_append = "_temp_" + ''.join(
            random.choice("abcdefghijklmnopqrstupvxyz") for _ in range(5))
        # (images_ref, preview_dict) -- see crop() for why identity works.
        self._cache = None

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
                "x": ("INT", {"default": 0, "min": 0, "max": BIGMAX, "step": 1,
                              "tooltip": "Left edge of the crop, in source pixels."}),
                "y": ("INT", {"default": 0, "min": 0, "max": BIGMAX, "step": 1,
                              "tooltip": "Top edge of the crop, in source pixels."}),
                "width": ("INT", {"default": 0, "min": 0, "max": BIGMAX, "step": 1,
                                  "tooltip": "Crop width in source pixels. "
                                             "0 means out to the right edge."}),
                "height": ("INT", {"default": 0, "min": 0, "max": BIGMAX, "step": 1,
                                   "tooltip": "Crop height in source pixels. "
                                              "0 means down to the bottom edge."}),
            },
        }

    RETURN_TYPES = ("IMAGE", "BBOX",)
    RETURN_NAMES = ("image", "bbox",)
    FUNCTION = "crop"
    # True so the node runs on queue even with nothing wired downstream --
    # otherwise there would be no way to get a first preview to aim at.
    OUTPUT_NODE = True
    CATEGORY = "KDNodes/image"

    def _write_preview(self, images):
        """
        Write the proxy PNG and return the payload the frontend needs to point
        an <img> at it through ComfyUI's native /view route.
        """
        arr = _preview_uint8(images)

        full_output_folder, filename, counter, subfolder, _ = \
            folder_paths.get_save_image_path("CropKD" + self.prefix_append,
                                             self.output_dir)
        file = f"{filename}_{counter:05}_.png"
        out_path = os.path.join(full_output_folder, file)

        # compress_level 1: a throwaway proxy, rewritten whenever the input
        # changes, so encode speed matters and file size does not.
        Image.fromarray(arr).save(out_path, compress_level=1)

        # width/height are the SOURCE dimensions, not the proxy's -- the crop
        # widgets are in source pixels and the frontend needs the real extent
        # to clamp against.  preview_* is only used to size the element.
        # "_path" is for the cleanup below; the frontend ignores it.
        return {"filename": file, "subfolder": subfolder, "type": self.type,
                "width": int(images.shape[2]), "height": int(images.shape[1]),
                "preview_width": int(arr.shape[1]),
                "preview_height": int(arr.shape[0]),
                "_path": out_path}

    def _discard_proxy(self, preview):
        """Remove a superseded proxy so only the current one is left in temp."""
        path = (preview or {}).get("_path")
        if not path:
            return
        try:
            os.remove(path)
        except OSError:
            # Still held open by an <img>, or already swept -- harmless either way.
            pass

    def crop(self, image, x, y, width, height):
        if image.shape[0] == 0:
            raise ValueError("CropImageKD: input batch is empty")

        src_h, src_w = int(image.shape[1]), int(image.shape[2])

        # ComfyUI's cache is per node and all-or-nothing: nudging the box
        # changes this node's cache key, so the whole method re-runs, PNG
        # encode included.  Comparing tensor identity skips rewriting a proxy
        # of an image that did not change.  Holding the reference in
        # self._cache keeps the tensor alive, so its id cannot be recycled and
        # `is` stays an exact test.
        if self._cache is not None and self._cache[0] is image:
            preview = self._cache[1]
        else:
            preview = self._write_preview(image)
            self._discard_proxy(self._cache[1] if self._cache else None)
            self._cache = (image, preview)

        cx, cy, cw, ch = clamp_rect(x, y, width, height, src_w, src_h)

        # A slice, not a copy: contiguity is not required downstream and the
        # view keeps a 4K batch from being duplicated for no reason.
        cropped = image[:, cy:cy + ch, cx:cx + cw, :]

        return {
            "ui": {"kd_crop": [preview] if preview else []},
            # KJNodes-style BBOX: a list of (x, y, width, height) tuples, so a
            # single crop is a one-item list.  Feeds BboxToInt, ImageCropByBbox
            # and the rest of that family directly.
            "result": (cropped, [(cx, cy, cw, ch)],),
        }
