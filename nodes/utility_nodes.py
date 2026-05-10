import gc, torch.cuda, comfy.model_management
from server import PromptServer
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

