"""
Manual calibration tool: checks whether the meter-distance grid overlay that
mission_control draws on drone photos (mission_control/utils/add_guardrails.py)
actually matches reality for your camera.

The grid math (add_guardrails.meters_per_pixel) assumes a camera_fov_degrees
value (default 10.8 degrees, matching the Arducam M12 / Sony IMX477 rig's
vertical FOV -- see NOTE below) and the drone's altitude at capture time. If
your camera's real FOV of the square capture differs from that assumption,
every distance label the VLM sees is wrong by the same proportion, even
though the math itself is self-consistent.

This does NOT touch a drone or MAVLink. It works entirely from an
already-captured photo of a physical reference (e.g. two marks on a tape
measure, or floor tiles of known size) taken at a known drone/camera height.

Workflow:
  1) Crop & preview the photo the same way the mission pipeline does:
       python3 -m scripts.calibrate_grid_scale --photo shot.jpg --prepare
     -> writes shot.square.png. Open it in any image viewer that shows pixel
        coordinates on hover, and note the pixel of two points whose
        real-world ground distance you know (e.g. two tape-measure marks).

  2) Run the actual calibration:
       python3 -m scripts.calibrate_grid_scale --photo shot.jpg --height 20 \\
           --ref-pixel1 120,340 --ref-pixel2 480,340 --ref-distance 5.0
     -> prints the predicted vs. measured meters-per-pixel scale, the error,
        a suggested camera_fov_degrees, and writes shot.grid_check.png with
        the production grid overlay burned in so you can eyeball it against
        the reference in the photo.

NOTE: mission_control/bridges/vlm_bridge.py passes Config.fov_degrees (read
from the FOV_ANGLE docker env var, default 10.8) through to add_grid(), so
changing FOV_ANGLE in docker/.env is enough to retune this without touching
code. Real-world scale correctness still depends on that number matching
your actual camera's FOV for the square capture -- this tool tells you if it
doesn't.
"""

import argparse
import math
import sys
from pathlib import Path

from mission_control.utils.add_guardrails import (
    dot_matrix_two_dimensional_drone,
    meters_per_pixel,
)
from mission_control.utils.image_processing import crop_img_square


def parse_pixel(value: str) -> tuple[float, float]:
    x_str, y_str = value.split(",")
    return float(x_str), float(y_str)


def load_square_image(photo_path: Path):
    data = photo_path.read_bytes()
    return crop_img_square(data)  # (PIL.Image, side)


def run(args) -> int:
    photo_path = Path(args.photo)
    img, side = load_square_image(photo_path)
    print(
        f"[image] {photo_path} -> cropped to square {side}x{side} (matches mission_control's crop_img_square)"
    )

    if args.prepare:
        out_path = photo_path.with_name(f"{photo_path.stem}.square.png")
        img.save(out_path)
        print(f"[prepare] Wrote {out_path}")
        print(
            "Open it in an image viewer that shows pixel coordinates on hover, "
            "find two points a known real-world distance apart, then re-run with "
            "--height, --ref-pixel1, --ref-pixel2, --ref-distance."
        )
        return 0

    if (
        args.height is None
        or args.ref_pixel1 is None
        or args.ref_pixel2 is None
        or args.ref_distance is None
    ):
        print(
            "[error] --height, --ref-pixel1, --ref-pixel2 and --ref-distance are all required "
            "unless --prepare is passed."
        )
        return 1

    p1 = parse_pixel(args.ref_pixel1)
    p2 = parse_pixel(args.ref_pixel2)
    pixel_dist = math.dist(p1, p2)
    if pixel_dist == 0:
        print("[error] --ref-pixel1 and --ref-pixel2 are identical.")
        return 1

    measured_mpp = args.ref_distance / pixel_dist
    predicted_mpp = meters_per_pixel(side, args.fov, args.height)

    error_pct = (
        (measured_mpp - predicted_mpp) / predicted_mpp * 100
        if predicted_mpp
        else float("inf")
    )
    suggested_fov = 2 * math.degrees(math.atan(measured_mpp * side / (2 * args.height)))

    print(
        f"\nPredicted scale (fov={args.fov} deg, height={args.height}m): {predicted_mpp:.4f} m/px"
    )
    print(
        f"Measured scale (from your {args.ref_distance}m reference over {pixel_dist:.1f}px): "
        f"{measured_mpp:.4f} m/px"
    )
    print(f"Error: {error_pct:+.1f}%")
    print(
        f"Suggested camera_fov_degrees to match reality: {suggested_fov:.1f} "
        "(set FOV_ANGLE in docker/.env, or DEFAULT_CAMERA_FOV_DEGREES in add_guardrails.py, to match)"
    )

    passed = abs(error_pct) <= args.tolerance_pct
    print(f"RESULT: {'PASS' if passed else 'FAIL'} (tolerance={args.tolerance_pct}%)")

    overlay = dot_matrix_two_dimensional_drone(
        img.copy(), camera_fov_degrees=args.fov, drone_height=args.height
    )
    out_path = args.out or str(
        photo_path.with_name(f"{photo_path.stem}.grid_check.png")
    )
    overlay.save(out_path)
    print(
        f"\n[overlay] Wrote {out_path} -- compare the labeled dot distances against your physical reference visually."
    )

    return 0 if passed else 2


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--photo", required=True, help="Path to a photo of a physical ground reference"
    )
    p.add_argument(
        "--prepare",
        action="store_true",
        help="Just crop & save the square image for pixel-picking, then exit",
    )
    p.add_argument(
        "--height",
        type=float,
        help="Drone/camera height above ground when the photo was taken (m)",
    )
    p.add_argument(
        "--fov",
        type=float,
        default=10.8,
        help="Assumed FOV in degrees of the square capture (must match FOV_ANGLE / "
        "add_guardrails.DEFAULT_CAMERA_FOV_DEGREES unless you're testing a hypothesis)",
    )
    p.add_argument(
        "--ref-pixel1",
        help="Pixel coords 'x,y' of first reference point in the cropped square image",
    )
    p.add_argument("--ref-pixel2", help="Pixel coords 'x,y' of second reference point")
    p.add_argument(
        "--ref-distance",
        type=float,
        help="Known real-world ground distance between the two points (m)",
    )
    p.add_argument(
        "--tolerance-pct",
        type=float,
        default=5.0,
        help="Max allowed scale error, percent",
    )
    p.add_argument(
        "--out",
        help="Path to write the annotated overlay image (default: <photo>.grid_check.png)",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
