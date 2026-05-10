import os
import platform
import shutil
import tarfile
import zipfile
import logging

logger = logging.getLogger("KDNodes")

_BIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin")

_FFMPEG_URLS = {
    ("Linux",  "x86_64"):  "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz",
    ("Linux",  "aarch64"): "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linuxarm64-gpl.tar.xz",
    ("Windows","AMD64"):   "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    ("Darwin", "x86_64"):  "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz",
    ("Darwin", "arm64"):   "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linuxarm64-gpl.tar.xz",
}


def _expected_binary():
    name = "ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg"
    return os.path.join(_BIN_DIR, name)


def _extract_ffmpeg_from_tar(archive_path, dest_path):
    with tarfile.open(archive_path, "r:xz") as tar:
        for member in tar.getmembers():
            if member.name.endswith("/ffmpeg") and member.isfile():
                member.name = os.path.basename(member.name)
                tar.extract(member, _BIN_DIR)
                os.chmod(dest_path, 0o755)
                return True
    return False


def _extract_ffmpeg_from_zip(archive_path, dest_path):
    with zipfile.ZipFile(archive_path, "r") as zf:
        for name in zf.namelist():
            if name.endswith("bin/ffmpeg.exe"):
                data = zf.read(name)
                with open(dest_path, "wb") as f:
                    f.write(data)
                return True
    return False


def ensure_ffmpeg():
    """
    Ensure an ffmpeg binary is available.  Search order:
      1. Bundled in <node_pack>/bin/
      2. System PATH
      3. Auto-download a static build from GitHub

    Returns the absolute path to the binary, or None on failure.
    """
    dest = _expected_binary()

    # Already have it
    if os.path.isfile(dest):
        return dest

    # Already on system PATH
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        logger.info(f"[KDNodes] Using system ffmpeg: {system_ffmpeg}")
        return system_ffmpeg

    # Download
    key = (platform.system(), platform.machine())
    url = _FFMPEG_URLS.get(key)
    if url is None:
        logger.warning(
            f"[KDNodes] No ffmpeg download available for {key}. "
            "Install ffmpeg manually and ensure it is on your PATH."
        )
        return None

    os.makedirs(_BIN_DIR, exist_ok=True)
    archive_name = url.rsplit("/", 1)[-1]
    archive_path = os.path.join(_BIN_DIR, archive_name)

    logger.info(f"[KDNodes] Downloading ffmpeg for {key[0]} {key[1]}...")
    try:
        import urllib.request
        urllib.request.urlretrieve(url, archive_path)
    except Exception as e:
        logger.error(f"[KDNodes] Failed to download ffmpeg: {e}")
        return None

    logger.info("[KDNodes] Extracting ffmpeg binary...")
    try:
        if archive_path.endswith(".zip"):
            ok = _extract_ffmpeg_from_zip(archive_path, dest)
        else:
            ok = _extract_ffmpeg_from_tar(archive_path, dest)
    except Exception as e:
        logger.error(f"[KDNodes] Failed to extract ffmpeg: {e}")
        return None
    finally:
        if os.path.exists(archive_path):
            os.remove(archive_path)

    if ok and os.path.isfile(dest):
        logger.info(f"[KDNodes] ffmpeg installed to {dest}")
        return dest

    logger.error("[KDNodes] Could not locate ffmpeg binary inside the archive.")
    return None
