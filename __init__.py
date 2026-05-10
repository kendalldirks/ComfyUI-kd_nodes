from .nodes.ffmpeg_helper import ensure_ffmpeg
ensure_ffmpeg()

from .nodes.image_nodes import *
from .nodes.utility_nodes import *
from .nodes.set_path import *
from .nodes.save_image_kd import *
from .nodes.load_image_kd import *
from .nodes.datatype import *
from .nodes.load_video_kd import *
from .nodes.save_video_kd import *

NODE_CONFIG = {
    #image nodes
    "SaveImageKD": {"class": SaveImageKD, "name": "Save Image KD"},
    "LoadImageKD": {"class": LoadImageKD, "name": "Load Image KD"},
    "LoadImagesPathKD": {"class": LoadImagesPathKD, "name": "Load Images from Path KD"},
    "MattePreview": {"class": MattePreview, "name": "Matte Preview"},
    "ImageRebatchOverlap": {"class": ImageRebatchOverlap, "name": "Rebatch Images Overlap"},
    "UnbatchImagesOverlapBlend": {"class": UnbatchImagesOverlapBlend, "name": "Unbatch Images Overlap Blend"},
    "PreviewImageKD": {"class": PreviewImageKD, "name": "Preview Image KD"},
    "PreviewAnimationKD": {"class": PreviewAnimationKD, "name": "Preview Animation KD"},

    #video nodes
#    "LoadVideoKD": {"class": LoadVideoKD, "name": "Load Video KD"},
    "SaveVideoKD": {"class": SaveVideoKD, "name": "Save Video KD"},

    #utility nodes
    "ItemFromListString": {"class": ItemFromListString, "name": "Item From List (String)"},
    "StringToInt": {"class": StringToInt, "name": "String to Integer"},
    "IntToString": {"class": IntToString, "name": "Integer to String"},
    "PurgeVRAM": {"class": PurgeVRAM, "name": "Purge VRAM"},
    "SetPath": {"class": SetPath, "name": "Set Path"},
    "Datatype": {"class": Datatype, "name": "Data Type"},
    "RaiseError": {"class": RaiseError, "name": "Raise Error"},
}

def generate_node_mappings(node_config):
    node_class_mappings = {}
    node_display_name_mappings = {}

    for node_name, node_info in node_config.items():
        node_class_mappings[node_name] = node_info["class"]
        node_display_name_mappings[node_name] = node_info.get("name", node_info["class"].__name__)

    return node_class_mappings, node_display_name_mappings

NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS = generate_node_mappings(NODE_CONFIG)
WEB_DIRECTORY = "./js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]