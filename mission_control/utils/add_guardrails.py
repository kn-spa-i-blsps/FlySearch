# Code from: https://github.com/gmum/FlySearch/blob/main/misc/add_guardrails.py
# Small changes applied.
import math
import os

import PIL.Image
from PIL import ImageDraw, ImageFont

# Arducam M12 / Sony IMX477 (1/2.3"), 4056x3040 px, 1.55um pixels, 25mm M12 lens:
# horizontal FOV 14.4 deg, vertical FOV 10.8 deg, diagonal FOV 17.9 deg.
#
# The grid overlay is always drawn on a square-cropped photo (see
# mission_control.utils.image_processing.crop_img_square and the square
# capture size drone_control requests from the camera). Going from the 4:3
# sensor to a square frame crops down to the sensor's full height, so the
# square frame's angular extent on both axes matches the sensor's VERTICAL
# FOV, independent of whatever pixel resolution it's finally scaled to.
# Hence we use 10.8 here, not the horizontal or diagonal figure.
#
# Verify this against your actual capture pipeline with
# scripts/calibrate_grid_scale.py before trusting it for real missions.
DEFAULT_CAMERA_FOV_DEGREES = 10.8


def get_system_font(size: int) -> ImageFont.FreeTypeFont:
    """
    Load font with priority: FONT_LOCATION env var, then NotoSerif-Bold.
    Raises ValueError if neither is available.
    """
    # First priority: Check FONT_LOCATION environment variable
    font_location = os.getenv("FONT_LOCATION")
    if font_location:
        if os.path.exists(font_location):
            try:
                return ImageFont.truetype(font_location, size)
            except OSError as e:
                raise ValueError(
                    f"Cannot load font from FONT_LOCATION '{font_location}': {e}"
                )
        else:
            raise ValueError(
                f"Font file specified in FONT_LOCATION does not exist: {font_location}"
            )

    # Second priority: Try to find NotoSerif-Bold in common locations
    noto_serif_bold_paths = [
        "/usr/share/fonts/google-noto/NotoSerif-Bold.ttf",
        "/usr/share/fonts/noto/NotoSerif-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf",
        "/usr/share/fonts/TTF/NotoSerif-Bold.ttf",
        "/System/Library/Fonts/NotoSerif-Bold.ttf",
        "/Library/Fonts/NotoSerif-Bold.ttf",
        "C:/Windows/Fonts/NotoSerif-Bold.ttf",
    ]

    for font_path in noto_serif_bold_paths:
        if os.path.exists(font_path):
            try:
                return ImageFont.truetype(font_path, size)
            except OSError:
                continue

    # If neither FONT_LOCATION nor NotoSerif-Bold found, raise exception
    raise ValueError(
        "Cannot find required font. Please either:\n"
        "1. Set FONT_LOCATION environment variable to point to a valid font file, or\n"
        "2. Install NotoSerif-Bold font in a standard system location:\n"
        f"   - {', '.join(noto_serif_bold_paths)}"
    )


def carthesian(x, y):
    for el_x in x:
        for el_y in y:
            yield el_x, el_y


def meters_per_pixel(
    width: int, camera_fov_degrees: float, drone_height: float
) -> float:
    """Ground distance (in meters) covered by one pixel of a square image.

    Derived from pinhole geometry: at `drone_height`, the ground footprint
    spanning the full horizontal FOV is `2 * drone_height * tan(fov / 2)`.
    Dividing by image width gives meters per pixel.
    """
    return 2 * drone_height * math.tan(math.radians(camera_fov_degrees / 2)) / width


def compute_grid_points(
    width,
    height,
    w_dots=5,
    h_dots=5,
    camera_fov_degrees=DEFAULT_CAMERA_FOV_DEGREES,
    drone_height=100,
):
    """Compute grid-dot pixel positions and their real-world (x, y) offsets in meters.

    Pure geometry, no image drawing, so the scale math can be checked (or reused
    by a calibration tool) without needing PIL/fonts/an actual photo.

    Returns a list of dicts: {x_px, y_px, x_diff_unit, y_diff_unit}.
    """
    assert width == height

    pixel_per_unit = meters_per_pixel(width, camera_fov_degrees, drone_height)

    pixels_per_cell_w = width / w_dots
    pixels_per_cell_h = height / h_dots

    w_center = w_dots / 2
    h_center = h_dots / 2

    points = []
    for x, y in carthesian(range(1, w_dots), range(1, h_dots)):
        x_diff = x - w_center
        y_diff = h_center - y

        x_diff_px = x_diff * pixels_per_cell_w
        y_diff_px = y_diff * pixels_per_cell_h

        x_diff_unit = int(round(x_diff_px * pixel_per_unit))
        y_diff_unit = int(round(y_diff_px * pixel_per_unit))

        points.append(
            {
                "x_px": x * pixels_per_cell_w,
                "y_px": y * pixels_per_cell_h,
                "x_diff_unit": x_diff_unit,
                "y_diff_unit": y_diff_unit,
            }
        )

    return points


# It doesn't render dots on the edges of the image.
def dot_matrix_two_dimensional_drone(
    img: PIL.Image.Image,
    w_dots=5,
    h_dots=5,
    camera_fov_degrees=DEFAULT_CAMERA_FOV_DEGREES,
    drone_height=100,
):
    def get_opposite_color(pixel_color):
        if pixel_color[0] + pixel_color[1] + pixel_color[2] >= 255 * 3 / 2:
            opposite_color = (0, 0, 0)
        else:
            opposite_color = (255, 255, 255)

        return opposite_color

    width, height = img.size

    points = compute_grid_points(
        width,
        height,
        w_dots=w_dots,
        h_dots=h_dots,
        camera_fov_degrees=camera_fov_degrees,
        drone_height=drone_height,
    )

    # Unit -> unit used inside of Unreal Engine
    # Pixel -> pixel used in the image
    # Cell -> cell in the grid

    if img.mode != "RGB":
        img = img.convert("RGB")
    draw = ImageDraw.Draw(img, "RGB")

    font = get_system_font(
        width // 40
    )  # Adjust font size if needed; default == width // 40

    for point in points:
        x_px, y_px = point["x_px"], point["y_px"]
        x_diff_unit, y_diff_unit = point["x_diff_unit"], point["y_diff_unit"]

        pixel_color = img.getpixel((x_px, y_px))
        opposite_color = get_opposite_color(pixel_color)

        circle_radius = width // 240
        draw.ellipse(
            [
                (x_px - circle_radius, y_px - circle_radius),
                (x_px + circle_radius, y_px + circle_radius),
            ],
            fill=opposite_color,
        )

        text_x_px, text_y_px = x_px + 3, y_px

        draw.text(
            (text_x_px, text_y_px),
            f"({x_diff_unit}, {y_diff_unit})",
            fill=opposite_color,
            font=font,
        )

    return img
