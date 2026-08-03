import os, json, re, torch, hashlib, subprocess, time, shutil

import numpy as np
from PIL import Image, ImageColor, ImageSequence, ImageOps
from PIL.PngImagePlugin import PngInfo
import torchvision.transforms.functional as TF

from aiohttp import web
from server import PromptServer

from comfy.cli_args import args
import folder_paths
import node_helpers

from nodes import SaveImage
import random

from .utils import frames_to_uint8



def tensor2mask(t: torch.Tensor) -> torch.Tensor:
    size = t.size()
    if (len(size) < 4):
        return t
    if size[3] == 1:
        return t[:,:,:,0]
    elif size[3] == 4:
        # Not sure what the right thing to do here is. Going to try to be a little smart and use alpha unless all alpha is 1 in case we'll fallback to RGB behavior
        if torch.min(t[:, :, :, 3]).item() != 1.:
            return t[:,:,:,3]

    return TF.rgb_to_grayscale(tensor2rgb(t).permute(0,3,1,2), num_output_channels=1)[:,0,:,:]

def tensor2rgb(t: torch.Tensor) -> torch.Tensor:
    size = t.size()
    if (len(size) < 4):
        return t.unsqueeze(3).repeat(1, 1, 1, 3)
    if size[3] == 1:
        return t.repeat(1, 1, 1, 3)
    elif size[3] == 4:
        return t[:, :, :, :3]
    else:
        return t


class MattePreview:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "color": ("STRING", {"default": "#FF0000"}),
                "opacity": ("FLOAT", {"default": 0.75, "min": 0.0, "max": 1.0, "step": 0.01}),
                "invert": ("BOOLEAN", {"default": False}),
                "mask": ("MASK",),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "mix"

    CATEGORY = "KDNodes/image"
    DESCRIPTION = "Overlays a mask over an image for quick visualization."

    def mix(self, image, color, opacity, invert, mask):
        r, g, b = ImageColor.getrgb(color)

        # Built straight onto the image's device and dtype.  Creating it on the
        # CPU in float32 and moving afterwards would silently promote a
        # half-precision image to float32 for the entire blend.
        rgb = torch.tensor([r / 255.0, g / 255.0, b / 255.0],
                           device=image.device, dtype=image.dtype)

        m = tensor2mask(mask)
        if tuple(m.shape[-2:]) != tuple(image.shape[1:3]):
            raise ValueError(
                f"MattePreview: mask is {tuple(m.shape[-2:])} but the image is "
                f"{tuple(image.shape[1:3])}; they have to match.")

        # opacity and the optional invert folded into one multiply-add:
        #   invert off -> m * opacity
        #   invert on  -> (1 - m) * opacity  ==  m * -opacity + opacity
        # mul() hands back a buffer we own, so add_ and clamp_ can then run in
        # place without reaching into the caller's mask.
        opacity = float(opacity)
        scale = -opacity if invert else opacity
        bias = opacity if invert else 0.0
        m = m.to(device=image.device, dtype=image.dtype).unsqueeze(-1)
        m = m.mul(scale).add_(bias).clamp_(0.0, 1.0)

        # lerp(a, b, w) == a + w * (b - a), which is the same result as
        # a * (1 - w) + b * w but in one fused kernel writing one buffer
        # instead of four.  `rgb` broadcasts from (3,) and `m` from (..., 1),
        # so neither the colour nor the mask is ever materialised at full size
        # -- the old version allocated a complete image-sized copy of each.
        out = torch.lerp(image[..., :3], rgb, m)

        # An alpha channel rides along untouched instead of crashing: the old
        # path routed RGBA into tensor2rgba(), which does not exist.
        if image.shape[-1] > 3:
            out = torch.cat((out, image[..., 3:]), dim=-1)

        return (out,)

class ImageRebatchOverlap:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "batch_size": ("INT", {"default": 1, "min": 1, "max": 4096}),
                "overlap": ("INT", {"default": 0, "min": 0, "max": 4095}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "rebatch"
    CATEGORY = "KDNodes/image"

    # Make ComfyUI pass list inputs when upstream produces lists
    INPUT_IS_LIST = True
    # Tell ComfyUI we are returning a list for the first (and only) output
    OUTPUT_IS_LIST = (True,)

    def rebatch(self, images, batch_size, overlap):
        # With INPUT_IS_LIST=True, scalar inputs arrive as 1-item lists
        batch_size = int(batch_size[0])
        overlap = int(overlap[0])

        if overlap >= batch_size:
            raise ValueError(f"overlap ({overlap}) must be < batch_size ({batch_size}).")

        step = batch_size - overlap

        # images is a list of batch tensors: each (B,H,W,C)
        all_images = []
        for img in images:
            for i in range(img.shape[0]):
                all_images.append(img[i:i+1])  # keep batch dim => (1,H,W,C)

        output_list = []
        n = len(all_images)

        start = 0
        while start < n:
            window = all_images[start:start + batch_size]
            if not window:
                break
            output_list.append(torch.cat(window, dim=0))  # (batch,H,W,C) (or shorter at end)
            start += step

        return (output_list,)

class UnbatchImagesOverlapBlend:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "batches": ("IMAGE",),
                "overlap": ("INT", {"default": 0, "min": 0, "max": 4095}),
                "transition": (["linear", "center cut", "ease in", "ease out"], {"default": "linear"}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "unbatch_blend"
    CATEGORY = "KDNodes/image"
    INPUT_IS_LIST = True

    @staticmethod
    def _alpha_linear(j: int, k: int) -> float:
        if k <= 1:
            return 0.5
        return j / (k - 1)

    @staticmethod
    def _alpha_center_cut(j: int, k: int) -> float:
        """
        Hard cut in the middle of the overlap.
        First half uses previous, second half uses next.
        """
        if k <= 1:
            return 0.5
        cut = (k - 1) / 2.0
        return 0.0 if j < cut else 1.0

    @staticmethod
    def _alpha_ease_in(j: int, k: int) -> float:
        """
        Slow start, faster toward the end (quadratic ease-in).
        """
        if k <= 1:
            return 0.5
        t = j / (k - 1)
        return t * t

    @staticmethod
    def _alpha_ease_out(j: int, k: int) -> float:
        """
        Fast start, slower toward the end (quadratic ease-out).
        """
        if k <= 1:
            return 0.5
        t = j / (k - 1)
        return 1.0 - (1.0 - t) * (1.0 - t)

    @classmethod
    def _get_alpha_fn(cls, transition: str):
        if transition == "linear":
            return cls._alpha_linear
        if transition == "center cut":
            return cls._alpha_center_cut
        if transition == "ease in":
            return cls._alpha_ease_in
        if transition == "ease out":
            return cls._alpha_ease_out
        return cls._alpha_linear

    @staticmethod
    def _blend_overlap(out_frames: list, next_frames: list, k: int, alpha_fn) -> None:
        """
        Blends last k frames of out_frames with first k frames of next_frames.
        Modifies out_frames in-place.
        """
        if k <= 0:
            return

        start_idx = len(out_frames) - k
        for j in range(k):
            alpha = float(alpha_fn(j, k))
            prev_f = out_frames[start_idx + j]
            next_f = next_frames[j]
            out_frames[start_idx + j] = prev_f * (1.0 - alpha) + next_f * alpha

    def unbatch_blend(self, batches, overlap, transition):
        # INPUT_IS_LIST=True => scalars/strings come in as 1-item lists
        overlap = int(overlap[0])
        transition = transition[0] if isinstance(transition, list) else transition

        if len(batches) == 0:
            return (torch.empty((0, 0, 0, 3), device="cpu"),)

        # no overlap => straight concat
        if overlap == 0:
            return (torch.cat(batches, dim=0),)

        alpha_fn = self._get_alpha_fn(transition)

        # Start with frames from the first batch
        out_frames = []
        first = batches[0]
        for i in range(first.shape[0]):
            out_frames.append(first[i:i+1])

        # Stitch the rest
        for b in batches[1:]:
            next_frames = [b[i:i+1] for i in range(b.shape[0])]

            k = min(overlap, len(out_frames), len(next_frames))
            if k > 0:
                self._blend_overlap(out_frames, next_frames, k, alpha_fn)
                out_frames.extend(next_frames[k:])  # append non-overlap tail
            else:
                out_frames.extend(next_frames)

        return (torch.cat(out_frames, dim=0),)

class LoadImageKD:
    @classmethod
    def INPUT_TYPES(s):
        input_dir = folder_paths.get_input_directory()
        files = [f for f in os.listdir(input_dir) if os.path.isfile(os.path.join(input_dir, f))]
        files = folder_paths.filter_files_content_types(files, ["image"])
        return {"required":
                    {"image": (sorted(files), {"image_upload": True})},
                }

    CATEGORY = "image"

    RETURN_TYPES = ("IMAGE", "MASK", "STRING")
    RETURN_NAMES = ("image", "mask", "imagepath")
    FUNCTION = "load_image"

    CATEGORY = "KDNodes/image"
    DESCRIPTION = "Loads an image with it's filepath"

    def load_image(self, image):
        image_path = folder_paths.get_annotated_filepath(image)

        img = node_helpers.pillow(Image.open, image_path)

        output_images = []
        output_masks = []
        w, h = None, None

        excluded_formats = ['MPO']

        for i in ImageSequence.Iterator(img):
            i = node_helpers.pillow(ImageOps.exif_transpose, i)

            if i.mode == 'I':
                i = i.point(lambda i: i * (1 / 255))
            image = i.convert("RGB")

            if len(output_images) == 0:
                w = image.size[0]
                h = image.size[1]

            if image.size[0] != w or image.size[1] != h:
                continue

            image = np.array(image).astype(np.float32) / 255.0
            image = torch.from_numpy(image)[None,]
            if 'A' in i.getbands():
                mask = np.array(i.getchannel('A')).astype(np.float32) / 255.0
                mask = 1. - torch.from_numpy(mask)
            elif i.mode == 'P' and 'transparency' in i.info:
                mask = np.array(i.convert('RGBA').getchannel('A')).astype(np.float32) / 255.0
                mask = 1. - torch.from_numpy(mask)
            else:
                mask = torch.zeros((64,64), dtype=torch.float32, device="cpu")
            output_images.append(image)
            output_masks.append(mask.unsqueeze(0))

        if len(output_images) > 1 and img.format not in excluded_formats:
            output_image = torch.cat(output_images, dim=0)
            output_mask = torch.cat(output_masks, dim=0)
        else:
            output_image = output_images[0]
            output_mask = output_masks[0]

        return (output_image, output_mask, image_path)

    @classmethod
    def IS_CHANGED(s, image):
        image_path = folder_paths.get_annotated_filepath(image)
        m = hashlib.sha256()
        with open(image_path, 'rb') as f:
            m.update(f.read())
        return m.digest().hex()

    @classmethod
    def VALIDATE_INPUTS(s, image):
        if not folder_paths.exists_annotated_filepath(image):
            return "Invalid image file: {}".format(image)

        return True

def images_generator(directory: str, image_load_cap: int = 0, skip_first_images: int = 0, select_every_nth: int = 1):
    if not os.path.isdir(directory):
        raise FileNotFoundError(f"Directory '{directory}' cannot be found.")

    dir_files = get_sorted_dir_files_from_directory(
        directory,
        skip_first_images,
        select_every_nth,
        FolderOfImages.IMG_EXTENSIONS
    )

    if len(dir_files) == 0:
        raise FileNotFoundError(f"No files in directory '{directory}'.")

    if image_load_cap > 0:
        dir_files = dir_files[:image_load_cap]

    first_image = Image.open(dir_files[0])
    first_image = ImageOps.exif_transpose(first_image)

    width, height = first_image.size
    has_alpha = "A" in first_image.getbands()
    iformat = "RGBA" if has_alpha else "RGB"

    yield width, height, has_alpha

    def load_image(file_path):
        i = Image.open(file_path)
        i = ImageOps.exif_transpose(i)
        i = i.convert(iformat)
        i = np.array(i, dtype=np.float32)

        # Normalize in-place through shared memory
        torch.from_numpy(i).div_(255)

        if i.shape[0] != height or i.shape[1] != width:
            i = torch.from_numpy(i).movedim(-1, 0).unsqueeze(0)
            i = common_upscale(i, width, height, "lanczos", "center")
            i = i.squeeze(0).movedim(0, -1).numpy()

        if has_alpha:
            i[:, :, -1] = 1 - i[:, :, -1]

        return i

    total_images = len(dir_files)
    processed_images = 0
    pbar = ProgressBar(total_images)

    prev_image = None
    images = map(load_image, dir_files)

    try:
        prev_image = next(images)
        while True:
            next_image = next(images)
            yield prev_image
            processed_images += 1
            pbar.update_absolute(processed_images, total_images)
            prev_image = next_image
    except StopIteration:
        pass

    if prev_image is not None:
        yield prev_image

def load_images(directory: str, image_load_cap: int = 0, skip_first_images: int = 0, select_every_nth: int = 1):
    dir_files = get_sorted_dir_files_from_directory(directory, skip_first_images, select_every_nth, FolderOfImages.IMG_EXTENSIONS)

    if image_load_cap > 0:
        dir_files = dir_files[:image_load_cap]

    file_paths = list(dir_files)

    gen = images_generator(directory, image_load_cap, skip_first_images, select_every_nth)

    width, height, has_alpha = next(gen)
    channels = 4 if has_alpha else 3

    images = torch.from_numpy(
        np.fromiter(
            gen,
            np.dtype((np.float32, (height, width, channels)))
        )
    )

    if has_alpha:
        masks = images[:, :, :, 3]
        images = images[:, :, :, :3]
    else:
        masks = torch.zeros((images.size(0), 64, 64), dtype=torch.float32, device="cpu")

    if len(images) == 0:
        raise FileNotFoundError(f"No images could be loaded from directory '{directory}'.")

    return images, masks, images.size(0), file_paths


class LoadImagesPathKD:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "directory": ("STRING", {"placeholder": "X://path/to/images", "vhs_path_extensions": []}),
            },
            "optional": {
                "image_load_cap": ("INT", {"default": 0, "min": 0, "step": 1}),
                "start_index": ("INT", {"default": 0, "min": 0, "step": 1}),
                "select_every_nth": ("INT", {"default": 1, "min": 1, "step": 1}),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK", "INT", "STRING")
    RETURN_NAMES = ("IMAGE", "MASK", "frame_count", "image_path")
    FUNCTION = "load_images"

    CATEGORY = "KDNodes/image"

    def load_images(self, directory: str, **kwargs):
        directory = strip_path(directory)
        if directory is None or validate_load_images(directory) != True:
            raise Exception("directory is not valid: " + directory)

        return load_images(directory, **kwargs)

    @classmethod
    def IS_CHANGED(s, directory: str, **kwargs):
        if directory is None:
            return "input"
        return is_changed_load_images(directory, **kwargs)

    @classmethod
    def VALIDATE_INPUTS(s, directory: str, **kwargs):
        if directory is None:
            return True
        return validate_load_images(strip_path(directory))


# --- PreviewImageKD's Save button -------------------------------------------
#
# The preview PNG on disk already carries the prompt and workflow in its text
# chunks, but the clipboard cannot carry them out: browsers decode and re-encode
# images written through the Async Clipboard API, which strips the metadata.
# Copying the file instead keeps it intact.

@PromptServer.instance.routes.post("/kd_nodes/save_preview")
async def _kd_save_preview(request):
    """
    Copy a preview image out of ComfyUI's temp area to a path the user typed
    into the node.  The frontend falls back to an ordinary browser download
    whenever this fails, so an unusable path just means "download instead"
    rather than being an error worth surfacing loudly.
    """
    from .save_video_kd import get_versioned_filename

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "malformed request"}, status=400)

    filename = os.path.basename(data.get("filename") or "")
    subfolder = data.get("subfolder") or ""
    ftype = data.get("type") or "temp"
    dest = (data.get("path") or "").strip().strip('"')

    if not filename or not dest:
        return web.json_response({"error": "no filename or path"}, status=400)

    # Resolve the source inside ComfyUI's own directories, refusing anything
    # that tries to climb out of them via the subfolder.
    base = folder_paths.get_directory_by_type(ftype)
    if not base:
        return web.json_response({"error": f"unknown type '{ftype}'"}, status=400)
    base = os.path.abspath(base)
    src_dir = os.path.abspath(os.path.join(base, subfolder))
    try:
        contained = os.path.commonpath([base, src_dir]) == base
    except ValueError:                       # different drives on Windows
        contained = False
    if not contained:
        return web.json_response({"error": "invalid subfolder"}, status=400)

    src = os.path.join(src_dir, filename)
    if not os.path.isfile(src):
        return web.json_response({"error": "source image not found"}, status=404)

    # A folder or a full file path are both accepted; anything without an
    # extension is treated as a folder.
    dest = os.path.expanduser(os.path.expandvars(dest))
    if os.path.isdir(dest) or not os.path.splitext(dest)[1]:
        out_dir, out_name = dest, filename
    else:
        out_dir, out_name = os.path.dirname(dest) or ".", os.path.basename(dest)

    try:
        os.makedirs(out_dir, exist_ok=True)
        stem, ext = os.path.splitext(out_name)
        target = os.path.join(
            out_dir, get_versioned_filename(out_dir, stem, ext.lstrip(".") or "png"))
        shutil.copy2(src, target)          # copy2, so the file arrives byte-identical
    except (OSError, ValueError) as e:
        return web.json_response({"error": str(e)}, status=400)

    return web.json_response({"saved": target})


class PreviewImageKD(SaveImage):
    def __init__(self):
        self.output_dir = folder_paths.get_temp_directory()
        self.type = "temp"
        self.prefix_append = "_temp_" + ''.join(random.choice("abcdefghijklmnopqrstupvxyz") for x in range(5))
        self.compress_level = 1

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {"images": ("IMAGE",)},
            "optional": {
                "save_path": ("STRING", {"default": "", "multiline": False,
                                         "tooltip": "Folder, or a full file path, for the Save "
                                                    "button. Leave blank — or give a path that "
                                                    "can't be written — to fall back to a normal "
                                                    "browser download."}),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "save_images"
    OUTPUT_NODE = True
    CATEGORY = "KDNodes/image"

    def save_images(self, images, save_path="", prompt=None, extra_pnginfo=None):
        # `save_path` is read by the Save button in the frontend; there is
        # nothing to do with it at execution time. Declared here so ComfyUI
        # serialises it, and so SaveImage.save_images is not handed an
        # argument it cannot take.
        out = super().save_images(images, "ComfyUI", prompt, extra_pnginfo)

        # SaveImage returns just {"ui": {...}}. Adding "result" turns this into
        # a passthrough as well as a preview: the tensor goes straight back out
        # untouched, so chaining it mid-graph costs nothing but the preview it
        # was already writing. OUTPUT_NODE stays True so it still runs with
        # nothing connected downstream.
        out["result"] = (images,)
        return out

# Set True to print per-stage timings for PreviewAnimationKD to the console.
PREVIEW_PROFILE = False


def _preview_log(name, seconds, nbytes=None):
    detail = f"{nbytes / 1e6:9.1f} MB" if nbytes else ""
    print(f"[PreviewAnimationKD] {name:<30} {seconds:7.3f}s  {detail}")


class PreviewAnimationKD:
    def __init__(self):
        self.output_dir = folder_paths.get_temp_directory()
        self.type = "temp"
        self.prefix_append = "_temp_" + ''.join(random.choice("abcdefghijklmnopqrstupvxyz") for x in range(5))

    @classmethod
    def INPUT_TYPES(s):
        return {"required":
                    {
                     "fps": ("FLOAT", {"default": 24.0, "min": 0.01, "max": 1000.0, "step": 0.01}),
                     "crf": ("INT", {"default": 18, "min": 0, "max": 51,
                                     "tooltip": "H.264 quality: lower = better/larger. "
                                                "0 = lossless, ~18 visually lossless, 23 = default."}),
                     "preset": (["ultrafast", "superfast", "veryfast", "faster", "fast", "medium"],
                                {"default": "veryfast"}),
                     "max_preview_size": ("INT", {"default": 1024, "min": 0, "max": 8192, "step": 8,
                                                   "tooltip": "Downscale the preview so its longest edge "
                                                              "doesn't exceed this (keeps aspect ratio, never upscales). "
                                                              "0 = full resolution, which is much slower: a "
                                                              "289-frame 4K batch takes ~15s at 0 vs ~2s at 1024."}),
                     },
                "optional": {
                    "images": ("IMAGE", ),
                    "masks": ("MASK", ),
                    "passthrough": ("*", {}),
                },
            }

    RETURN_TYPES = ("*",)
    RETURN_NAMES = ("passthrough",)
    FUNCTION = "preview"
    OUTPUT_NODE = True
    CATEGORY = "KDNodes/video"

    def preview(self, fps, crf, preset, max_preview_size=0, images=None, masks=None, passthrough=None):
        from .load_video_kd import FFMPEG_PATH

        t_total = time.perf_counter()
        if PREVIEW_PROFILE:
            src = images if images is not None else masks
            if src is not None:
                print(f"[PreviewAnimationKD] input {tuple(src.shape)} = "
                      f"{src.numel() * src.element_size() / 1e6:.0f} MB, "
                      f"max_preview_size={max_preview_size}")
            t = time.perf_counter()

        # Downscales and converts in chunks, and hands back frames already at
        # their final even dimensions so ffmpeg needs no scale/pad filter.
        frames = frames_to_uint8(images, masks, target_size=max_preview_size)

        if PREVIEW_PROFILE and frames is not None:
            _preview_log("resize + uint8 (chunked)", time.perf_counter() - t,
                         frames.nbytes)
            print(f"[PreviewAnimationKD]   -> {frames.shape[2]}x{frames.shape[1]}"
                  f" x {frames.shape[0]} frames")

        if frames is None or frames.shape[0] == 0:
            print("PreviewAnimationKD: No images or masks provided")
            return {"ui": {"kd_video": []}, "result": (passthrough,)}
        if FFMPEG_PATH is None:
            print("PreviewAnimationKD: ffmpeg not found, cannot build preview")
            return {"ui": {"kd_video": []}, "result": (passthrough,)}

        num_frames, H, W = frames.shape[0], frames.shape[1], frames.shape[2]

        full_output_folder, filename, counter, subfolder, filename_prefix = \
            folder_paths.get_save_image_path("AnimPreview" + self.prefix_append, self.output_dir)
        file = f"{filename}_{counter:05}_.mp4"
        out_path = os.path.join(full_output_folder, file)

        args = [
            FFMPEG_PATH, "-v", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", str(preset), "-crf", str(crf),
            "-movflags", "+faststart", out_path,
        ]
        if PREVIEW_PROFILE:
            t = time.perf_counter()
        proc = subprocess.Popen(args, stdin=subprocess.PIPE)
        # Contiguous already, so hand ffmpeg the buffer rather than paying for
        # a full tobytes() duplicate.
        proc.stdin.write(frames.reshape(-1).data)
        proc.stdin.close()
        if PREVIEW_PROFILE:
            # Writing to a pipe blocks whenever ffmpeg can't keep up, so this
            # figure includes ffmpeg's encoding work, not just the copy.
            _preview_log("pipe write to ffmpeg", time.perf_counter() - t,
                         frames.nbytes)
            t = time.perf_counter()
        proc.wait()
        if PREVIEW_PROFILE:
            _preview_log("ffmpeg drain/finish", time.perf_counter() - t)
            _preview_log("TOTAL preview build", time.perf_counter() - t_total,
                         os.path.getsize(out_path) if os.path.isfile(out_path) else 0)

        # Served through ComfyUI's native /view route (no re-transcode) for full quality.
        preview = {"filename": file, "subfolder": subfolder, "type": self.type,
                   "format": "video/mp4", "frames": num_frames, "fps": float(fps),
                   "width": W, "height": H}
        return {"ui": {"kd_video": [preview]}, "result": (passthrough,)}