import os
import asyncio
import cv2
import hashlib
import itertools
import logging
import re
import numpy as np
import torch
import psutil
import subprocess
import sys
import shutil
import folder_paths
from collections.abc import Mapping
from comfy.utils import ProgressBar
from aiohttp import web
from server import PromptServer

logger = logging.getLogger(__name__)

BIGMAX = (2**53 - 1)
ENCODE_ARGS = ("utf-8", "backslashreplace")

video_extensions = ['webm', 'mp4', 'mkv', 'gif', 'mov']


# --- ffmpeg detection ---

def _find_ffmpeg():
    """
    Locate an ffmpeg binary.  Search order:
      1. Bundled in <this-package>/bin/  (future-proofing for a bundled build)
      2. System PATH via shutil.which
    Returns the absolute path string, or None.
    """
    # 1 — bundled
    pkg_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    for candidate in ("ffmpeg", "ffmpeg.exe"):
        bundled = os.path.join(pkg_dir, "bin", candidate)
        if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
            return bundled

    # 2 — system
    return shutil.which("ffmpeg")


FFMPEG_PATH = _find_ffmpeg()

if FFMPEG_PATH is None:
    logger.error("[LoadVideoKD] No ffmpeg binary found. Audio extraction and video preview transcoding will not work.")


# --- yt-dlp detection ---
ytdl_path = os.environ.get("VHS_YTDL", None) or shutil.which('yt-dlp') \
        or shutil.which('youtube-dl')
download_history = {}


# --- PyQt5 for native file dialog ---

def install_pyqt():
    subprocess.run([sys.executable, "-m", "pip", "install", "PyQt5"], check=True)

try:
    from PyQt5.QtWidgets import QApplication, QFileDialog, QWidget
    from PyQt5.QtGui import QCursor
    from PyQt5.QtCore import Qt
except ImportError:
    print("[LoadVideoKD] PyQt5 not found, installing...")
    install_pyqt()
    from PyQt5.QtWidgets import QApplication, QFileDialog, QWidget
    from PyQt5.QtGui import QCursor
    from PyQt5.QtCore import Qt
    print("[LoadVideoKD] PyQt5 installed successfully")


def _open_video_dialog(start_dir=""):
    app = QApplication.instance() or QApplication(sys.argv)

    # Create invisible parent at center of current screen
    cursor_pos = QCursor.pos()
    screen = app.screenAt(cursor_pos)
    if screen:
        geo = screen.availableGeometry()
        center_x = geo.x() + geo.width() // 2
        center_y = geo.y() + geo.height() // 2
    else:
        center_x = cursor_pos.x()
        center_y = cursor_pos.y()

    parent = QWidget()
    parent.setGeometry(center_x, center_y, 0, 0)
    parent.setWindowOpacity(0)
    parent.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
    parent.show()

    # If start_dir is a file path, use its parent directory
    if start_dir and os.path.isfile(start_dir):
        start_dir = os.path.dirname(start_dir)
    elif start_dir and not os.path.isdir(start_dir):
        start_dir = ""

    path, _ = QFileDialog.getOpenFileName(
        parent,
        "Select Video File",
        start_dir,
        "Video files (*.mp4 *.webm *.mkv *.gif *.mov);;All files (*)"
    )

    parent.close()
    parent.deleteLater()

    return path or ""


@PromptServer.instance.routes.get("/kd_nodes/open_video")
async def open_video_dialog(request):
    try:
        start_dir = request.query.get("path", "")
        path = _open_video_dialog(start_dir)
        return web.json_response({"path": path or ""})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


# --- Video serving route for preview (async streaming ffmpeg transcode) ---

@PromptServer.instance.routes.get("/kd_nodes/view_video")
async def view_video(request):
    query = request.rel_url.query
    filename = query.get("filename", "")
    if not filename or not os.path.isfile(filename):
        return web.Response(status=404, text="File not found")

    ext = os.path.splitext(filename)[1].lower()

    # GIFs can be served directly as images
    if ext == '.gif':
        return web.FileResponse(filename, headers={"Content-Type": "image/gif"})

    if FFMPEG_PATH is None:
        return web.FileResponse(filename)

    in_args = ["-i", filename]

    # Prepass to detect source codec and fps
    base_fps = 30
    try:
        proc = await asyncio.create_subprocess_exec(
            FFMPEG_PATH, *in_args, '-t', '0', '-f', 'null', '-',
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL
        )
        _, res_stderr = await proc.communicate()
        match = re.search(': Video: (\\w+) .+, (\\d+) fps,', res_stderr.decode(*ENCODE_ARGS))
        if match:
            base_fps = float(match.group(2))
            if match.group(1) == 'vp9':
                in_args = ['-c:v', 'libvpx-vp9'] + in_args
    except Exception as e:
        logger.warn(f"ffmpeg prepass failed for {filename}: {e}")

    vfilters = []

    # Apply frame rate
    target_rate = base_fps
    modified_rate = target_rate / (float(query.get('select_every_nth', 1)) or 1)

    # Apply skip/seek
    start_time = 0
    if float(query.get('skip_first_frames', 0)) > 0:
        start_time = float(query.get('skip_first_frames')) / target_rate
        if start_time > 1 / modified_rate:
            start_time += 1 / modified_rate

    if start_time > 0:
        if start_time > 4:
            post_seek = ['-ss', '4']
            pre_seek = ['-ss', str(start_time - 4)]
        else:
            post_seek = ['-ss', str(start_time)]
            pre_seek = []
    else:
        pre_seek = []
        post_seek = []

    args = [FFMPEG_PATH, "-v", "error"] + pre_seek + in_args + post_seek
    args += ['-r', str(modified_rate)]

    # Apply scaling
    force_size = query.get('force_size', '')
    if force_size and force_size != 'Disabled':
        size = force_size.split('x')
        if size[0] == '?' or size[1] == '?':
            size[0] = "-2" if size[0] == '?' else f"'min({size[0]},iw)'"
            size[1] = "-2" if size[1] == '?' else f"'min({size[1]},ih)'"
        size = ':'.join(size)
        vfilters.append(f"scale={size}")

    if len(vfilters) > 0:
        args += ["-vf", ",".join(vfilters)]

    # Apply frame cap
    if float(query.get('frame_load_cap', 0)) > 0:
        args += ["-frames:v", query['frame_load_cap'].split('.')[0]]

    # Encode to VP9 WebM with realtime deadline for speed
    args += ['-c:v', 'libvpx-vp9', '-deadline', 'realtime', '-cpu-used', '8',
             '-an', '-f', 'webm', '-']

    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL
        )
        try:
            resp = web.StreamResponse()
            resp.content_type = 'video/webm'
            await resp.prepare(request)
            while len(bytes_read := await proc.stdout.read(2**20)) != 0:
                await resp.write(bytes_read)
            await proc.wait()
        except (ConnectionResetError, ConnectionError):
            proc.kill()
    except BrokenPipeError:
        pass
    return resp


# --- Utility functions ---

def strip_path(path):
    path = path.strip()
    if path.startswith("\""):
        path = path[1:]
    if path.endswith("\""):
        path = path[:-1]
    return path


def is_url(url):
    return url.split("://")[0] in ["http", "https"]


def is_safe_path(path, strict=False):
    if "VHS_STRICT_PATHS" not in os.environ and not strict:
        return True
    basedir = os.path.abspath('.')
    try:
        common_path = os.path.commonpath([basedir, path])
    except:
        return False
    return common_path == basedir


def calculate_file_hash(filename):
    h = hashlib.sha256()
    h.update(filename.encode())
    h.update(str(os.path.getmtime(filename)).encode())
    return h.hexdigest()


def hash_path(path):
    if path is None:
        return "input"
    if is_url(path):
        return "url"
    if not os.path.isfile(path):
        return "DNE"
    return calculate_file_hash(strip_path(path))


def validate_path(path, allow_none=False, allow_url=True):
    if path is None:
        return allow_none
    if is_url(path):
        if not allow_url:
            return "URLs are unsupported for this path"
        return is_safe_path(path)
    if not os.path.isfile(strip_path(path)):
        return "Invalid file path: {}".format(path)
    return is_safe_path(path)


def try_download_video(url):
    if ytdl_path is None:
        return None
    if url in download_history:
        return download_history[url]
    os.makedirs(folder_paths.get_temp_directory(), exist_ok=True)
    try:
        res = subprocess.run([ytdl_path, "--print", "after_move:filepath",
                              "-P", folder_paths.get_temp_directory(), url],
                             capture_output=True, check=True)
        file = res.stdout.decode(*ENCODE_ARGS)[:-1]
    except subprocess.CalledProcessError as e:
        raise Exception("An error occurred in the yt-dl process:\n" \
                + e.stderr.decode(*ENCODE_ARGS))
        file = None
    download_history[url] = file
    return file


# --- Audio (ffmpeg) ---

def get_audio(file, start_time=0, duration=0):
    if FFMPEG_PATH is None:
        logger.warn("No ffmpeg found, cannot extract audio")
        return {'waveform': torch.zeros(1, 1, 1), 'sample_rate': 44100}

    args = [FFMPEG_PATH, "-i", file]
    if start_time > 0:
        args += ["-ss", str(start_time)]
    if duration > 0:
        args += ["-t", str(duration)]
    try:
        res = subprocess.run(args + ["-f", "f32le", "-"],
                             capture_output=True, check=True)
        audio = torch.frombuffer(bytearray(res.stdout), dtype=torch.float32)
        match = re.search(', (\\d+) Hz, (\\w+), ', res.stderr.decode(*ENCODE_ARGS))
    except subprocess.CalledProcessError as e:
        raise Exception(f"Failed to extract audio from {file}:\n" \
                + e.stderr.decode(*ENCODE_ARGS))
    if match:
        ar = int(match.group(1))
        ac = {"mono": 1, "stereo": 2}[match.group(2)]
    else:
        ar = 44100
        ac = 2
    audio = audio.reshape((-1, ac)).transpose(0, 1).unsqueeze(0)
    return {'waveform': audio, 'sample_rate': ar}


class LazyAudioMap(Mapping):
    def __init__(self, file, start_time, duration):
        self.file = file
        self.start_time = start_time
        self.duration = duration
        self._dict = None

    def __getitem__(self, key):
        if self._dict is None:
            self._dict = get_audio(self.file, self.start_time, self.duration)
        return self._dict[key]

    def __iter__(self):
        if self._dict is None:
            self._dict = get_audio(self.file, self.start_time, self.duration)
        return iter(self._dict)

    def __len__(self):
        if self._dict is None:
            self._dict = get_audio(self.file, self.start_time, self.duration)
        return len(self._dict)


def lazy_get_audio(file, start_time=0, duration=0, **kwargs):
    return LazyAudioMap(file, start_time, duration)


# --- Frame generators ---

def cv_frame_generator(video, frame_load_cap, skip_first_frames,
                       select_every_nth, unique_id=None):
    video_cap = cv2.VideoCapture(video)
    if not video_cap.isOpened() or not video_cap.grab():
        raise ValueError(f"{video} could not be loaded with cv.")

    fps = video_cap.get(cv2.CAP_PROP_FPS)
    width = int(video_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(video_cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps

    width = 0

    if width <= 0 or height <= 0:
        _, frame = video_cap.retrieve()
        height, width, _ = frame.shape

    total_frame_count = 0
    total_frames_evaluated = -1
    frames_added = 0
    target_frame_time = 1 / fps

    if total_frames > 0:
        yieldable_frames = total_frames
        if select_every_nth:
            yieldable_frames //= select_every_nth
        if frame_load_cap != 0:
            yieldable_frames = min(frame_load_cap, yieldable_frames)
    else:
        yieldable_frames = 0

    yield (width, height, fps, duration, total_frames, target_frame_time, yieldable_frames)
    pbar = ProgressBar(yieldable_frames)
    time_offset = target_frame_time

    while video_cap.isOpened():
        if time_offset < target_frame_time:
            is_returned = video_cap.grab()
            if not is_returned:
                break
            time_offset += target_frame_time
        if time_offset < target_frame_time:
            continue
        time_offset -= target_frame_time

        total_frame_count += 1
        if total_frame_count <= skip_first_frames:
            continue
        else:
            total_frames_evaluated += 1

        if total_frames_evaluated % select_every_nth != 0:
            continue

        unused, frame = video_cap.retrieve()
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frame = np.array(frame, dtype=np.float32)
        torch.from_numpy(frame).div_(255)
        prev_frame = frame
        frames_added += 1
        if pbar is not None:
            pbar.update_absolute(frames_added, yieldable_frames)

        yield prev_frame

        if frame_load_cap > 0 and frames_added >= frame_load_cap:
            break


def resized_cv_frame_gen(downscale_ratio=1, **kwargs):
    gen = cv_frame_generator(**kwargs)
    info = next(gen)
    width, height = info[0], info[1]
    yield (*info, width, height, False)
    yield from gen


# --- Main load function ---

def load_video_kd(unique_id=None, generator=resized_cv_frame_gen, **kwargs):
    kwargs['video'] = strip_path(kwargs['video'])
    downscale_ratio = 1

    gen = generator(unique_id=unique_id, downscale_ratio=downscale_ratio, **kwargs)
    (width, height, fps, duration, total_frames, target_frame_time,
     yieldable_frames, new_width, new_height, alpha) = next(gen)

    try:
        memory_limit = (psutil.virtual_memory().available + psutil.swap_memory().free) - 2 ** 27
    except:
        logger.warn("Failed to calculate available memory. Memory load limit has been disabled")
        memory_limit = BIGMAX

    max_loadable_frames = int(memory_limit // (width * height * 3 * 0.1))

    original_gen = gen
    gen = itertools.islice(gen, max_loadable_frames)

    images = torch.from_numpy(np.fromiter(gen, np.dtype((np.float32, (new_height, new_width, 4 if alpha else 3)))))

    if memory_limit is not None:
        try:
            next(original_gen)
            raise RuntimeError(f"Memory limit hit after loading {len(images)} frames. Stopping execution.")
        except StopIteration:
            pass

    if len(images) == 0:
        raise RuntimeError("No frames generated")

    if 'start_time' in kwargs:
        start_time = kwargs['start_time']
    else:
        start_time = kwargs['skip_first_frames'] * target_frame_time
    target_frame_time *= kwargs.get('select_every_nth', 1)

    audio = lazy_get_audio(kwargs['video'], start_time, kwargs['frame_load_cap'] * target_frame_time)

    return (images, len(images), audio)


# --- Node class ---

class LoadVideoKD:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "video": ("STRING", {"placeholder": "X://insert/path/here.mp4", "vhs_path_extensions": video_extensions}),
                "frame_load_cap": ("INT", {"default": 0, "min": 0, "max": BIGMAX, "step": 1, "disable": 0}),
                "skip_first_frames": ("INT", {"default": 0, "min": 0, "max": BIGMAX, "step": 1}),
                "select_every_nth": ("INT", {"default": 1, "min": 1, "max": BIGMAX, "step": 1}),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID"
            },
        }

    CATEGORY = "KD_Nodes/Video"

    RETURN_TYPES = ("IMAGE", "INT", "AUDIO", "STRING")
    RETURN_NAMES = ("IMAGE", "frame_count", "audio", "video_path")

    FUNCTION = "load_video"

    def load_video(self, **kwargs):
        if kwargs['video'] is None or validate_path(kwargs['video']) != True:
            raise Exception("video is not a valid path: " + kwargs['video'])
        if is_url(kwargs['video']):
            kwargs['video'] = try_download_video(kwargs['video']) or kwargs['video']
        video_path = kwargs['video']
        images, frame_count, audio = load_video_kd(**kwargs)
        return (images, frame_count, audio, video_path)

    @classmethod
    def IS_CHANGED(s, video, **kwargs):
        return hash_path(video)

    @classmethod
    def VALIDATE_INPUTS(s, video):
        return validate_path(video, allow_none=True)


NODE_CLASS_MAPPINGS = {
    "LoadVideoKD": LoadVideoKD,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LoadVideoKD": "Load Video (KD)",
}