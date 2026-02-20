import subprocess
from pathlib import Path


def _make_square(path: Path, quality: int = 90) -> None:
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        print("[square] Pillow not installed; leaving image as-is.")
        return

    img = Image.open(path)
    width, height = img.size

    if width == height:
        print(f"[square] Image already square: {width}x{height}")
        return

    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    right = left + side
    bottom = top + side

    cropped = img.crop((left, top, right, bottom))
    cropped.save(path, quality=quality)
    print(f"[square] Cropped to square: {side}x{side}")


def capture_photo(
    *,
    destination: Path,
    width: int,
    height: int,
    quality: int,
    video_device: str,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)

    # 1) Picamera2 (CSI camera)
    try:
        from picamera2 import Picamera2  # type: ignore

        cam = Picamera2()
        cfg = cam.create_still_configuration(main={"size": (width, height)})
        cam.configure(cfg)
        cam.start()
        cam.capture_file(str(destination))
        cam.stop()
        print(f"Image saved at: {destination} (Picamera2)")
        return
    except Exception as exc:
        print(f"[capture] Picamera2 unavailable/failed: {exc}")

    # 2) fswebcam (USB V4L2 camera)
    cmd = [
        "fswebcam",
        "-d",
        video_device,
        "-r",
        f"{width}x{height}",
        "-S",
        "10",
        "--no-banner",
        str(destination),
    ]

    print(f"[capture] Running fallback: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
        print(f"Image saved at: {destination} (fswebcam)")
        _make_square(destination, quality)
    except FileNotFoundError as exc:
        raise RuntimeError("[capture] ERROR: fswebcam not found. Install: sudo apt install fswebcam") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"[capture] fswebcam failed (exit code={exc.returncode})") from exc
