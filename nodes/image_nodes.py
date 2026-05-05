import os, json, re, torch, hashlib

import numpy as np
from PIL import Image, ImageColor, ImageSequence, ImageOps
from PIL.PngImagePlugin import PngInfo
import torchvision.transforms.functional as TF

from comfy.cli_args import args
import folder_paths
import node_helpers

from nodes import SaveImage
import random



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

def tensor2batch(t: torch.Tensor, bs: torch.Size) -> torch.Tensor:
    if len(t.size()) < len(bs):
        t = t.unsqueeze(3)
    if t.size()[0] < bs[0]:
        t.repeat(bs[0], 1, 1, 1)
    dim = bs[3]
    if dim == 1:
        return tensor2mask(t)
    elif dim == 3:
        return tensor2rgb(t)
    elif dim == 4:
        return tensor2rgba(t)

def tensors2common(t1: torch.Tensor, t2: torch.Tensor) -> (torch.Tensor, torch.Tensor):
    t1s = t1.size()
    t2s = t2.size()
    if len(t1s) < len(t2s):
        t1 = t1.unsqueeze(3)
    elif len(t1s) > len(t2s):
        t2 = t2.unsqueeze(3)

    if len(t1.size()) == 3:
        if t1s[0] < t2s[0]:
            t1 = t1.repeat(t2s[0], 1, 1)
        elif t1s[0] > t2s[0]:
            t2 = t2.repeat(t1s[0], 1, 1)
    else:
        if t1s[0] < t2s[0]:
            t1 = t1.repeat(t2s[0], 1, 1, 1)
        elif t1s[0] > t2s[0]:
            t2 = t2.repeat(t1s[0], 1, 1, 1)

    t1s = t1.size()
    t2s = t2.size()
    if len(t1s) > 3 and t1s[3] < t2s[3]:
        return tensor2batch(t1, t2s), t2
    elif len(t1s) > 3 and t1s[3] > t2s[3]:
        return t1, tensor2batch(t2, t1s)
    else:
        return t1, t2


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
        r, g, b = r / 255., g / 255., b / 255.
        image_size = image.size()
        image2 = torch.tensor([r, g, b]).to(device=image.device).unsqueeze(0).unsqueeze(0).unsqueeze(0).repeat(image_size[0], image_size[1], image_size[2], 1)
        image, image2 = tensors2common(image, image2)
        mask.reshape((-1, 1, mask.shape[-2], mask.shape[-1])).movedim(1, -1).expand(-1, -1, -1, 3)
        mask = tensor2batch(tensor2mask(mask), image.size())

        if invert:
            mask = 1.0 - mask

        mask = (mask * float(opacity)).clamp(0.0, 1.0)

        return (image * (1. - mask) + image2 * mask,)

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
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = ()
    FUNCTION = "save_images"
    OUTPUT_NODE = True
    CATEGORY = "KDNodes/image"

class PreviewAnimationKD:
    def __init__(self):
        self.output_dir = folder_paths.get_temp_directory()
        self.type = "temp"
        self.prefix_append = "_temp_" + ''.join(random.choice("abcdefghijklmnopqrstupvxyz") for x in range(5))
        self.compress_level = 1

    methods = {"default": 4, "fastest": 0, "slowest": 6}
    @classmethod
    def INPUT_TYPES(s):
        return {"required":
                    {
                     "fps": ("FLOAT", {"default": 8.0, "min": 0.01, "max": 1000.0, "step": 0.01}),
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
    CATEGORY = "KDNodes/image"

    def preview(self, fps, images=None, masks=None, passthrough=None):
        filename_prefix = "AnimPreview"
        full_output_folder, filename, counter, subfolder, filename_prefix = folder_paths.get_save_image_path(filename_prefix, self.output_dir)
        results = list()

        pil_images = []

        if images is not None and masks is not None:
            for image in images:
                i = 255. * image.cpu().numpy()
                img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))
                pil_images.append(img)
            for mask in masks:
                if pil_images:
                    mask_np = mask.cpu().numpy()
                    mask_np = np.clip(mask_np * 255, 0, 255).astype(np.uint8)
                    mask_img = Image.fromarray(mask_np, mode='L')
                    img = pil_images.pop(0)
                    img = img.convert("RGBA")
                    rgba_mask_img = Image.new("RGBA", img.size, (255, 255, 255, 255))
                    rgba_mask_img.putalpha(mask_img)
                    composited_img = Image.alpha_composite(img, rgba_mask_img)
                    pil_images.append(composited_img)

        elif images is not None and masks is None:
            for image in images:
                i = 255. * image.cpu().numpy()
                img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))
                pil_images.append(img)

        elif masks is not None and images is None:
            for mask in masks:
                mask_np = 255. * mask.cpu().numpy()
                mask_img = Image.fromarray(np.clip(mask_np, 0, 255).astype(np.uint8))
                pil_images.append(mask_img)
        else:
            print("PreviewAnimation: No images or masks provided")
            return {"ui": {"images": results, "animated": (None,), "text": "empty"}, "result": (passthrough,)}

        num_frames = len(pil_images)

        c = len(pil_images)
        for i in range(0, c, num_frames):
            file = f"{filename}_{counter:05}_.webp"
            pil_images[i].save(os.path.join(full_output_folder, file), save_all=True, duration=int(1000.0/fps), append_images=pil_images[i + 1:i + num_frames], lossless=False, quality=50, method=0)
            results.append({
                "filename": file,
                "subfolder": subfolder,
                "type": self.type
            })
            counter += 1

        animated = num_frames != 1
        return {"ui": {"images": results, "animated": (animated,), "text": [f"{num_frames}x{pil_images[0].size[0]}x{pil_images[0].size[1]}"]}, "result": (passthrough,)}