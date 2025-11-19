#!/usr/bin/env python3
import os
import subprocess
from datetime import datetime
from pathlib import Path

def main():
    DIR = Path(os.environ.get("IMG_DIR", "/img"))
    FNAME = os.environ.get("FNAME") or f"img_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    W = int(os.environ.get("WIDTH", "500"))
    H = int(os.environ.get("HEIGHT", "500"))
    Q = int(os.environ.get("QUALITY", "90"))

    DIR.mkdir(parents=True, exist_ok=True)
    path = DIR / FNAME

    # 1) Próba: Picamera2 (kamera CSI na RPi)
    try:
        from picamera2 import Picamera2  # type: ignore
        cam = Picamera2()
        cfg = cam.create_still_configuration(main={"size": (W, H)})
        cam.configure(cfg)
        cam.start()
        cam.capture_file(str(path))
        cam.stop()
        print(f"Image saved at: {path}")
        return
    except Exception as e:
        print(f"[capture] Picamera2 unavailable/failed: {e}")

    # 2) Fallback: libcamera-jpeg (USB webcam przez V4L2/libcamera)
    cmd = ["libcamera-jpeg", "-o", str(path), "-n", "--width", str(W), "--height", str(H), "-q", str(Q)]
    if CAM_INDEX:
        cmd.extend(["--camera", str(CAM_INDEX)])
    try:
        subprocess.run(cmd, check=True)
        print(f"Image saved at: {path}")
        return
    except FileNotFoundError:
        print("[capture] libcamera-jpeg not found (install libcamera-apps).")
    except subprocess.CalledProcessError as e:
        print(f"[capture] libcamera-jpeg failed (code={e.returncode}).")

    except Exception as e:
        raise SystemExit(f"[capture] All methods failed. Last error: {e}")

if __name__ == "__main__":
    main()