import gc, json, torch.cuda, comfy.model_management
from server import PromptServer
from comfy_api.latest import io
from .utils import AnyType

any = AnyType("*")

class ItemFromListString:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "strings": ("STRING", {"forceInput": True}),
                "index": ("INT", {"default": 0, "min": 0, "step": 1}),
            }
        }

    INPUT_IS_LIST = True
    RETURN_TYPES = ("STRING",)
    FUNCTION = "get_item"
    CATEGORY = "KDNodes/utility"

    def flatten(self, lst):
        flattened = []
        for item in lst:
            if isinstance(item, list): flattened.extend(self.flatten(item))
            else:
                flattened.append(item)
        return flattened


    def get_item(self, strings, index):

        if strings is None:
            return ("",)

        if isinstance(strings, list):
            items = self.flatten(strings)
        else:
            items = [str(strings)]

        index = index[0] if isinstance(index, list) and len(index) > 0 else 0
        if index >= len(items): return ("",)
        return (str(items[index]),)

class StringToInt:
    """
    A node that converts a string to an integer.
    """

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "text": ("STRING", {"default": "", "multiline": False}),
            },
        }

    RETURN_TYPES = ("INT",)
    RETURN_NAMES = ("integer",)

    CATEGORY = "KDNodes/utility"
    FUNCTION = "process"

    def process(self, text: str) -> tuple:
        return (int(text),)

class IntToString:
    """
    A node that converts an integer to a string.
    """

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "integer": ("INT", {"default": 0, "min": -2000000000, "max": 2000000000}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)

    CATEGORY = "KDNodes/utility"
    FUNCTION = "process"

    def process(self, integer: int) -> tuple:
        return (str(integer),)

class PurgeVRAM:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "anything": (any, {}),
                "unload_models": ("BOOLEAN", {"default": True}),
                "clear_gpu_cache": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = (any,)
    RETURN_NAMES = ("anything",)
    OUTPUT_NODE = True
    FUNCTION = "purge"
    CATEGORY = "KDNodes/utility"

    def purge(self, anything, unload_models=True, clear_gpu_cache=True):

        if unload_models:
            comfy.model_management.unload_all_models()

        gc.collect()

        if clear_gpu_cache:
            comfy.model_management.soft_empty_cache()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()

        if unload_models:
            PromptServer.instance.prompt_queue.set_flag("free_memory", True)

        return (anything,)


class RaiseError:
    """
    A ComfyUI node that raises an error, printing a custom message.
    The workflow halts before the output is passed through.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "message": ("STRING", {"default": "An error occurred."}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("passthrough",)
    FUNCTION = "raise_error"
    CATEGORY = "KDNodes/utility"

    def raise_error(self, message):
        raise Exception(f"[RaiseErrorNode] {message}")
        return (message,)  # Never reached, but satisfies ComfyUI's type system

class NoneConstant:
    """
    Outputs a Python None. Feed into an optional input to make it behave as disconnected.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    RETURN_TYPES = (any,)
    RETURN_NAMES = ("none",)
    FUNCTION = "get_none"
    CATEGORY = "KDNodes/utility"

    def get_none(self):
        return (None,)

class SAM3PointsToNativeCoords(io.ComfyNode):
    """
    Converts SAM3_POINTS_PROMPT (normalized 0-1 coords) into the JSON pixel-coord
    strings expected by the native SAM3_Detect node's positive/negative_coords inputs.
    """

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="SAM3PointsToNativeCoords",
            display_name="SAM3 Points -> Native Coords",
            category="KDNodes/utility",
            inputs=[
                io.Custom("SAM3_POINTS_PROMPT").Input("positive_points", optional=True),
                io.Custom("SAM3_POINTS_PROMPT").Input("negative_points", optional=True),
                io.Image.Input("image", optional=True),
                io.Int.Input("width", default=0, min=0, optional=True),
                io.Int.Input("height", default=0, min=0, optional=True),
            ],
            outputs=[
                io.String.Output(display_name="positive_coords"),
                io.String.Output(display_name="negative_coords"),
            ],
        )

    @classmethod
    def execute(cls, positive_points=None, negative_points=None,
                image=None, width=0, height=0) -> io.NodeOutput:
        if image is not None:
            H, W = int(image.shape[1]), int(image.shape[2])
        elif width > 0 and height > 0:
            W, H = int(width), int(height)
        else:
            raise ValueError("SAM3PointsToNativeCoords: connect an image or set width/height.")

        pos_out, neg_out = [], []

        def _ingest(prompt):
            if not prompt:
                return
            pts = prompt.get("points", []) or []
            labels = prompt.get("labels", None)
            if labels is None or len(labels) != len(pts):
                labels = [1] * len(pts)
            for pt, lab in zip(pts, labels):
                d = {"x": int(round(float(pt[0]) * W)), "y": int(round(float(pt[1]) * H))}
                (pos_out if int(lab) == 1 else neg_out).append(d)

        _ingest(positive_points)
        _ingest(negative_points)

        return io.NodeOutput(json.dumps(pos_out), json.dumps(neg_out))

