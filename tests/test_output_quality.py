import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from app import App
from camera_engine import PhotoBoothEngine
from meme_manager import MemeManager, STICKERS


class OutputQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = PhotoBoothEngine.__new__(PhotoBoothEngine)
        self.engine.config = {
            "width": 60,
            "height": 90,
            "layout": [[0, 0, 60, 90]],
        }
        self.engine.output_filename = os.path.join(self.temp.name, "photo.jpg")

    def test_gif_size_uses_template_config(self):
        self.engine.config["gif_size"] = [30, 45]
        self.assertEqual(self.engine._get_gif_size(), (30, 45))

    def test_gif_size_falls_back_for_missing_or_invalid_config(self):
        for value in (None, "480,720", [0, 720], [480, -1], [480, 720.5], [True, 720], [480]):
            with self.subTest(value=value):
                self.engine.config.pop("gif_size", None)
                if value is not None:
                    self.engine.config["gif_size"] = value
                self.assertEqual(self.engine._get_gif_size(), (480, 720))

    def test_explicit_gif_size_overrides_template_config(self):
        self.engine.config["gif_size"] = [30, 45]
        self.assertEqual(self.engine._get_gif_size((12, 18)), (12, 18))

    def test_save_gif_uses_configured_size(self):
        self.engine.config["gif_size"] = [30, 45]
        gif_path = self.engine._save_gif([Image.new("RGB", (60, 90), "red")])
        with Image.open(gif_path) as gif:
            self.assertEqual(gif.size, (30, 45))

    def test_process_saves_jpeg_with_high_quality_and_no_subsampling(self):
        self.engine.photos = [np.zeros((90, 60, 3), dtype=np.uint8)]
        with patch.object(self.engine, "get_photo_preview", return_value=Image.new("RGB", (60, 90))), patch.object(
            self.engine, "_save_gif", return_value=None
        ), patch.object(Image.Image, "save", autospec=True) as save:
            self.engine.process()
        self.assertEqual(save.call_args.kwargs["quality"], 95)
        self.assertEqual(save.call_args.kwargs["subsampling"], 0)

    def test_missing_sticker_assets_are_skipped_when_cycling(self):
        manager = MemeManager.__new__(MemeManager)
        manager._sticker_images = {
            "glasses": object(), "hat": None, "mustache": None,
            "cat_ears": None, "meme": object(),
        }
        manager._available_sticker_indices = [
            index for index, sticker in enumerate(STICKERS)
            if manager._sticker_images.get(sticker["key"]) is not None
        ]
        manager.sticker_idx = manager._available_sticker_indices[0]
        manager.meme_mode = True

        manager.cycle_sticker()
        self.assertEqual(manager.current_sticker_name(), "meme")
        manager.cycle_sticker()
        self.assertEqual(manager.current_sticker_name(), "glasses")
        self.assertIsNotNone(manager.get_current_sticker())

    def test_sticker_mode_stays_off_when_no_assets_available(self):
        manager = MemeManager.__new__(MemeManager)
        manager._sticker_images = {sticker["key"]: None for sticker in STICKERS}
        manager._available_sticker_indices = []
        manager.sticker_idx = 0
        manager.meme_mode = False
        manager.toggle_mode()
        self.assertFalse(manager.meme_mode)
        manager.cycle_sticker()
        self.assertEqual(manager.current_sticker_name(), "NONE")

    def test_capture_session_filter_is_copied_back_to_live_preview(self):
        label = type("LabelStub", (), {"configure": lambda self, **kwargs: setattr(self, "text", kwargs["text"])})()
        app = type("AppStub", (), {
            "live_cam": type("LiveCamStub", (), {"current_filter": 0, "_filter_label": label})()
        })()

        App._set_live_filter(app, 3)
        self.assertEqual(app.live_cam.current_filter, 3)
        self.assertIn("BEAUTY", label.text)

    def test_template_loader_includes_about_me(self):
        app = type("AppStub", (), {})()
        app._load_templates = App._load_templates.__get__(app)
        templates = app._load_templates()
        about_me = next(template for template in templates if template["name"] == "About Me")
        self.assertEqual(about_me["photo_count"], 1)
        self.assertEqual(about_me["width"], 1080)
        self.assertEqual(about_me["height"], 1350)
        self.assertEqual(about_me["layout"][0], [368, 499, 344, 433])
        self.assertTrue(os.path.isfile(about_me["overlay_full_path"]))

    def test_app_session_copies_capture_filter_back_to_live_preview(self):
        app = type("AppStub", (), {
            "_session_cancel": type("CancelEvent", (), {"is_set": lambda self: False})(),
            "_run_on_ui": lambda self, callback, *args: callback(*args),
            "_set_live_filter": lambda self, value: setattr(self, "restored_filter", value),
            "_finish_session": lambda self: None,
            "_show_preview": lambda self, *args: setattr(self, "preview_args", args),
        })()
        class EngineStub:
            filter_idx = 0

            def capture(self):
                self.filter_idx = 3
                return True

            def process(self):
                return "photo.jpg", "photo.gif"

        engine = EngineStub()
        with patch("app.PhotoBoothEngine", return_value=engine):
            App._run_session(app, {}, 0, 4)

        self.assertEqual(app.restored_filter, 3)
        self.assertIs(app.preview_args[0], engine)

    def test_jpg_and_gif_are_saved_at_template_canvas_size(self):
        self.engine.photos = [np.zeros((90, 60, 3), dtype=np.uint8)]
        self.engine.config["gif_size"] = [30, 45]
        self.engine.config["overlay_full_path"] = None
        with patch.object(self.engine, "get_photo_preview", return_value=Image.new("RGB", (60, 90), "red")):
            jpg_path, gif_path = self.engine.process()
        with Image.open(jpg_path) as jpg, Image.open(gif_path) as gif:
            self.assertEqual(jpg.size, (60, 90))
            self.assertEqual(gif.size, (30, 45))
            # 4:4:4 tanpa chroma subsampling memakai tabel kuantisasi untuk luma dan kroma.
            self.assertGreaterEqual(len(jpg.quantization), 2)
        self.assertEqual(os.path.dirname(jpg_path), self.temp.name)
        self.assertEqual(os.path.splitext(gif_path)[1], ".gif")
        with open(jpg_path, "rb") as saved_jpg:
            self.assertEqual(saved_jpg.read(3), b"\xff\xd8\xff")


if __name__ == "__main__":
    unittest.main()
