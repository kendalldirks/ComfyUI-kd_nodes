import os.path
import json, re
import numpy as np
import folder_paths
from PIL import Image
from PIL.PngImagePlugin import PngInfo

def build_png_metadata(prompt=None, extra_pnginfo=None):
    metadata = PngInfo()
    if prompt is not None:
        metadata.add_text("prompt", json.dumps(prompt))
    if extra_pnginfo is not None:
        for x in extra_pnginfo:
            metadata.add_text(x, json.dumps(extra_pnginfo[x]))
    return metadata

def get_versioned_subfolder_name(parent_dir, subfolder_name):
    if not os.path.exists(parent_dir):
        return f"{subfolder_name}1"

    existing = os.listdir(parent_dir)
    pattern = re.compile(rf"^{re.escape(subfolder_name)}(\d+)?$")

    max_version = 0

    for name in existing:
        if not os.path.isdir(os.path.join(parent_dir, name)):
            continue
        match = pattern.match(name)
        if match:
            if match.group(1) is None:
                max_version = max(max_version, 1)
            else:
                max_version = max(max_version, int(match.group(1)))

    return f"{subfolder_name}{max_version + 1}"

class SaveImageKD:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "images": ("IMAGE", {"tooltip": "The images to save."}),
                "save_path":               ("STRING",  {"default": ""}),
                "filename_prefix":         ("STRING",  {"default": "image"}),
                "sequence_start_index":    ("INT",     {"default": 1,    "min": 0, "max": 99999}),
                "zero_padding":            ("INT",     {"default": 4,    "min": 0, "max": 9}),
                # --- gap inserted here by JS ---
                "create_subfolder":        ("BOOLEAN", {"default": False}),
                "subfolder_name":          ("STRING",  {"default": "subfolder"}),
                "auto_version_subfolder":  ("BOOLEAN", {"default": False}),
                # --- gap inserted here by JS ---
                "compression_level":       ("INT",     {"default": 6,    "min": 0, "max": 9}),
                "embed_workflow":          ("BOOLEAN", {"default": True}),
            },
            "hidden": {
                "prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("filename",)
    FUNCTION = "save_images"

    OUTPUT_NODE = True

    CATEGORY = "KDNodes/image"
    DESCRIPTION = "Saves the input images to the specified save path."


    def save_images(self,images, save_path, filename_prefix, sequence_start_index, zero_padding, create_subfolder, subfolder_name, auto_version_subfolder, compression_level, embed_workflow, prompt=None, extra_pnginfo=None):

        if not os.path.isabs(save_path):
            save_path = os.path.join(folder_paths.get_output_directory(), save_path)

        if create_subfolder:
            subfolder_name = subfolder_name.strip().strip("/\\")
            if auto_version_subfolder:
                subfolder_name = get_versioned_subfolder_name(os.path.abspath(save_path), subfolder_name)
            final_save_path = os.path.join(save_path, subfolder_name)
        else:
            final_save_path = save_path

        final_save_path = os.path.normpath(final_save_path)
        os.makedirs(final_save_path, exist_ok=True)


        counter = sequence_start_index

        for image in images:
            i = 255. * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))

            filename = f"{filename_prefix}{counter:0{zero_padding}d}"
            filename = f"{filename}.png"

            if embed_workflow:
                metadata = build_png_metadata(prompt, extra_pnginfo)
            else:
                metadata = None

            img.save(os.path.join(final_save_path, filename), pnginfo=metadata, compress_level=compression_level)

            counter += 1

        return filename,