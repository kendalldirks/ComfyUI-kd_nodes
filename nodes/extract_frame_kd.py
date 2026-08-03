import os
import random
import subprocess

import folder_paths

from .utils import plan_resize, frames_to_uint8


BIGMAX = (2**53 - 1)


# --- Preview proxy settings ---
#
# The preview video is a display proxy only.  The frame this node outputs is
# sliced straight out of the input tensor and is never decoded back out of the
# mp4, so the proxy can be small and lossy without affecting the result.
#
# A short GOP keeps seeking frame-exact and quick without the quality cost of
# all-intra.  Measured on high-frequency texture at crf 20: -g 1 scored
# 32.5 dB PSNR at 5.4 MB, -g 12 scored 42.5 dB at 2.2 MB -- 10 dB better and
# smaller.  With no inter-frame prediction available, x264's CRF ratecontrol
# raises the quantizer from ~20 to ~32 to hit the same rate factor, so
# all-intra is actively worse here.  Seeks stayed exact at every GOP tested.

PREVIEW_CRF = 20
PREVIEW_PRESET = "veryfast"

# Keyframe interval. 12 is ~0.2 dB off an unconstrained GOP while only ever
# decoding 11 frames forward on a seek. Drop to 6 if scrubbing feels sluggish;
# do NOT set 1 -- see above.
PREVIEW_GOP = 12

# Rate the proxy is encoded at.  Fixed on purpose and unrelated to the node's
# `fps` widget: the frontend seeks by frame index converted through *this*
# value, so tying it to a user-editable widget would desynchronise the
# scrubber from the already-loaded video whenever that widget changed.  The
# `fps` widget only labels the timecode readout.
PREVIEW_ENCODE_FPS = 24.0

# Proxy sizing.  Caps the LONGEST edge, so portrait and landscape sources give
# proxies of the same size.  Sources already under it are left alone entirely.
# Raising this is cheap: the cost is dominated by reading the source batch,
# which is fixed regardless of how big the proxy ends up.
PREVIEW_TARGET_SIZE = 1024       # longest edge; 0 = never downscale
PREVIEW_MIN_DIM = 16             # never downscale an axis below this


# --- Node ---

class ExtractFrameKD:
    """
    Pick one frame out of an IMAGE batch using an in-node video scrubber.

    On execution the batch is encoded to a small short-GOP mp4 in the temp
    directory and handed to the frontend, which scrubs it client-side.
    Scrubbing never executes anything; it only moves the `frame` widget, and
    the chosen frame is emitted on the next queue.
    """

    def __init__(self):
        self.output_dir = folder_paths.get_temp_directory()
        self.type = "temp"
        self.prefix_append = "_temp_" + ''.join(random.choice("abcdefghijklmnopqrstupvxyz") for x in range(5))
        # (images_ref, preview_dict) — see extract() for why identity works
        self._cache = None

    @classmethod
    def INPUT_TYPES(s):
        return {"required":
                    {
                     "images": ("IMAGE", ),
                     "frame": ("INT", {"default": 0, "min": 0, "max": BIGMAX, "step": 1,
                                       "tooltip": "Index of the frame to output. Drag the "
                                                  "scrubber or type a value, then queue."}),
                     "fps": ("FLOAT", {"default": 24.0, "min": 0.01, "max": 1000.0, "step": 0.01,
                                       "tooltip": "Timecode basis for the scrubber readout. "
                                                  "Does not change which frames exist."}),
                    },
                }

    RETURN_TYPES = ("IMAGE", "INT", "INT",)
    RETURN_NAMES = ("image", "frame_index", "total_frames",)
    FUNCTION = "extract"
    OUTPUT_NODE = True
    CATEGORY = "KDNodes/video"

    def _plan(self, h, w):
        """(height, width) for the proxy. See plan_resize in utils."""
        return plan_resize(h, w,
                           target_size=PREVIEW_TARGET_SIZE,
                           min_dim=PREVIEW_MIN_DIM)

    def _frames_uint8(self, images):
        """Proxy-sized (N, H, W, 3) uint8 array. See frames_to_uint8 in utils."""
        return frames_to_uint8(images,
                               target_size=PREVIEW_TARGET_SIZE,
                               min_dim=PREVIEW_MIN_DIM)

    def _encode_preview(self, images):
        """
        Encode the batch to a small short-GOP mp4 in the temp directory.

        Returns the payload the frontend needs to point a <video> at the file
        through ComfyUI's native /view route, or None if it couldn't be written.
        """
        from .load_video_kd import FFMPEG_PATH

        if FFMPEG_PATH is None:
            print("ExtractFrameKD: ffmpeg not found, cannot build preview")
            return None

        frames = self._frames_uint8(images)
        num_frames, H, W = frames.shape[0], frames.shape[1], frames.shape[2]

        full_output_folder, filename, counter, subfolder, filename_prefix = \
            folder_paths.get_save_image_path("FrameScrub" + self.prefix_append, self.output_dir)
        file = f"{filename}_{counter:05}_.mp4"
        out_path = os.path.join(full_output_folder, file)

        # Already downscaled to even dimensions in _frames_uint8, so ffmpeg gets
        # the proxy at its final size and needs no filtering at all.
        args = [
            FFMPEG_PATH, "-v", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{W}x{H}", "-r", str(PREVIEW_ENCODE_FPS), "-i", "-",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", str(PREVIEW_PRESET), "-crf", str(PREVIEW_CRF),
            # Short GOP: seeks stay frame-exact, quality stays intact.
            "-g", str(PREVIEW_GOP), "-keyint_min", str(PREVIEW_GOP),
            "-movflags", "+faststart", out_path,
        ]
        # frames is C-contiguous out of np.empty, so hand ffmpeg the underlying
        # buffer directly.  tobytes() would duplicate the whole thing first.
        buf = frames.reshape(-1).data

        proc = subprocess.Popen(args, stdin=subprocess.PIPE)
        proc.stdin.write(buf)
        proc.stdin.close()
        proc.wait()

        if not os.path.isfile(out_path):
            print("ExtractFrameKD: ffmpeg did not produce a preview")
            return None

        # Served through ComfyUI's native /view route (no re-transcode).
        # "_path" is for our own cleanup below; the frontend ignores it.
        return {"filename": file, "subfolder": subfolder, "type": self.type,
                "format": "video/mp4", "frames": int(num_frames),
                "fps": PREVIEW_ENCODE_FPS,
                "width": W, "height": H, "_path": out_path}

    def _discard_proxy(self, preview):
        """Remove a superseded proxy so only the current one is left in temp."""
        path = (preview or {}).get("_path")
        if not path:
            return
        try:
            os.remove(path)
        except OSError:
            # Still held open by a <video>, or already swept — harmless either way.
            pass

    def extract(self, images, frame, fps):
        num_frames = images.shape[0]
        if num_frames == 0:
            raise ValueError("ExtractFrameKD: input batch is empty")

        frame_index = max(0, min(int(frame), num_frames - 1))

        # ComfyUI's cache is all-or-nothing per node: changing `frame` changes
        # this node's cache key, so the whole method re-runs, ffmpeg included.
        # Comparing tensor identity skips the re-encode when only `frame` moved.
        # Holding the reference in self._cache keeps the tensor alive, so its
        # id can't be recycled and `is` stays an exact test.
        if self._cache is not None and self._cache[0] is images:
            preview = self._cache[1]
        else:
            preview = self._encode_preview(images)
            if preview is not None:
                self._discard_proxy(self._cache[1] if self._cache else None)
                self._cache = (images, preview)

        return {"ui": {"kd_frame": [preview] if preview else []},
                "result": (images[frame_index:frame_index + 1], frame_index, num_frames,)}
