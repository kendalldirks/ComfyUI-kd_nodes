import subprocess
import sys
from aiohttp import web
from server import PromptServer

def install_pyqt():
    subprocess.run([sys.executable, "-m", "pip", "install", "PyQt5"], check=True)

try:
    from PyQt5.QtWidgets import QApplication, QFileDialog
    from PyQt5.QtCore import Qt
except ImportError:
    print("[SetPath] PyQt5 not found, installing...")
    install_pyqt()
    from PyQt5.QtWidgets import QApplication, QFileDialog
    from PyQt5.QtCore import Qt
    print("[SetPath] PyQt5 installed successfully")

def _open_directory_dialog():
    app = QApplication.instance() or QApplication(sys.argv)
    dialog = QFileDialog()
    dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowStaysOnTopHint)
    dialog.setWindowModality(Qt.ApplicationModal)
    dialog.setFileMode(QFileDialog.Directory)
    dialog.setOption(QFileDialog.ShowDirsOnly, True)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    dialog.exec_()
    if dialog.result() == QFileDialog.Accepted:
        return dialog.selectedFiles()[0]
    return ""

@PromptServer.instance.routes.get("/set_path/open")
async def open_set_path(request):
    try:
        path = _open_directory_dialog()
        return web.json_response({"path": path or ""})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

class SetPath:
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
