"""Pure full-frame coordinate checks; no device or gameplay."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/python'))
import recognition


class FrameGeometryTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(recognition, 'map_frame_boxes'), 'Full-frame mapping is required')

    def box(self, x, y, width, height, label='book', confidence=.8):
        return dict(label=label, value=confidence, x=x, y=y, width=width, height=height)

    def test_current_12_by_9_view_maps_both_teams_without_stretching(self):
        layout = recognition.frame_layout(1440, 1080)
        self.assertEqual((layout['resized_width'], layout['resized_height'], layout['pad_y']), (416, 312, 52))
        boxes = recognition.map_frame_boxes([self.box(52, 104, 104, 104), self.box(260, 104, 104, 104)], 1440, 1080)
        for box, left, team in zip(boxes, (180, 900), ('red', 'blue')):
            self.assertEqual((box['x'], box['y'], box['width'], box['height'], box['team']), (left, 180, 360, 360, team))

    def test_complete_frame_keeps_every_edge_and_removes_padding(self):
        mapped, = recognition.map_frame_boxes([self.box(0, 0, 416, 416)], 1920, 1080)
        self.assertEqual((mapped['x'], mapped['y'], mapped['width'], mapped['height']), (0, 0, 1920, 1080))

    def test_known_objects_map_back_and_a_centre_object_is_not_split(self):
        # Scale is 13/60, with 91 pixels of top padding.
        cases = [(self.box(26, 130, 52, 65), dict(x=120, y=180, width=240, height=300), 'red'),
                 (self.box(234, 130, 52, 65), dict(x=1080, y=180, width=240, height=300), 'blue'),
                 (self.box(195, 130, 52, 65), dict(x=900, y=180, width=240, height=300), 'blue')]
        for box, expected_box, team in cases:
            mapped, = recognition.map_frame_boxes([box], 1920, 1080)
            for key, expected in expected_box.items():
                self.assertAlmostEqual(mapped[key], expected)
            self.assertEqual(mapped['team'], team)

    def test_padding_only_boxes_and_invalid_coordinates_are_discarded(self):
        boxes = [self.box(0, 0, 100, 91), self.box(0, 325, 100, 91),
                 self.box(float('nan'), 100, 50, 50), self.box(0, 100, 50, 50, confidence=.49)]
        self.assertEqual(recognition.map_frame_boxes(boxes, 1920, 1080), [])

    def test_guidance_uses_full_frame_area_and_clears_each_half_independently(self):
        # 960 x 120 = 5.56% of the full frame; 960 x 100 = 4.63%.
        large = self.box(0, 91, 208, 26)
        small = self.box(208, 91, 208, 65 / 3)
        selected = recognition.select_recognition(recognition.map_frame_boxes([large, small], 1920, 1080), 1920, 1080)
        self.assertEqual(selected['red']['label'], 'book')
        self.assertIsNone(selected['blue'])
        selected = recognition.select_recognition(recognition.map_frame_boxes([{**large, 'x': 208}], 1920, 1080), 1920, 1080)
        self.assertIsNone(selected['red'])
        self.assertEqual(selected['blue']['box'][0], 960)


if __name__ == '__main__':
    unittest.main()
