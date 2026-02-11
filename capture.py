#!/usr/bin/env python3
import io, os, subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from PIL import Image  # Pillow must be installed
except ImportError:
    Image = None
    print("[square] Pillow not installed; leaving image as-is.")

def _make_square_image(img: "Image.Image") -> "Image.Image":
    """Crop image at 'path' to a centered square (in-place)."""
    w, h = img.size
    if w == h:
        print(f"[square] Image already cropped to square: {w}x{w}")
        return img
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    right = left + side
    bottom = top  + side
    print(f"[square] Cropped to square: {side}x{side}")
    return img.crop((left, top, right, bottom))

def _encode_pil_jpeg(img: "Image.Image", quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()

def _capture_picamera_bytes(picam2, width: int, height: int, quality: int, square: bool) -> Optional[bytes]:

    try:
        frame = picam2.capture_array("main")
        if Image is None:
            raise RuntimeError("Pillow required to encode Picamera2 frame to JPEG")
        img = Image.fromarray(frame)
        if square:
            img = _make_square_image(img)
        return _encode_pil_jpeg(img, quality)
    except Exception as e:
        print(f"[capture] Picamera2 capture failed: {e}")
        return None

def _capture_fswebcam_bytes(width: int, height: int, quality: int, video_dev: str, square: bool) -> bytes:
    cmd = [
        "fswebcam",
        "-d", video_dev,
        "-r", f"{width}x{height}",
        "-S", "10",
        "--no-banner",
        "--jpeg", str(quality),
        "--stdout",
    ]
    print(f"[capture] Running fswebcam: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=True, capture_output=True)
    data = result.stdout
    if square and Image is not None:
        try:
            img = Image.open(io.BytesIO(data))
            img = _make_square_image(img)
            data = _encode_pil_jpeg(img, quality)
        except Exception as e:
            print(f"[capture] square crop skipped (fswebcam): {e}")
    return data

def capture_bytes(width: int, height: int, quality: int = 90, video_dev: str = "/dev/video0", square: bool = True) -> bytes:
    """
    Capture a JPEG and return it as bytes. Tries Picamera2 first, then fswebcam.
    If Pillow is missing, square crop is skipped.
    """
    if square and Image is None:
        print("[capture] Pillow not installed; skipping square crop.")
        square = False

    data = _capture_picamera_bytes(width, height, quality, square)
    if data is not None:
        return data

    try:
        return _capture_fswebcam_bytes(width, height, quality, video_dev, square)
    except FileNotFoundError:
        raise SystemExit("[capture] ERROR: fswebcam not found. Install: sudo apt install fswebcam")
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"[capture] fswebcam failed (exit code={e.returncode})")

def main():
    """When called directly, capture.py will save the photo under DIR / FNAME.
    By default, when imported in producer_server.py, photo is sent directly to server
    and is not saved on RaspberryPi.
    """

    # Env variables → can be overridden by Docker/WS client
    DIR = Path(os.environ.get("IMG_DIR", "/img"))
    FNAME = os.environ.get("FNAME") or f"img_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    W = int(os.environ.get("WIDTH", "640"))
    H = int(os.environ.get("HEIGHT", "480"))
    Q = int(os.environ.get("QUALITY", "90"))
    VIDEO_DEV = os.environ.get("VIDEO_DEVICE", "/dev/video0")

    DIR.mkdir(parents=True, exist_ok=True)
    path = DIR / FNAME

    data = capture_bytes(width=W, height=H, quality=Q, video_dev=VIDEO_DEV, square=True)
    with path.open("wb") as f:
        f.write(data)
    print(f"Image saved at: {path}")

if __name__ == "__main__":
    main()