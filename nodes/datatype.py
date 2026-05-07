class Datatype:
    """Returns the datatype of any connected input as a string."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "value": ("*",),
            }
        }

    RETURN_TYPES = ("STRING", "STRING",)
    RETURN_NAMES = ("type", "detail",)
    FUNCTION = "inspect"
    CATEGORY = "KDNodes/utility"
    OUTPUT_NODE = False

    def inspect(self, value):
        # Native ComfyUI VIDEO type: dict with 'path' and 'fps'
        if isinstance(value, dict) and "path" in value and "fps" in value:
            clean = "VIDEO"
            detail = f"path={value.get('path', '?')} fps={value.get('fps', '?')}"

        # Tensor: IMAGE (4D), MASK (3D), or raw latent array
        elif hasattr(value, "shape"):
            ndim = value.ndim
            if ndim == 4:
                clean = "IMAGE"
            elif ndim == 3:
                clean = "MASK"
            else:
                clean = "TENSOR"
            detail = f"shape={tuple(value.shape)} dtype={value.dtype}"

        # LATENT: dict with 'samples' tensor
        elif isinstance(value, dict) and "samples" in value:
            clean = "LATENT"
            samples = value["samples"]
            shape = tuple(samples.shape) if hasattr(samples, "shape") else "?"
            detail = f"samples.shape={shape}"

        # AUDIO: dict with 'waveform' and 'sample_rate'
        elif isinstance(value, dict) and "waveform" in value and "sample_rate" in value:
            waveform = value["waveform"]
            shape = tuple(waveform.shape) if hasattr(waveform, "shape") else "?"
            clean = "AUDIO"
            detail = f"sample_rate={value['sample_rate']} waveform.shape={shape}"

        # Generic dict
        elif isinstance(value, dict):
            clean = "DICT"
            detail = f"keys={list(value.keys())}"

        # CONDITIONING: list of [tensor, dict] pairs
        elif isinstance(value, list) and len(value) > 0 and isinstance(value[0], (list, tuple)) and len(value[0]) == 2 and hasattr(value[0][0], "shape"):
            clean = "CONDITIONING"
            cond_shape = tuple(value[0][0].shape)
            pool_keys = list(value[0][1].keys()) if isinstance(value[0][1], dict) else "?"
            detail = f"len={len(value)} cond.shape={cond_shape} pool_keys={pool_keys}"

        # List / tuple
        elif isinstance(value, (list, tuple)):
            clean = type(value).__name__.upper()
            detail = f"len={len(value)}"

        else:
            clean = type(value).__name__.upper()
            detail = repr(value) if len(repr(value)) < 200 else type(value).__name__

        return (clean, detail,)

