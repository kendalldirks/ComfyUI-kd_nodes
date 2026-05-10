import os
import re
import shutil
import subprocess
import numpy as np
import torch
import folder_paths

def _find_ffmpeg():
    """
    Locate an ffmpeg binary.  Search order:
      1. Bundled in <this‑package>/bin/  (future‑proofing for a bundled build)
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


def get_versioned_filename(directory, basename, ext):
    """
    Given  directory/basename.ext  return the next available version:
        basename.ext   → basename_v2.ext  → basename_v3.ext  …
    If the base name doesn't exist yet, return it unchanged.
    """
    target = f"{basename}.{ext}"
    if not os.path.exists(os.path.join(directory, target)):
        return target

    pattern = re.compile(
        rf"^{re.escape(basename)}_v(\d+)\.{re.escape(ext)}$", re.IGNORECASE
    )
    max_ver = 1                       # the un‑versioned file counts as v1
    for name in os.listdir(directory):
        m = pattern.match(name)
        if m:
            max_ver = max(max_ver, int(m.group(1)))

    return f"{basename}_v{max_ver + 1}.{ext}"


def tensor_to_bytes(t):
    """Convert a single HWC float‑[0,1] tensor to uint8 numpy HWC."""
    arr = t.cpu().numpy()
    return np.clip(arr * 255.0 + 0.5, 0, 255).astype(np.uint8)



class SaveVideoKD:
    """
    Encodes a batch of IMAGE frames into an H.264 MP4 via FFmpeg, with
    optional AUDIO muxing.
    """

    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "images": ("IMAGE",   {"tooltip": "The image batch to encode as video frames."}),
                "save_path": ("STRING", {"default": ""}),
                "filename_prefix": ("STRING", {"default": "video"}),
                "frame_rate": ("FLOAT",   {"default": 24.0, "min": 1.0, "max": 120.0, "step": 0.01}),
                "codec_CRF": ("INT",     {"default": 18,   "min": 0,   "max": 51, "tooltip": "H.264 CRF value.  Lower = higher quality / larger file.  0 = lossless, 18‑23 = visually transparent, 28+ = noticeable compression."}),
                "allow_overwrites": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "audio": ("AUDIO",),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("filename",)
    OUTPUT_NODE = True
    FUNCTION = "save_video"
    CATEGORY = "KDNodes/video"
    DESCRIPTION = (
        "Encodes input images as an H.264 MP4 video. "
        "Optionally muxes an AUDIO input.  Video duration is authoritative — "
        "audio is padded with silence or trimmed to match."
    )

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    def save_video(
        self,
        images,
        frame_rate,
        codec_CRF,
        save_path,
        filename_prefix,
        allow_overwrites,
        audio=None,
        prompt=None,
        extra_pnginfo=None,
    ):
        if FFMPEG_PATH is None:
            raise RuntimeError(
                "ffmpeg was not found.  Install ffmpeg and make sure it is on "
                "your system PATH, or place an ffmpeg binary in the 'bin' "
                "sub‑folder of this node pack."
            )

        if images is None or (isinstance(images, torch.Tensor) and images.size(0) == 0):
            return ("",)

        num_frames = images.shape[0] if isinstance(images, torch.Tensor) else len(images)

        # ----- resolve output directory -----
        if not os.path.isabs(save_path):
            save_path = os.path.join(folder_paths.get_output_directory(), save_path)

        final_save_path = os.path.normpath(save_path)
        os.makedirs(final_save_path, exist_ok=True)

        # ----- determine output filename -----
        if allow_overwrites:
            out_filename = f"{filename_prefix}.mp4"
        else:
            out_filename = get_versioned_filename(
                final_save_path, filename_prefix, "mp4"
            )

        out_path = os.path.join(final_save_path, out_filename)

        # ----- frame geometry & alignment -----
        first = images[0]
        h, w = first.shape[0], first.shape[1]
        # H.264 requires even dimensions
        pad_w = w % 2
        pad_h = h % 2
        if pad_w or pad_h:
            padding = (0, pad_w, 0, pad_h)   # left, right, top, bottom
            padfunc = torch.nn.ReplicationPad2d(padding)
            def _pad(frame):
                return padfunc(
                    frame.permute(2, 0, 1).to(dtype=torch.float32)
                ).permute(1, 2, 0)
            images_iter = map(_pad, images)
            w += pad_w
            h += pad_h
        else:
            images_iter = iter(images)

        # ----- check for audio -----
        has_audio = False
        if audio is not None:
            try:
                waveform = audio["waveform"]     # [1, C, samples]
                sample_rate = audio["sample_rate"]
                if waveform is not None and waveform.numel() > 0:
                    has_audio = True
            except (KeyError, TypeError):
                pass

        # ----- encode -----
        if has_audio:
            self._encode_with_audio(
                images_iter, w, h, frame_rate, codec_CRF,
                out_path, waveform, sample_rate, num_frames,
            )
        else:
            self._encode_silent(
                images_iter, w, h, frame_rate, codec_CRF, out_path,
            )

        return (out_filename,)

    # ------------------------------------------------------------------
    # Encoding helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_video_input_args(w, h, frame_rate):
        """Common ffmpeg args for raw‑video stdin."""
        return [
            FFMPEG_PATH, "-y",
            "-v", "error",
            # --- raw video on stdin ---
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", f"{w}x{h}",
            "-r", str(frame_rate),
            "-i", "-",
        ]

    @staticmethod
    def _video_encode_args(codec_quality):
        """H.264 encoding args (output side)."""
        return [
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", str(codec_quality),
            "-pix_fmt", "yuv420p",
            # Tag colour properly so players / YouTube interpret sRGB correctly
            "-colorspace", "bt709",
            "-color_primaries", "bt709",
            "-color_trc", "bt709",
            "-movflags", "+faststart",
        ]

    @staticmethod
    def _pipe_frames(proc, images_iter):
        """Write every frame into the process stdin, then close."""
        for frame in images_iter:
            proc.stdin.write(tensor_to_bytes(frame).tobytes())
        proc.stdin.flush()
        proc.stdin.close()

    @staticmethod
    def _read_stderr(proc):
        """Read stderr in a background thread to prevent pipe deadlocks."""
        import threading

        stderr_chunks = []
        def _drain():
            stderr_chunks.append(proc.stderr.read())
        t = threading.Thread(target=_drain, daemon=True)
        t.start()
        return t, stderr_chunks

    # -- silent video ---------------------------------------------------

    def _encode_silent(self, images_iter, w, h, frame_rate, crf, out_path):
        args = (
            self._build_video_input_args(w, h, frame_rate)
            + self._video_encode_args(crf)
            + [out_path]
        )

        with subprocess.Popen(
            args, stdin=subprocess.PIPE, stderr=subprocess.PIPE
        ) as proc:
            stderr_thread, stderr_chunks = self._read_stderr(proc)
            self._pipe_frames(proc, images_iter)
            proc.wait()
            stderr_thread.join()

        err = b"".join(stderr_chunks)
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg exited with code {proc.returncode}:\n"
                + err.decode("utf-8", errors="replace")
            )

    # -- video + audio (single output file) -----------------------------

    def _encode_with_audio(
        self, images_iter, w, h, frame_rate, crf,
        out_path, waveform, sample_rate, num_frames,
    ):
        """
        Two‑pass approach:
          1. Encode silent video to a temp file via stdin pipe.
          2. Mux temp video + raw audio (piped on stdin) into final output.

        Audio handling (matches VHS):
          • Audio shorter than video → padded with silence via apad=whole_dur
          • Audio longer  than video → trimmed at video end via -shortest
        """
        import tempfile

        temp_dir = os.path.dirname(out_path)
        temp_fd, temp_video = tempfile.mkstemp(prefix="temp_", suffix=".mp4", dir=temp_dir)
        os.close(temp_fd)

        try:
            self._encode_silent(images_iter, w, h, frame_rate, crf, temp_video)

            channels = waveform.size(1)
            audio_data = (
                waveform.squeeze(0)        # [C, samples]
                .transpose(0, 1)           # [samples, C]
                .contiguous()
                .numpy()
                .tobytes()
            )

            # apad=whole_dur=X pads silence up to X seconds then STOPS.
            # Bare "apad" generates infinite silence and hangs FFmpeg.
            min_audio_dur = num_frames / frame_rate + 1

            args = [
                FFMPEG_PATH, "-y",
                "-v", "error",
                "-i", temp_video,
                "-f", "f32le",
                "-ar", str(sample_rate),
                "-ac", str(channels),
                "-i", "-",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-af", f"apad=whole_dur={min_audio_dur}",
                "-shortest",
                "-movflags", "+faststart",
                out_path,
            ]

            result = subprocess.run(args, input=audio_data, capture_output=True)
            if result.returncode != 0:
                raise RuntimeError(
                    f"ffmpeg (audio mux) exited with code {result.returncode}:\n"
                    + result.stderr.decode("utf-8", errors="replace")
                )
        finally:
            if os.path.exists(temp_video):
                os.remove(temp_video)