import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np


SCRIPT = Path(__file__).resolve().parents[1] / "skills/visual-click-skill/scripts/locate.py"
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

    def test_multiscale_template_maps_resized_target(self):
        rng = np.random.default_rng(21)
        template = rng.integers(0, 256, (16, 20), dtype=np.uint8)
        screenshot = rng.integers(0, 256, (120, 140), dtype=np.uint8)
        screenshot[50:74, 45:75] = module.cv2.resize(template, (30, 24),
                                                       interpolation=module.cv2.INTER_CUBIC)
        config = {"roi": [5, 10, 100, 80], "screen_origin": [-1920, 100],
                  "physical_pixels_per_image_pixel": 1.25, "threshold": 0.995,
                  "template_scales": [1.0, 1.5]}
        result = module.locate(screenshot, template, config)
        self.assertTrue(result["matched"])
        self.assertEqual(result["template_scale"], 1.5)
        self.assertEqual(result["image_bbox"], [45, 50, 30, 24])
        self.assertEqual(result["screen_center"], [-1845.0, 177.5])

    def test_duplicate_templates_block_click_until_occurrence_selected(self):
        rng = np.random.default_rng(22)
        template = rng.integers(0, 256, (10, 12), dtype=np.uint8)
        screenshot = rng.integers(0, 256, (100, 120), dtype=np.uint8)
        screenshot[20:30, 20:32] = template
        screenshot[60:70, 70:82] = template
        config = {"roi": [0, 0, 120, 100], "screen_origin": [0, 0],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0.999,
                  "template_scales": [1.0]}
        ambiguous = module.locate(screenshot, template, config)
        self.assertFalse(ambiguous["matched"])
        self.assertTrue(ambiguous["ambiguous"])
        self.assertEqual(len(ambiguous["matches"]), 2)
        with self.assertRaisesRegex(ValueError, "not matched"):
            module.execute_click({**ambiguous, "stable": True},
                                 {"expected_window_title": "App", "click_offset": [0, 0]}, [])
        config["template_occurrence"] = 2
        selected = module.locate(screenshot, template, config)
        self.assertTrue(selected["matched"])
        self.assertEqual(selected["image_center"], [76.0, 65.0])
        config["template_max_candidates"] = 1
        self.assertTrue(module.locate(screenshot, template, config)["candidate_limit_reached"])
        self.assertFalse(module.locate(screenshot, template, config)["matched"])

    def test_edge_fallback_recovers_target_under_changed_lighting(self):
        y, x = np.mgrid[0:24, 0:24]
        circle = (x - 12) ** 2 + (y - 12) ** 2 < 36
        template = np.full((24, 24), 100, dtype=np.uint8)
        template[circle] = 200
        _, screen_x = np.mgrid[0:90, 0:100]
        screenshot = np.clip(-80 + 4 * screen_x, 0, 255).astype(np.uint8)
        screenshot[35:59, 42:66][circle] = 200
        config = {"roi": [0, 0, 100, 90], "screen_origin": [0, 0],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0.8}
        result = module.locate(screenshot, template, config)
        self.assertTrue(result["matched"])
        self.assertEqual(result["preprocess"], "edges")
        self.assertEqual(result["screen_center"], [54.0, 47.0])

    def test_gaussian_fallback_recovers_noisy_target(self):
        rng = np.random.default_rng(42)
        template = module.cv2.GaussianBlur(rng.integers(0, 256, (28, 30), dtype=np.uint8),
                                           (3, 3), 0)
        screenshot = rng.integers(0, 256, (100, 110), dtype=np.uint8)
        screenshot[40:68, 50:80] = np.clip(template.astype(float) + rng.normal(0, 55, template.shape),
                                            0, 255).astype(np.uint8)
        config = {"roi": [0, 0, 110, 100], "screen_origin": [0, 0],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0.6}
        prepared = {}
        result = module.locate(screenshot, template, config, prepared)
        self.assertTrue(result["matched"])
        self.assertEqual(result["preprocess"], "gaussian")
        self.assertEqual(result["screen_center"], [65.0, 54.0])
        self.assertEqual(prepared["_preferred"], "gaussian")
        with mock.patch.object(module.cv2, "Canny") as canny:
            self.assertTrue(module.locate(screenshot, template, config, prepared)["matched"])
            canny.assert_not_called()

    def test_fast_path_skips_enhancement_and_low_score_peak_scan(self):
        rng = np.random.default_rng(81)
        screenshot = rng.integers(0, 256, (60, 60), dtype=np.uint8)
        template = screenshot[10:20, 15:25].copy()
        config = {"roi": [0, 0, 60, 60], "screen_origin": [0, 0],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0.999,
                  "template_preprocess": ["gray", "edges"],
                  "template_canny_low": 40, "template_canny_high": 100}
        with mock.patch.object(module.cv2, "Canny") as canny:
            self.assertTrue(module.locate(screenshot, template, config)["matched"])
            canny.assert_not_called()
        with mock.patch.object(module.cv2, "dilate") as dilate:
            self.assertFalse(module.locate(np.zeros((60, 60), dtype=np.uint8), template,
                                           {**config, "template_preprocess": ["gray"]})["matched"])
            dilate.assert_not_called()

    def test_live_dry_run_uses_roi_and_never_clicks(self):
        rng = np.random.default_rng(8)
        gray = rng.integers(0, 256, (40, 40), dtype=np.uint8)
        bgra = np.stack([gray, gray, gray, np.full_like(gray, 255)], axis=2)
        template = gray[12:20, 15:25].copy()
        config = {"roi": [10, 10, 40, 40], "screen_origin": [-1920, 0],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0.99,
                  "stable_frames": 2, "frame_interval_ms": 0, "max_center_shift_px": 0,
                  "template_scales": [1.0, 1.5]}

        class FakeMss:
            monitors = [None, SimpleNamespace(left=-1920, top=0, width=1920, height=1080)]

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def grab(self, region):
                self_region = {"left": -1910, "top": 10, "width": 40, "height": 40}
                assert region == self_region
                self.calls = getattr(self, "calls", 0) + 1
                frame = bgra.copy()
                frame[0, 0, :3] = self.calls
                return frame

        with (mock.patch.dict(sys.modules, {"mss": SimpleNamespace(MSS=FakeMss)}),
              mock.patch.object(module, "set_dpi_awareness"),
              mock.patch.object(module.cv2, "resize", wraps=module.cv2.resize) as resize,
              mock.patch.object(module, "send_click") as click):
            result = module.run_live(template, config, execute=False)
        self.assertTrue(result["stable"])
        self.assertFalse(result["clicked"])
        self.assertEqual(result["screen_center"], [-1890.0, 26.0])
        self.assertEqual(resize.call_count, 1)
        click.assert_not_called()

    def test_window_relative_roi_follows_moving_window(self):
        gray = np.random.default_rng(9).integers(0, 256, (40, 40), dtype=np.uint8)
        bgra = np.stack([gray, gray, gray, np.full_like(gray, 255)], axis=2)
        template = gray[12:20, 15:25].copy()
        config = {"roi": [10, 10, 40, 40], "roi_relative_to": "window",
                  "screen_origin": [0, 0], "physical_pixels_per_image_pixel": 1,
                  "threshold": 0.99, "stable_frames": 2, "frame_interval_ms": 0,
                  "max_center_shift_px": 50, "watch_frames": 2,
                  "expected_window_title": "FlClash", "target_surface": "window"}
        regions = []

        class FakeMss:
            monitors = [None, {"left": 0, "top": 0, "width": 1000, "height": 1000}]

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def grab(self, region):
                regions.append(region.copy())
                return bgra

        windows = [("FlClash", (100, 200, 500, 500)),
                   ("FlClash", (130, 220, 530, 520))]
        observed = []
        with (mock.patch.dict(sys.modules, {"mss": SimpleNamespace(MSS=FakeMss)}),
              mock.patch.object(module, "set_dpi_awareness"),
              mock.patch.object(module, "foreground_window", side_effect=windows),
              mock.patch.object(module, "send_click") as click):
            module.run_live(template, config, execute=False, on_frame=observed.append)
        self.assertEqual([region["left"] for region in regions], [110, 140])
        self.assertEqual([item["screen_center"] for item in observed],
                         [[130.0, 226.0], [160.0, 246.0]])
        self.assertTrue(observed[-1]["stable"])
        click.assert_not_called()

    def test_unstable_live_match_blocks_execute(self):
        template = np.random.default_rng(4).integers(0, 256, (8, 8), dtype=np.uint8)
        frames = []
        for x in (10, 10, 20):
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

    def test_watch_tracks_movement_and_clicks_latest_stable_frame(self):
        template = np.random.default_rng(31).integers(0, 256, (8, 8), dtype=np.uint8)
        frames = []
        for x in (10, 20, None, 25, 25, 25, 25):
            gray = np.zeros((40, 50), dtype=np.uint8)
            if x is not None:
                gray[10:18, x:x + 8] = template
            frames.append(np.stack([gray, gray, gray, np.full_like(gray, 255)], axis=2))
        config = {"roi": [0, 0, 50, 40], "screen_origin": [100, 200],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0.99,
                  "stable_frames": 2, "frame_interval_ms": 0, "max_center_shift_px": 0,
                  "watch_frames": len(frames) - 1}

        class FakeMss:
            monitors = [None, {"left": 0, "top": 0, "width": 1000, "height": 1000}]

            def __enter__(self):
                self.frames = iter(frames)
                return self

            def __exit__(self, *_):
                return False

            def grab(self, _):
                return next(self.frames)

        observed = []
        with (mock.patch.object(module.sys, "platform", "win32"),
              mock.patch.dict(sys.modules, {"mss": SimpleNamespace(MSS=FakeMss)}),
              mock.patch.object(module, "set_dpi_awareness"),
              mock.patch.object(module, "execute_click", side_effect=lambda result, *_: result["screen_center"]) as click):
            result = module.run_live(template, config, execute=True, on_frame=observed.append)
        self.assertEqual([item["frame"] for item in observed], [1, 2, 3, 4, 5, 6])
        self.assertEqual([item["screen_center"] for item in observed if item["matched"]],
                         [[114.0, 214.0], [124.0, 214.0], [129.0, 214.0], [129.0, 214.0], [129.0, 214.0]])
        self.assertEqual([item["stable"] for item in observed], [False, False, False, False, True, True])
        self.assertEqual(result["click_point"], [129.0, 214.0])
        click.assert_called_once()

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

    def test_window_move_after_capture_blocks_click(self):
        result = {"matched": True, "stable": True, "screen_center": [50, 50],
                  "capture_window_bounds": [0, 0, 100, 100]}
        with (mock.patch.object(module, "foreground_window", return_value=("App", (10, 0, 110, 100))),
              mock.patch.object(module, "send_click") as click):
            with self.assertRaisesRegex(ValueError, "window moved after capture"):
                module.execute_click(result, {"expected_window_title": "App", "click_offset": [0, 0]},
                                     [None, {"left": 0, "top": 0, "width": 500, "height": 500}])
            click.assert_not_called()

    def test_post_recognition_change_blocks_click_and_single_frame_movement(self):
        template = np.random.default_rng(73).integers(0, 256, (8, 8), dtype=np.uint8)

        def frame(x):
            gray = np.zeros((40, 50), dtype=np.uint8)
            gray[10:18, x:x + 8] = template
            return module.cv2.cvtColor(gray, module.cv2.COLOR_GRAY2BGRA)

        config = {"roi": [0, 0, 50, 40], "screen_origin": [0, 0],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0.99,
                  "stable_frames": 1, "frame_interval_ms": 0, "max_center_shift_px": 0}
        for frames in ([frame(10), frame(10), frame(20)], [frame(10), frame(20)]):
            with self.subTest(captures=len(frames)):
                capture = mock.MagicMock()
                capture.__enter__.return_value = capture
                capture.grab.side_effect = frames
                with (mock.patch.object(module.sys, "platform", "win32"),
                      mock.patch.dict(sys.modules, {"mss": SimpleNamespace(MSS=lambda: capture)}),
                      mock.patch.object(module, "set_dpi_awareness"),
                      mock.patch.object(module, "execute_click") as click):
                    result = module.run_live(template, config, execute=True)
                self.assertFalse(result["clicked"])
                click.assert_not_called()
                if len(frames) == 3:
                    self.assertEqual(result["reason"], "frame_changed_before_click")
                    self.assertFalse(result["stable"])

    def test_ocr_remembers_enhancement_and_skips_duplicate_pixels(self):
        crop = np.random.default_rng(23).integers(50, 120, (50, 100), dtype=np.uint8)
        config = {"roi": [0, 0, 100, 50], "screen_origin": [0, 0],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0.9,
                  "ocr_threshold": 0.7, "ocr_occurrence": None,
                  "ocr_preprocess": "auto", "ocr_scale": 1,
                  "ocr_adaptive_block_size": 31, "ocr_adaptive_c": 11}
        empty = SimpleNamespace(txts=(), scores=(), boxes=(), word_results=())
        found = SimpleNamespace(txts=("SAVE",), scores=(0.95,),
                                boxes=([[10, 10], [50, 10], [50, 30], [10, 30]],), word_results=())
        engine = mock.Mock(side_effect=[empty, found])
        state = {}
        result = module.locate_text(crop, "SAVE", config, engine, state)
        self.assertEqual(result["preprocess"], "clahe")
        self.assertEqual(state["preferred"], "clahe")
        self.assertEqual(engine.call_count, 2)  # scale=1 makes raw/upscale identical
        config["ocr_scale"] = 2
        engine = mock.Mock(side_effect=[empty, found])
        module.locate_text(crop, "SAVE", config, engine, state)
        self.assertEqual(engine.call_count, 2)  # raw -> previous winner, skipping upscale

    def test_execute_exit_status_requires_actual_click(self):
        import json

        config = {"roi": [0, 0, 10, 10]}
        with (mock.patch.object(sys, "argv", ["locate.py", "--config", "unused.json", "--live",
                                             "--text", "SAVE", "--execute", "--watch"]),
              mock.patch.object(Path, "read_text", return_value=json.dumps(config)),
              mock.patch.object(module, "run_live", return_value={"matched": True, "stable": True,
                                                                 "clicked": False}) as run):
            self.assertEqual(module.main(), 2)
            run.return_value["clicked"] = True
            self.assertEqual(module.main(), 0)
            run.side_effect = KeyboardInterrupt
            self.assertEqual(module.main(), 2)

    def test_desktop_double_click_requires_unobscured_target(self):
        result = {"matched": True, "stable": True, "screen_center": [506, 94]}
        config = {"target_surface": "desktop", "click_offset": [0, 0],
                  "click_count": 2, "click_interval_ms": 120}
        monitors = [None, {"left": 0, "top": 0, "width": 1000, "height": 1000}]
        with (mock.patch.object(module, "window_classes_at", return_value=["QtWindow"]) as classes,
              mock.patch.object(module, "send_click") as click):
            with self.assertRaisesRegex(ValueError, "not on the Windows desktop"):
                module.execute_click(result, config, monitors)
            classes.assert_called_once_with(506, 94)
            click.assert_not_called()
        with (mock.patch.object(module, "window_classes_at",
                                return_value=["SysListView32", "SHELLDLL_DefView", "WorkerW"]),
              mock.patch.object(module, "send_click") as click,
              mock.patch.object(module.time, "sleep") as sleep):
            self.assertEqual(module.execute_click(result, config, monitors), [506, 94])
            self.assertEqual(click.call_args_list, [mock.call(506, 94), mock.call(506, 94)])
            sleep.assert_called_once_with(0.12)
        config["click_count"] = 3
        with mock.patch.object(module, "send_click") as click:
            with self.assertRaisesRegex(ValueError, "click_count"):
                module.execute_click(result, config, monitors)
            click.assert_not_called()

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
                  "ocr_preprocess": "auto", "ocr_scale": 2,
                  "ocr_adaptive_block_size": 31, "ocr_adaptive_c": 11}
        result = module.locate_text(np.zeros((50, 100), dtype=np.uint8), "SAVE", config, engine)
        self.assertTrue(result["matched"])
        self.assertEqual(result["preprocess"], "upscale")
        self.assertEqual(engine.call_count, 2)

    def test_clahe_preprocessing_expands_low_contrast_roi(self):
        crop = np.tile(np.arange(100, 120, dtype=np.uint8), (40, 1))
        config = {"ocr_preprocess": ["clahe"], "ocr_scale": 2,
                  "ocr_adaptive_block_size": 31, "ocr_adaptive_c": 11,
                  "ocr_clahe_clip_limit": 3.0, "ocr_clahe_grid_size": 4}
        method, enhanced, factor = next(module.ocr_variants(crop, config))
        self.assertEqual((method, factor, enhanced.shape), ("clahe", 2, (80, 40)))
        self.assertGreater(np.ptp(enhanced), np.ptp(crop))

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
        self.assertEqual(engine.call_count, 1)
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
        with (mock.patch.object(module.sys, "platform", "win32"),
              mock.patch.object(module, "set_dpi_awareness"),
              mock.patch.object(module, "foreground_window", side_effect=RuntimeError("no active, visible window")),
              mock.patch.dict(sys.modules, {"mss": SimpleNamespace(MSS=FakeMss)})):
            self.assertIsNone(module.inspect_desktop()["foreground_window"])

    def test_timed_wait_retries_movement_and_uses_cache(self):
        template = np.random.default_rng(91).integers(0, 256, (8, 8), dtype=np.uint8)
        config = {"roi": [0, 0, 50, 40], "screen_origin": [0, 0],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0.99,
                  "stable_frames": 2, "frame_interval_ms": 100, "max_center_shift_px": 0,
                  "timeout_seconds": 1, "template_preprocess": ["gray"]}
        for positions, timeout, expected in [([None, 10, 20, 20], 1, True),
                                             ([None, None, None], 0.25, False)]:
            frames = []
            observed = []
            for x in positions:
                gray = np.zeros((40, 50), dtype=np.uint8)
                if x is not None:
                    gray[10:18, x:x + 8] = template
                frames.append(module.cv2.cvtColor(gray, module.cv2.COLOR_GRAY2BGRA))
            clock = [0.0]
            capture = mock.MagicMock()
            capture.__enter__.return_value = capture
            capture.grab.side_effect = frames
            with (self.subTest(timeout=timeout),
                  mock.patch.dict(sys.modules, {"mss": SimpleNamespace(MSS=lambda: capture)}),
                  mock.patch.object(module, "set_dpi_awareness"),
                  mock.patch.object(module.time, "monotonic", side_effect=lambda: clock[0]),
                  mock.patch.object(module.time, "sleep", side_effect=lambda t: clock.__setitem__(0, clock[0] + t)),
                  mock.patch.object(module, "send_click") as click):
                result = module.run_live(template, {**config, "timeout_seconds": timeout}, execute=False,
                                         on_frame=None if expected else observed.append)
            self.assertEqual(result["stable"], expected)
            self.assertEqual(result["frames"], len(frames))
            self.assertGreater(result["cache_hits"], 0)
            click.assert_not_called()
            if expected:
                self.assertEqual(result["screen_center"], [24.0, 14.0])
            else:
                self.assertTrue(result["timed_out"])
                self.assertEqual(result["elapsed_ms"], 250)
                self.assertTrue(observed[-1]["timed_out"])

    def test_slow_recognition_exceeding_timeout_never_clicks(self):
        config = {"roi": [0, 0, 50, 40], "screen_origin": [0, 0],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0.99,
                  "stable_frames": 1, "frame_interval_ms": 0, "max_center_shift_px": 0,
                  "timeout_seconds": 1}
        clock = [0.0]
        capture = mock.MagicMock()
        capture.__enter__.return_value = capture
        capture.grab.side_effect = [np.zeros((40, 50, 4), dtype=np.uint8),
                                    np.ones((40, 50, 4), dtype=np.uint8)]
        calls = []

        def slow_locate(*_):
            calls.append(1)
            if len(calls) == 2:
                clock[0] = 2.0
            return {"matched": True, "screen_center": [20, 20], "screen_bbox": [15, 15, 10, 10]}

        with (mock.patch.dict(sys.modules, {"mss": SimpleNamespace(MSS=lambda: capture)}),
              mock.patch.object(module.sys, "platform", "win32"),
              mock.patch.object(module, "set_dpi_awareness"),
              mock.patch.object(module.time, "monotonic", side_effect=lambda: clock[0]),
              mock.patch.object(module, "locate", side_effect=slow_locate),
              mock.patch.object(module, "execute_click") as click):
            result = module.run_live(np.ones((8, 8), dtype=np.uint8), config, execute=True)
        self.assertEqual(result["reason"], "timeout")
        self.assertFalse(result["stable"])
        self.assertEqual(result["frames"], 2)
        click.assert_not_called()

    def test_unicode_image_paths_and_invalid_images(self):
        config = {"roi": [0, 0, 80, 40]}
        result = {"matched": True, "confidence": 0.99, "image_bbox": [10, 10, 20, 10]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "中文目录" / "定位结果.png"
            module.save_evidence(np.zeros((40, 80), dtype=np.uint8), result, config, path)
            image = module.read_gray(path)
            self.assertEqual(image.shape, (40, 80))
            self.assertTrue(image[10, 10])
            for data in (b"", b"not an image"):
                path.write_bytes(data)
                with self.assertRaisesRegex(ValueError, "cannot read image"):
                    module.read_gray(path)

    def test_timed_wait_clicks_once_unless_final_capture_expires(self):
        template = np.random.default_rng(92).integers(0, 256, (8, 8), dtype=np.uint8)
        gray = np.zeros((40, 50), dtype=np.uint8)
        gray[10:18, 20:28] = template
        found = module.cv2.cvtColor(gray, module.cv2.COLOR_GRAY2BGRA)
        config = {"roi": [0, 0, 50, 40], "screen_origin": [0, 0],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0.99,
                  "stable_frames": 1, "frame_interval_ms": 0, "max_center_shift_px": 0,
                  "timeout_seconds": 1, "template_preprocess": ["gray"]}
        for expire in (False, True):
            clock, grabs = [0.0], []
            capture = mock.MagicMock()
            capture.__enter__.return_value = capture

            def grab(_):
                grabs.append(1)
                if expire and len(grabs) == 4:
                    clock[0] = 2.0
                return np.zeros_like(found) if len(grabs) == 1 else found

            capture.grab.side_effect = grab
            with (self.subTest(expire=expire),
                  mock.patch.dict(sys.modules, {"mss": SimpleNamespace(MSS=lambda: capture)}),
                  mock.patch.object(module.sys, "platform", "win32"),
                  mock.patch.object(module, "set_dpi_awareness"),
                  mock.patch.object(module.time, "monotonic", side_effect=lambda: clock[0]),
                  mock.patch.object(module.time, "sleep"),
                  mock.patch.object(module, "execute_click", return_value=[24, 14]) as click):
                result = module.run_live(template, config, execute=True)
            self.assertEqual(result["clicked"], not expire)
            self.assertEqual(click.call_count, 0 if expire else 1)
            self.assertEqual(result["frames"], 3)

    def test_candidate_limit_bounds_peak_materialization(self):
        template = np.random.default_rng(93).integers(0, 256, (8, 8), dtype=np.uint8)
        config = {"roi": [0, 0, 48, 48], "screen_origin": [0, 0],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0,
                  "template_preprocess": ["gray"], "template_max_candidates": 5}
        with mock.patch.object(module, "location_result", wraps=module.location_result) as build:
            result = module.locate(np.zeros((48, 48), dtype=np.uint8), template, config)
        self.assertFalse(result["matched"])
        self.assertTrue(result["candidate_limit_reached"])
        self.assertLessEqual(build.call_count, 6)  # 最佳诊断框加最多 5 个候选。

    def test_resizing_target_at_same_center_is_not_stable(self):
        config = {"roi": [0, 0, 50, 40], "screen_origin": [0, 0],
                  "physical_pixels_per_image_pixel": 1, "threshold": 0.9,
                  "stable_frames": 2, "frame_interval_ms": 0, "max_center_shift_px": 3}
        capture = mock.MagicMock()
        capture.__enter__.return_value = capture
        capture.grab.side_effect = [np.zeros((40, 50, 4), dtype=np.uint8),
                                    np.ones((40, 50, 4), dtype=np.uint8)]
        targets = [{"matched": True, "screen_center": [25, 20], "screen_bbox": box}
                   for box in ([20, 15, 10, 10], [10, 5, 30, 30])]
        with (mock.patch.dict(sys.modules, {"mss": SimpleNamespace(MSS=lambda: capture)}),
              mock.patch.object(module, "set_dpi_awareness"),
              mock.patch.object(module, "locate", side_effect=targets),
              mock.patch.object(module, "send_click") as click):
            result = module.run_live(np.ones((8, 8), dtype=np.uint8), config, execute=False)
        self.assertFalse(result["stable"])
        click.assert_not_called()

    def test_watch_without_stable_match_exits_unsuccessfully(self):
        with (mock.patch.object(sys, "argv", ["locate.py", "--config", "unused.json", "--live",
                                             "--text", "SAVE", "--watch"]),
              mock.patch.object(Path, "read_text", return_value="{}"),
              mock.patch.object(module, "run_live", return_value={"matched": False, "stable": False})):
            self.assertEqual(module.main(), 2)


if __name__ == "__main__":
    unittest.main()
