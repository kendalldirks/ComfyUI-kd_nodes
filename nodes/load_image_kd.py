import os
import numpy as np
import torch
from PIL import Image, ImageOps
from comfy.utils import common_upscale, ProgressBar

IMG_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif"}


def get_image_files(directory: str, skip_first_images: int = 0, select_every_nth: int = 1, image_load_cap: int = 0) -> list[str]:
    if not os.path.isdir(directory):
        raise FileNotFoundError(f"Directory not found: {directory}")
    files = sorted([
        os.path.join(directory, f) for f in os.listdir(directory)
        if os.path.splitext(f)[1].lower() in IMG_EXTENSIONS
    ])
    if not files:
        raise FileNotFoundError(f"No images found in: {directory}")
    files = files[skip_first_images::select_every_nth]
    if image_load_cap > 0:
        files = files[:image_load_cap]
    if not files:
        raise FileNotFoundError(f"No images remain after skip/select filters in: {directory}")
    return files


def scan_image_files(file_paths: list[str]) -> tuple[tuple[int, int], bool]:
    sizes = {}
    has_alpha = False
    for p in file_paths:
        i = ImageOps.exif_transpose(Image.open(p))
        has_alpha |= 'A' in i.getbands()
        sizes[i.size] = sizes.get(i.size, 0) + 1
    return max(sizes, key=sizes.get), has_alpha


def load_image_batch(file_paths: list[str], dominant_size: tuple[int, int], has_alpha: bool) -> tuple[torch.Tensor, torch.Tensor]:
    w, h = dominant_size
    iformat = "RGBA" if has_alpha else "RGB"
    images = []
    pbar = ProgressBar(len(file_paths))
    for idx, p in enumerate(file_paths):
        i = ImageOps.exif_transpose(Image.open(p)).convert(iformat)
        i = np.array(i, dtype=np.float32) / 255.0
        if i.shape[1] != w or i.shape[0] != h:
            t = torch.from_numpy(i).movedim(-1, 0).unsqueeze(0)
            t = common_upscale(t, w, h, "lanczos", "center")
            i = t.squeeze(0).movedim(0, -1).numpy()
        if has_alpha:
            i[:, :, -1] = 1.0 - i[:, :, -1]
        images.append(i)
        pbar.update_absolute(idx + 1, len(file_paths))
    batch = torch.from_numpy(np.stack(images))
    if has_alpha:
        masks = batch[:, :, :, 3]
        batch = batch[:, :, :, :3]
    else:
        masks = torch.zeros((batch.size(0), 64, 64), dtype=torch.float32)
    return batch, masks


class LoadImagesPathKD:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "path": ("STRING", {"default": ""}),
                "image_load_cap": ("INT", {"default": 0, "min": 0, "max": 99999, "step": 1}),
                "start_index": ("INT", {"default": 0, "min": 0, "max": 99999, "step": 1}),
                "select_every_nth": ("INT", {"default": 1, "min": 1, "max": 99999, "step": 1}),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK", "INT", "STRING")
    RETURN_NAMES = ("image", "mask", "frame_count", "image_path")
    FUNCTION = "load_images"
    CATEGORY = "KDNodes/image"
    DESCRIPTION = "Loads an image sequence from a specified directory."

    def load_images(self, path, image_load_cap, start_index, select_every_nth):

        file_paths = get_image_files(path.strip(), start_index, select_every_nth, image_load_cap)

        dominant_size, has_alpha = scan_image_files(file_paths)
        images, masks = load_image_batch(file_paths, dominant_size, has_alpha)

        return (images, masks, images.size(0), file_paths)