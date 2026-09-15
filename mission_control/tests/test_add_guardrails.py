import unittest

from mission_control.utils.add_guardrails import compute_grid_points, meters_per_pixel


class TestMetersPerPixel(unittest.TestCase):
    """Checks the pinhole-geometry formula the grid overlay uses to turn
    pixel offsets into meter labels. If this math is wrong, every distance
    the VLM is shown on the gridded photo is wrong."""

    def test_known_value_90_degree_fov(self):
        # At 90 degrees FOV, half-FOV is 45 degrees, tan(45) == 1,
        # so ground_width == 2 * drone_height exactly.
        width = 500
        drone_height = 100
        mpp = meters_per_pixel(width, camera_fov_degrees=90, drone_height=drone_height)
        self.assertAlmostEqual(mpp, (2 * drone_height) / width)

    def test_scales_linearly_with_height(self):
        mpp_10 = meters_per_pixel(500, camera_fov_degrees=90, drone_height=10)
        mpp_20 = meters_per_pixel(500, camera_fov_degrees=90, drone_height=20)
        self.assertAlmostEqual(mpp_20, mpp_10 * 2)

    def test_scales_inversely_with_width(self):
        mpp_500 = meters_per_pixel(500, camera_fov_degrees=90, drone_height=50)
        mpp_1000 = meters_per_pixel(1000, camera_fov_degrees=50, drone_height=50)
        mpp_1000_same_fov = meters_per_pixel(
            1000, camera_fov_degrees=90, drone_height=50
        )
        self.assertAlmostEqual(mpp_1000_same_fov, mpp_500 / 2)
        # sanity: differing fov used above isn't silently ignored
        self.assertNotAlmostEqual(mpp_1000, mpp_1000_same_fov)

    def test_wider_fov_covers_more_ground_per_pixel(self):
        narrow = meters_per_pixel(500, camera_fov_degrees=60, drone_height=50)
        wide = meters_per_pixel(500, camera_fov_degrees=120, drone_height=50)
        self.assertGreater(wide, narrow)

    def test_zero_height_means_zero_ground_scale(self):
        self.assertAlmostEqual(
            meters_per_pixel(500, camera_fov_degrees=90, drone_height=0), 0.0
        )


class TestComputeGridPoints(unittest.TestCase):
    """Checks the grid-dot placement and the meter labels attached to them."""

    def test_rejects_non_square_image(self):
        with self.assertRaises(AssertionError):
            compute_grid_points(400, 300)

    def test_point_count_matches_interior_dots(self):
        # dots are placed on interior grid lines only (1..w_dots-1), never on edges
        points = compute_grid_points(500, 500, w_dots=5, h_dots=5)
        self.assertEqual(len(points), 4 * 4)

    def test_center_is_symmetric(self):
        points = compute_grid_points(
            500, 500, w_dots=5, h_dots=5, camera_fov_degrees=90, drone_height=100
        )
        offsets = {(p["x_diff_unit"], p["y_diff_unit"]) for p in points}
        for x, y in offsets:
            self.assertIn((-x, -y), offsets)

    def test_known_label_matches_formula(self):
        width = 500
        drone_height = 100
        fov = 90
        w_dots = h_dots = 5
        points = compute_grid_points(
            width,
            width,
            w_dots=w_dots,
            h_dots=h_dots,
            camera_fov_degrees=fov,
            drone_height=drone_height,
        )

        mpp = meters_per_pixel(width, fov, drone_height)
        pixels_per_cell = width / w_dots
        center = w_dots / 2

        # leftmost interior column, x=1
        expected_x_unit = round((1 - center) * pixels_per_cell * mpp)
        col1 = [p for p in points if p["x_px"] == pixels_per_cell]
        self.assertTrue(col1)
        for p in col1:
            self.assertEqual(p["x_diff_unit"], expected_x_unit)

    def test_doubling_height_doubles_labeled_distances(self):
        points_h10 = compute_grid_points(
            500, 500, camera_fov_degrees=90, drone_height=10
        )
        points_h20 = compute_grid_points(
            500, 500, camera_fov_degrees=90, drone_height=20
        )

        for p10, p20 in zip(points_h10, points_h20):
            self.assertEqual(p10["x_px"], p20["x_px"])
            self.assertEqual(p10["y_px"], p20["y_px"])
            # rounding to the nearest meter can be off-by-one at small magnitudes
            self.assertLessEqual(abs(p20["x_diff_unit"] - 2 * p10["x_diff_unit"]), 1)
            self.assertLessEqual(abs(p20["y_diff_unit"] - 2 * p10["y_diff_unit"]), 1)


if __name__ == "__main__":
    unittest.main()
