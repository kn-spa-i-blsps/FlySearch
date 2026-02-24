import subprocess
from pathlib import Path


def _validate_captured_image(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"[capture] Capture output file does not exist: {path}")
    if path.stat().st_size == 0:
        raise RuntimeError(f"[capture] Capture output file is empty: {path}")

    try:
        from PIL import Image  # type: ignore
    except ImportError:
        with path.open("rb") as f:
            if f.read(2) != b"\xff\xd8":
                raise RuntimeError(f"[capture] Capture output is not a JPEG file: {path}")
        return

    try:
        with Image.open(path) as img:
            img.load()
    except Exception as exc:
        raise RuntimeError(f"[capture] Capture output is not a readable image: {path} ({exc})") from exc


def _make_square(path: Path, quality: int = 90) -> None:
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        print("[square] Pillow not installed; leaving image as-is.")
        return

    with Image.open(path) as img:
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
        cropped.save(path, format="JPEG", quality=quality)
        cropped.close()
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

        cam = None
        try:
            cam = Picamera2()
            cfg = cam.create_still_configuration(main={"size": (width, height)})
            cam.configure(cfg)
            cam.start()
            cam.capture_file(str(destination))
        finally:
            if cam is not None:
                try:
                    cam.stop()
                except Exception:
                    pass
                close_fn = getattr(cam, "close", None)
                if callable(close_fn):
                    try:
                        close_fn()
                    except Exception:
                        pass

        _validate_captured_image(destination)
        _make_square(destination, quality)
        _validate_captured_image(destination)
        print(f"Image saved at: {destination} (Picamera2)")
        return
    except Exception as exc:
        print(f"[capture] Picamera2 unavailable/failed: {exc}")

    try:
        destination.unlink(missing_ok=True)
    except Exception:
        pass

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
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if stdout:
            print(stdout)
        if stderr:
            print(stderr)

        if result.returncode != 0:
            output = "\n".join([x for x in [stdout, stderr] if x]).strip()
            if output:
                raise RuntimeError(
                    f"[capture] fswebcam failed (exit code={result.returncode}): {output}"
                )
            raise RuntimeError(f"[capture] fswebcam failed (exit code={result.returncode})")

        _validate_captured_image(destination)
        _make_square(destination, quality)
        _validate_captured_image(destination)
        print(f"Image saved at: {destination} (fswebcam)")
    except FileNotFoundError as exc:
        raise RuntimeError("[capture] ERROR: fswebcam not found. Install: sudo apt install fswebcam") from exc
