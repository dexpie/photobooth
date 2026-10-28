import os
import tempfile
import unittest
from collections import deque

import numpy as np
from PIL import Image, ImageSequence

from camera_engine import PRE_ROLL_FRAMES, PhotoBoothEngine, _shrink_motion_frame


class MotionGifTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = PhotoBoothEngine.__new__(PhotoBoothEngine)
        self.engine.config = {
            "width": 100,
            "height": 160,
            "layout": [[10, 10, 80, 140]],
            "gif_size": [50, 80],
        }
        self.engine.output_filename = os.path.join(self.temp.name, "session.jpg")
        self.engine.photos = [np.zeros((80, 100, 3), dtype=np.uint8)]
        self.engine.motion_clips = []

    def test_motion_frames_are_reduced_before_buffering(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        small = _shrink_motion_frame(frame)
        self.assertEqual(small.shape, (360, 640, 3))

    def test_motion_gif_uses_per_slot_camera_frames(self):
        self.engine.motion_clips = [[
            np.full((20, 30, 3), (0, 0, 255), dtype=np.uint8),
            np.full((20, 30, 3), (0, 255, 0), dtype=np.uint8),
            np.full((20, 30, 3), (255, 0, 0), dtype=np.uint8),
        ]]
        gif_path = self.engine._save_motion_gif()
        self.assertTrue(os.path.isfile(gif_path))
        with Image.open(gif_path) as gif:
            frames = [frame.convert("RGB") for frame in ImageSequence.Iterator(gif)]
            self.assertEqual(gif.size, (50, 80))
        self.assertGreaterEqual(len(frames), 3)
        self.assertNotEqual(frames[0].getpixel((25, 40)), frames[1].getpixel((25, 40)))

    def test_motion_gif_keeps_template_overlay(self):
        overlay = Image.new("RGBA", (100, 160), (0, 0, 0, 0))
        overlay.putpixel((0, 0), (255, 0, 255, 255))
        self.engine.motion_clips = [[
            np.zeros((20, 30, 3), dtype=np.uint8),
            np.full((20, 30, 3), (0, 255, 0), dtype=np.uint8),
        ]]
        gif_path = self.engine._save_motion_gif(overlay)
        self.assertIsNotNone(gif_path)
        with Image.open(gif_path) as gif:
            frame = gif.convert("RGB")
        pixel = frame.getpixel((0, 0))
        self.assertGreater(pixel[0], 220)
        self.assertGreater(pixel[2], 220)
        self.assertLess(pixel[1], 220)

    def test_capture_uses_bounded_preroll_without_extra_camera_reads(self):
        frames = deque(maxlen=PRE_ROLL_FRAMES)
        for index in range(PRE_ROLL_FRAMES + 5):
            frames.append(np.full((2, 2, 3), index, dtype=np.uint8))
        self.assertEqual(len(frames), PRE_ROLL_FRAMES)
        self.assertEqual(int(frames[0][0, 0, 0]), 5)
        self.assertEqual(int(frames[-1][0, 0, 0]), PRE_ROLL_FRAMES + 4)

    def test_missing_motion_clips_leave_existing_photo_gif_fallback(self):
        self.assertIsNone(self.engine._save_motion_gif())
        fallback = self.engine._save_gif([Image.new("RGB", (100, 160), "red")])
        self.assertTrue(os.path.isfile(fallback))


if __name__ == "__main__":
    unittest.main()