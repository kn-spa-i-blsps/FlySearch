import io

from mission_control.utils import add_guardrails as gd


def crop_img_square(photo_data):
    """Crops the image into square of size of shorter side. """

    img = Image.open(io.BytesIO(photo_data))
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    right = left + side
    bottom = top + side

    return img.crop((left, top, right, bottom)), side


import asyncio
from PIL import Image


def _add_grid_sync_copy(photo_path, drone_height):
    with Image.open(photo_path) as img:
        img_grid = gd.dot_matrix_two_dimensional_drone(
            img=img,
            drone_height=drone_height
        )

        clean_img = img_grid.copy()

    return clean_img


async def add_grid_async(photo_path, drone_height):
    return await asyncio.to_thread(_add_grid_sync_copy, photo_path, drone_height)
