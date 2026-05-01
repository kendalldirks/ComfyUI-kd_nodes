import subprocess
import sys
from aiohttp import web
from server import PromptServer

def install_pyqt():
    subprocess.run([sys.executable, "-m", "pip", "install", "PyQt5"], check=True)

try:
    from PyQt5.QtWidgets import QApplication, QFileDialog
except ImportError:
    print("[DirectoryKD] PyQt5 not found, installing...")
    install_pyqt()
    from PyQt5.QtWidgets import QApplication, QFileDialog
    print("[DirectoryKD] PyQt5 installed successfully")

def _open_directory_dialog():
    app = QApplication.instance() or QApplication(sys.argv)
    path = QFileDialog.getExistingDirectory(None, "Select Directory")
    return path or ""

@PromptServer.instance.routes.get("/directory_kd/open")
async def open_directory_kd(request):
    try:
        path = _open_directory_dialog()
        return web.json_response({"path": path or ""})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

class DirectoryKD:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "path": ("STRING", {"default": "", "multiline": True}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("path",)
    FUNCTION = "run"
    CATEGORY = "utils"

    def run(self, path):
        return (path.strip().replace("\n", "").replace("\r", ""),)