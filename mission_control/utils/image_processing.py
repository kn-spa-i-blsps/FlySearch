import io

from PIL import Image

import add_guardrails as gd


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

def add_grid(photo_path, drone_height):
    """ Adds grid to the image.

    That grid shows how many meters drone have to move to be above that point.
    """

    img = Image.open(photo_path)
    img_grid = gd.dot_matrix_two_dimensional_drone(
        img=img,
        drone_height=drone_height
    )
    # It might seem redundant, but without it while sending
    # original photo from the file is taken (Python optimization)
    img_grid.save("tmp.png")
    return Image.open("tmp.png")