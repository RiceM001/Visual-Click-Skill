import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np


SCRIPT = Path(__file__).resolve().parents[1] / "skills/visual-target-check/scripts/locate.py"
spec = importlib.util.spec_from_file_location("locate", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class LocateTests(unittest.TestCase):
    def test_roi_origin_and_scale(self):
        rng = np.random.default_rng(7)
        screenshot = rng.integers(0, 256, (100, 120), dtype=np.uint8)
        template = screenshot[35:45, 50:62].copy()
        config = {"roi": [40, 20, 40, 40], "screen_origin": [-1920, 100],
                  "physical_pixels_per_image_pixel": 1.25, "threshold": 0.99}
        result = module.locate(screenshot, template, config)
        self.assertTrue(result["matched"])
        self.assertEqual(result["image_center"], [56.0, 40.0])
        self.assertEqual(result["screen_center"], [-1850.0, 150.0])

    def test_invalid_roi(self):
        screenshot = np.zeros((20, 20), dtype=np.uint8)
        template = np.ones((4, 4), dtype=np.uint8)
        config = {"roi": [10, 10, 20, 20], "screen_origin": [0, 0],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0.9}
        with self.assertRaisesRegex(ValueError, "outside screenshot"):
            module.locate(screenshot, template, config)

    def test_live_dry_run_uses_roi_and_never_clicks(self):
        rng = np.random.default_rng(8)
        gray = rng.integers(0, 256, (40, 40), dtype=np.uint8)
        bgra = np.stack([gray, gray, gray, np.full_like(gray, 255)], axis=2)
        template = gray[12:20, 15:25].copy()
        config = {"roi": [10, 10, 40, 40], "screen_origin": [-1920, 0],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0.99,
                  "stable_frames": 2, "frame_interval_ms": 0, "max_center_shift_px": 0}

        class FakeMss:
            monitors = [None, SimpleNamespace(left=-1920, top=0, width=1920, height=1080)]

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def grab(self, region):
                self_region = {"left": -1910, "top": 10, "width": 40, "height": 40}
                assert region == self_region
                return bgra

        with (mock.patch.dict(sys.modules, {"mss": SimpleNamespace(MSS=FakeMss)}),
              mock.patch.object(module, "set_dpi_awareness"),
              mock.patch.object(module, "send_click") as click):
            result = module.run_live(template, config, execute=False)
        self.assertTrue(result["stable"])
        self.assertFalse(result["clicked"])
        self.assertEqual(result["screen_center"], [-1890.0, 26.0])
        click.assert_not_called()

    def test_unstable_live_match_blocks_execute(self):
        template = np.random.default_rng(4).integers(0, 256, (8, 8), dtype=np.uint8)
        frames = []
        for x in (10, 20):
            gray = np.zeros((40, 40), dtype=np.uint8)
            gray[10:18, x:x + 8] = template
            frames.append(np.stack([gray, gray, gray, np.full_like(gray, 255)], axis=2))
        config = {"roi": [0, 0, 40, 40], "screen_origin": [0, 0],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0.99,
                  "stable_frames": 2, "frame_interval_ms": 0, "max_center_shift_px": 3}

        class FakeMss:
            monitors = [None, {"left": 0, "top": 0, "width": 100, "height": 100}]

            def __enter__(self):
                self.frames = iter(frames)
                return self

            def __exit__(self, *_):
                return False

            def grab(self, _):
                return next(self.frames)

        with (mock.patch.object(module.sys, "platform", "win32"),
              mock.patch.dict(sys.modules, {"mss": SimpleNamespace(MSS=FakeMss)}),
              mock.patch.object(module, "set_dpi_awareness"),
              mock.patch.object(module, "execute_click") as click):
            result = module.run_live(template, config, execute=True)
        self.assertFalse(result["stable"])
        self.assertFalse(result["clicked"])
        click.assert_not_called()

    def test_execute_checks_window_before_click(self):
        result = {"matched": True, "stable": True, "screen_center": [-1850, 150]}
        config = {"expected_window_title": "Notepad", "click_offset": [2, -3]}
        monitors = [None, SimpleNamespace(left=-1920, top=0, width=1920, height=1080)]
        with (mock.patch.object(module, "foreground_window", return_value=("Notes", (-1900, 100, -1700, 300))),
              mock.patch.object(module, "send_click") as click):
            with self.assertRaisesRegex(ValueError, "expected foreground"):
                module.execute_click(result, config, monitors)
            click.assert_not_called()
        with (mock.patch.object(module, "foreground_window", return_value=("Untitled - Notepad", (-1900, 100, -1700, 300))),
              mock.patch.object(module, "send_click") as click):
            self.assertEqual(module.execute_click(result, config, monitors), [-1848, 147])
            click.assert_called_once_with(-1848, 147)
        with (mock.patch.object(module, "foreground_window", return_value=("Untitled - Notepad", (-1900, 100, -1700, 300))),
              mock.patch.object(module, "send_click") as click):
            self.assertEqual(module.execute_click(result, config,
                             [None, {"left": -1920, "top": 0, "width": 1920, "height": 1080}]),
                             [-1848, 147])
            click.assert_called_once_with(-1848, 147)

    def test_evidence_contains_only_roi(self):
        screenshot = np.random.default_rng(3).integers(0, 256, (80, 90), dtype=np.uint8)
        template = screenshot[30:40, 40:50].copy()
        config = {"roi": [20, 20, 50, 40], "screen_origin": [0, 0],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0.9}
        result = module.locate(screenshot, template, config)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.png"
            module.save_evidence(screenshot, result, config, path)
            image = module.cv2.imread(str(path))
            self.assertEqual(image.shape[:2], (40, 50))

    def test_ocr_word_boxes_map_to_physical_screen(self):
        def box(x1, x2):
            return [[x1, 20], [x2, 20], [x2, 60], [x1, 60]]

        output = SimpleNamespace(txts=("保存设置",), scores=(0.95,), boxes=[box(40, 200)],
                                 word_results=((('保', 0.98, box(40, 80)),
                                                ('存', 0.97, box(80, 120)),
                                                ('设', 0.96, box(120, 160)),
                                                ('置', 0.94, box(160, 200))),))
        engine = mock.Mock(return_value=output)
        config = {"roi": [10, 10, 200, 100], "screen_origin": [-1920, 100],
                  "physical_pixels_per_image_pixel": 1.25, "threshold": 0.9,
                  "ocr_threshold": 0.75, "ocr_occurrence": None,
                  "ocr_preprocess": ["upscale"], "ocr_scale": 2,
                  "ocr_adaptive_block_size": 31, "ocr_adaptive_c": 11}
        result = module.locate_text(np.zeros((150, 230), dtype=np.uint8), "设置", config, engine)
        self.assertTrue(result["matched"])
        self.assertEqual(result["image_bbox"], [70.0, 20.0, 40.0, 20.0])
        self.assertEqual(result["screen_center"], [-1807.5, 137.5])
        self.assertEqual(engine.call_args.args[0].shape[:2], (200, 400))

    def test_ocr_duplicate_text_requires_occurrence(self):
        def box(top):
            return [[10, top], [70, top], [70, top + 20], [10, top + 20]]

        output = SimpleNamespace(txts=("确认", "确认"), scores=(0.95, 0.94),
                                 boxes=[box(10), box(50)], word_results=(None, None))
        engine = mock.Mock(return_value=output)
        config = {"roi": [0, 0, 100, 100], "screen_origin": [0, 0],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0.9,
                  "ocr_threshold": 0.7, "ocr_occurrence": None,
                  "ocr_preprocess": ["raw"], "ocr_scale": 2,
                  "ocr_adaptive_block_size": 31, "ocr_adaptive_c": 11}
        screenshot = np.zeros((100, 100), dtype=np.uint8)
        ambiguous = module.locate_text(screenshot, "确认", config, engine)
        self.assertFalse(ambiguous["matched"])
        self.assertTrue(ambiguous["ambiguous"])
        with self.assertRaisesRegex(ValueError, "not matched"):
            module.execute_click({**ambiguous, "stable": True},
                                 {"expected_window_title": "App", "click_offset": [0, 0]}, [])
        config["ocr_occurrence"] = 2
        selected = module.locate_text(screenshot, "确认", config, engine)
        self.assertTrue(selected["matched"])
        self.assertEqual(selected["image_center"], [40.0, 60.0])

    def test_ocr_preprocess_fallback(self):
        def box():
            return [[20, 20], [100, 20], [100, 60], [20, 60]]

        empty = SimpleNamespace(txts=(), scores=(), boxes=(), word_results=())
        found = SimpleNamespace(txts=("SAVE",), scores=(0.95,), boxes=[box()],
                                word_results=(None,))
        engine = mock.Mock(side_effect=[empty, found])
        config = {"roi": [0, 0, 100, 50], "screen_origin": [0, 0],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0.9,
                  "ocr_threshold": 0.7, "ocr_occurrence": None,
                  "ocr_preprocess": ["raw", "upscale"], "ocr_scale": 2,
                  "ocr_adaptive_block_size": 31, "ocr_adaptive_c": 11}
        result = module.locate_text(np.zeros((50, 100), dtype=np.uint8), "SAVE", config, engine)
        self.assertTrue(result["matched"])
        self.assertEqual(result["preprocess"], "upscale")
        self.assertEqual(engine.call_count, 2)

    def test_ocr_occurrence_retries_when_first_pass_finds_too_few(self):
        def box(x, y):
            return [[x, y], [x + 40, y], [x + 40, y + 20], [x, y + 20]]

        first = SimpleNamespace(txts=("确认",), scores=(0.95,), boxes=(box(10, 10),),
                                word_results=())
        second = SimpleNamespace(txts=("确认", "确认"), scores=(0.96, 0.97),
                                 boxes=(box(20, 20), box(100, 100)), word_results=())
        config = {"roi": [0, 0, 100, 100], "screen_origin": [0, 0],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0.9,
                  "ocr_threshold": 0.7, "ocr_occurrence": 2,
                  "ocr_preprocess": ["raw", "upscale"], "ocr_scale": 2,
                  "ocr_adaptive_block_size": 31, "ocr_adaptive_c": 11}
        engine = mock.Mock(side_effect=[first, second])
        result = module.locate_text(np.zeros((100, 100), dtype=np.uint8), "确认", config, engine)
        self.assertTrue(result["matched"])
        self.assertEqual(result["preprocess"], "upscale")
        self.assertEqual(result["image_center"], [60.0, 55.0])
        self.assertEqual(engine.call_count, 2)

    def test_ambiguous_ocr_evidence_marks_all_candidates(self):
        config = {"roi": [0, 0, 100, 100]}
        result = {"matched": False, "confidence": 0.0, "image_bbox": None,
                  "matches": [{"image_bbox": [10, 30, 20, 10]},
                              {"image_bbox": [50, 60, 20, 10]}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ambiguous.png"
            module.save_evidence(np.zeros((100, 100), dtype=np.uint8), result, config, path)
            image = module.cv2.imread(str(path))
            self.assertTrue(image[30, 10].any())
            self.assertTrue(image[60, 50].any())

    def test_live_ocr_maps_window_roi_and_stays_dry(self):
        gray = np.zeros((40, 80), dtype=np.uint8)
        bgra = np.stack([gray, gray, gray, np.full_like(gray, 255)], axis=2)
        polygon = [[10, 10], [50, 10], [50, 30], [10, 30]]
        output = SimpleNamespace(txts=("SAVE",), scores=(0.95,), boxes=[polygon],
                                 word_results=(None,))
        engine = mock.Mock(return_value=output)
        config = {"roi": [20, 30, 80, 40], "screen_origin": [-1920, 100],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0.9,
                  "stable_frames": 2, "frame_interval_ms": 0, "max_center_shift_px": 0,
                  "ocr_threshold": 0.7, "ocr_occurrence": None,
                  "ocr_preprocess": ["raw"], "ocr_scale": 2,
                  "ocr_adaptive_block_size": 31, "ocr_adaptive_c": 11}

        class FakeMss:
            monitors = [None, {"left": -1920, "top": 0, "width": 1920, "height": 1080}]

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def grab(self, region):
                assert region == {"left": -1900, "top": 130, "width": 80, "height": 40}
                return bgra

        with (mock.patch.dict(sys.modules, {"mss": SimpleNamespace(MSS=FakeMss)}),
              mock.patch.object(module, "set_dpi_awareness"),
              mock.patch.object(module, "ocr_engine", return_value=engine),
              mock.patch.object(module, "send_click") as click):
            result = module.run_live("SAVE", config, execute=False)
        self.assertTrue(result["matched"])
        self.assertTrue(result["stable"])
        self.assertFalse(result["clicked"])
        self.assertEqual(result["screen_center"], [-1870.0, 150.0])
        self.assertEqual(engine.call_count, 2)
        click.assert_not_called()

    def test_inspect_reports_monitor_and_active_window(self):
        fake = SimpleNamespace(monitors=[None, {"left": -1920, "top": 0,
                                              "width": 1920, "height": 1080}])

        class FakeMss:
            def __enter__(self):
                return fake

            def __exit__(self, *_):
                return False

        with (mock.patch.object(module.sys, "platform", "win32"),
              mock.patch.object(module, "set_dpi_awareness"),
              mock.patch.object(module, "foreground_window", return_value=("Editor", (-1920, 0, 0, 1080))),
              mock.patch.dict(sys.modules, {"mss": SimpleNamespace(MSS=FakeMss)})):
            result = module.inspect_desktop()
        self.assertEqual(result["monitors"][0]["left"], -1920)
        self.assertEqual(result["foreground_window"]["title"], "Editor")


if __name__ == "__main__":
    unittest.main()
