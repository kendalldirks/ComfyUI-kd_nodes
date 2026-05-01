class SaveImageKD:
    def __init__(self):
        self.type = "output"
        self.prefix_append = ""
        self.compress_level = 4

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "images": ("IMAGE", {"tooltip": "The images to save."}),
                "filename_prefix": ("STRING", {"default": "ComfyUI", "tooltip": "The prefix for the file to save. This may include formatting information such as %date:yyyy-MM-dd% or %Empty Latent Image.width% to include values from nodes."}),
                "filename_separator": ("STRING", {"default": "_","tooltip": "The separator between the filename prefix and the sequence number"}),
                "sequence_start": ("INT", {"default": 1, "step": 1, "tooltip": "Starting index for filename counter."}),
                "zero_padding": ("INT", {"default": 5, "step": 1, "tooltip": "The number of zeros the output sequence is padded with"}),
                "output_folder": ("STRING", {"default": "output", "tooltip": "The folder to save the images to."}),
                "subfolder_name": ("STRING", {"default": "", "tooltip": "Optional subfolder appended to output directory."}),
                "auto_version_subfolder": ("BOOLEAN", {"default": True, "tooltip": "If enabled, finds the highest numbered subfolder matching subfolder_prefix and creates the next one."}),
                "compression_level": ("INT", {"default": 4, "step": 1, "min": 0, "max": 9, "tooltip": "Sets the Compression level on the saved image 0-1-"}),
            },
            "optional": {
                "caption_file_extension": ("STRING", {"default": ".txt", "tooltip": "The extension for the caption file."}),
                "caption": ("STRING", {"forceInput": True, "tooltip": "string to save as .txt file"}),
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
    DESCRIPTION = "Saves the input images to your ComfyUI output directory."

    def save_images(self, images, output_folder, filename_prefix="ComfyUI", filename_separator="_", sequence_start=1, zero_padding=5, subfolder_name="", auto_version_subfolder=True, compression_level=4, prompt=None, extra_pnginfo=None, caption=None, caption_file_extension=".txt"):
        filename_prefix += self.prefix_append
        self.compress_level = compression_level
        subfolder_name = subfolder_name.strip().strip("/\\")

        if os.path.isabs(output_folder):
            pass
        else:
            output_folder = os.path.join(folder_paths.get_output_directory(), output_folder)

        if subfolder_name != "":
            if auto_version_subfolder is True:
                subfolder_name = get_versioned_subfolder_name(os.path.abspath(output_folder), subfolder_name)
            output_folder = os.path.join(output_folder, subfolder_name)

        output_folder = os.path.normpath(output_folder)
        os.makedirs(output_folder, exist_ok=True)

        full_output_folder, filename, counter, subfolder, filename_prefix = folder_paths.get_save_image_path(filename_prefix, output_folder, images[0].shape[1], images[0].shape[0])

        counter = sequence_start  # Overrides the counter output by Comfyui above


        results = list()
        for (batch_number, image) in enumerate(images):
            i = 255. * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))
            metadata = None
            if not args.disable_metadata:
                metadata = PngInfo()
                if prompt is not None:
                    metadata.add_text("prompt", json.dumps(prompt))
                if extra_pnginfo is not None:
                    for x in extra_pnginfo:
                        metadata.add_text(x, json.dumps(extra_pnginfo[x]))

            filename_with_batch_num = filename.replace("%batch_num%", str(batch_number))
            base_file_name = f"{filename_with_batch_num}{filename_separator}{counter:0{zero_padding}d}"
            file = f"{base_file_name}.png"
            img.save(os.path.join(full_output_folder, file), pnginfo=metadata, compress_level=self.compress_level)
            results.append({
                "filename": file,
                "subfolder": subfolder,
                "type": self.type
            })
            if caption is not None:
                txt_file = base_file_name + caption_file_extension
                file_path = os.path.join(full_output_folder, txt_file)
                with open(file_path, 'w') as f:
                    f.write(caption)

            counter += 1

        return file,