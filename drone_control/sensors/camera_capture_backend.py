import subprocess
from threading import Lock
from pathlib import Path
from typing import Any


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


_CAMERA_LOCK = Lock()
_CAMERA_STATE: dict[str, Any] = {
    "camera": None,
    "recording": False,
    "video_path": None,
}


def _release_camera(camera: Any) -> None:
    if camera is None:
        return

    try:
        camera.stop_recording()
    except Exception:
        pass

    try:
        camera.stop()
    except Exception:
        pass

    close_fn = getattr(camera, "close", None)
    if callable(close_fn):
        try:
            close_fn()
        except Exception:
            pass


def recording_status() -> dict[str, object]:
    with _CAMERA_LOCK:
        return {
            "recording": bool(_CAMERA_STATE["recording"]),
            "path": str(_CAMERA_STATE["video_path"]) if _CAMERA_STATE["video_path"] else None,
        }


def start_video_recording(
    *,
    destination: Path,
    width: int,
    height: int,
    bitrate: int = 10_000_000,
) -> dict[str, object]:
    destination.parent.mkdir(parents=True, exist_ok=True)

    with _CAMERA_LOCK:
        if _CAMERA_STATE["recording"]:
            return {
                "recording": True,
                "path": str(_CAMERA_STATE["video_path"]) if _CAMERA_STATE["video_path"] else None,
            }

        try:
            from picamera2 import Picamera2  # type: ignore
            from picamera2.encoders import H264Encoder  # type: ignore
            from picamera2.outputs import FileOutput  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"[recording] Picamera2 unavailable: {exc}") from exc

        camera = None
        try:
            camera = Picamera2()

            lores_side = max(64, min(int(width), int(height)))
            cfg = camera.create_video_configuration(
                main={"size": (int(width), int(height)), "format": "RGB888"},
                lores={"size": (lores_side, lores_side), "format": "RGB888"},
                buffer_count=2,
                queue=False,
            )
            camera.configure(cfg)
            camera.start()

            encoder = H264Encoder(bitrate=int(bitrate))
            output = FileOutput(str(destination))
            camera.start_recording(encoder, output)

            _CAMERA_STATE["camera"] = camera
            _CAMERA_STATE["recording"] = True
            _CAMERA_STATE["video_path"] = destination
            print(f"[recording] Started: {destination}")
            return {"recording": True, "path": str(destination)}
        except Exception as exc:
            _release_camera(camera)
            _CAMERA_STATE["camera"] = None
            _CAMERA_STATE["recording"] = False
            _CAMERA_STATE["video_path"] = None
            raise RuntimeError(f"[recording] Failed to start recording: {exc}") from exc


def stop_video_recording() -> dict[str, object]:
    with _CAMERA_LOCK:
        camera = _CAMERA_STATE["camera"]
        if camera is None:
            return {"recording": False, "path": None}

        path = _CAMERA_STATE["video_path"]
        _release_camera(camera)
        _CAMERA_STATE["camera"] = None
        _CAMERA_STATE["recording"] = False
        _CAMERA_STATE["video_path"] = None
        print(f"[recording] Stopped: {path}")
        return {"recording": False, "path": str(path) if path else None}


def capture_photo(
    *,
    destination: Path,
    width: int,
    height: int,
    quality: int,
    video_device: str,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)

    # If recording is active, reuse the same Picamera2 session.
    if _CAMERA_STATE["recording"]:
        with _CAMERA_LOCK:
            active_camera = _CAMERA_STATE["camera"] if _CAMERA_STATE["recording"] else None
            if active_camera is not None:
                try:
                    active_camera.capture_file(str(destination), name="lores")
                    _validate_captured_image(destination)
                    _make_square(destination, quality)
                    _validate_captured_image(destination)
                    print(f"Image saved at: {destination} (shared Picamera2)")
                    return
                except Exception as exc:
                    print(f"[capture] Shared Picamera2 capture failed: {exc}")

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


def capture_video(
    *,
    destination: Path,
    width: int,
    height: int,
    quality: int,
    video_device: str,
) -> None:
    # Backward-compatible wrapper.
    _ = quality
    _ = video_device
    start_video_recording(destination=destination, width=width, height=height)
