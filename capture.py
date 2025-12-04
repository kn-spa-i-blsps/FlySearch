#!/usr/bin/env python3
import os
import subprocess
from datetime import datetime
from pathlib import Path

def make_square(path: Path, quality: int = 90) -> None:
    """Crop image at 'path' to a centered square (in-place)."""
    try:
        from PIL import Image  # Pillow must be installed
    except ImportError:
        print("[square] Pillow not installed; leaving image as-is.")
        return

    img = Image.open(path)
    w, h = img.size

    if w == h:
        print(f"[square] Image already square: {w}x{h}")
        return

    side = min(w, h)
    left   = (w - side) // 2
    top    = (h - side) // 2
    right  = left + side
    bottom = top  + side

    img_cropped = img.crop((left, top, right, bottom))
    img_cropped.save(path, quality=quality)
    print(f"[square] Cropped to square: {side}x{side}")

def main():
    # Env variables → can be overridden by Docker/WS client
    DIR = Path(os.environ.get("IMG_DIR", "/img"))
    FNAME = os.environ.get("FNAME") or f"img_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    W = int(os.environ.get("WIDTH", "640"))     # dopasowane do testu
    H = int(os.environ.get("HEIGHT", "480"))
    Q = int(os.environ.get("QUALITY", "90"))

    DIR.mkdir(parents=True, exist_ok=True)
    path = DIR / FNAME

    # 1) Attempt Picamera2 (CSI camera)
    try:
        from picamera2 import Picamera2  # type: ignore
        cam = Picamera2()
        cfg = cam.create_still_configuration(main={"size": (W, H)})
        cam.configure(cfg)
        cam.start()
        cam.capture_file(str(path))
        cam.stop()
        print(f"Image saved at: {path} (Picamera2)")
        return
    except Exception as e:
        print(f"[capture] Picamera2 unavailable/failed: {e}")

    # 2) Fallback: fswebcam (USB V4L2 camera) — WORKING CONFIG
    video_dev = os.environ.get("VIDEO_DEVICE", "/dev/video0")

    cmd = [
        "fswebcam",
        "-d", video_dev,
        "-r", f"{W}x{H}",
        "-S", "10",           # skip 10 frames → fixes black images
        "--no-banner",
        str(path),
    ]

    print(f"[capture] Running fallback: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
        print(f"Image saved at: {path} (fswebcam)")
        make_square(path, Q)
        return
    except FileNotFoundError:
        raise SystemExit("[capture] ERROR: fswebcam not found. Install: sudo apt install fswebcam")
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"[capture] fswebcam failed (exit code={e.returncode})")

if __name__ == "__main__":
    main()