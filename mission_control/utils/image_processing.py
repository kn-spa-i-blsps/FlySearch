import asyncio
import io

from PIL import Image

from mission_control.utils import add_guardrails as gd

TRAINING_IMAGE_SIZE = 500


def crop_img_square(photo_data):
    """Crops the image into square of size of shorter side."""

    img = Image.open(io.BytesIO(photo_data))
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    right = left + side
    bottom = top + side

    return img.crop((left, top, right, bottom)), side


def _add_grid_sync_copy(photo_path, drone_height, fov_degrees=10.8):
    with Image.open(photo_path) as img:
        # Image size and dot grid as in training (image_size=500, scaffold w_dots=h_dots=6).
        img = img.convert("RGB").resize((TRAINING_IMAGE_SIZE, TRAINING_IMAGE_SIZE), Image.LANCZOS)
        img_grid = gd.dot_matrix_two_dimensional_drone(
            img=img,
            w_dots=6,
            h_dots=6,
            drone_height=drone_height,
            camera_fov_degrees=fov_degrees,
        )

        clean_img = img_grid.copy()

    return clean_img


async def add_grid_async(photo_path, drone_height, fov_degrees=10.8):
    return await asyncio.to_thread(_add_grid_sync_copy, photo_path, drone_height, fov_degrees)
