"""Locate a visual target; live capture defaults to dry-run."""

import argparse
import ctypes
import json
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np


def validate_config(config: dict, image_shape=None) -> None:
    roi = config["roi"]
    origin = config["screen_origin"]
    scale = config["physical_pixels_per_image_pixel"]
    threshold = config["threshold"]
    if (not isinstance(roi, list) or not isinstance(origin, list)
            or len(roi) != 4 or len(origin) != 2
            or any(type(v) is not int for v in roi + origin)
            or any(v < 0 for v in roi[:2]) or any(v <= 0 for v in roi[2:])
            or type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0
            or type(threshold) not in (int, float) or not math.isfinite(threshold)
            or not 0 <= threshold <= 1):
        raise ValueError("invalid roi, screen_origin, scale, or threshold")
    left, top, width, height = roi
    if image_shape and (top + height > image_shape[0] or left + width > image_shape[1]):
        raise ValueError("roi extends outside screenshot")


def locate(screenshot: np.ndarray, template: np.ndarray, config: dict) -> dict:
    validate_config(config, screenshot.shape)
    left, top, width, height = config["roi"]
    origin = config["screen_origin"]
    scale = config["physical_pixels_per_image_pixel"]
    threshold = config["threshold"]
    if template.shape[0] > height or template.shape[1] > width:
        raise ValueError("template is larger than roi")
    if np.std(template) == 0:
        raise ValueError("constant template cannot be matched reliably")

    crop = screenshot[top:top + height, left:left + width]
    score_map = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)
    _, confidence, _, point = cv2.minMaxLoc(score_map)
    image_center = [left + point[0] + template.shape[1] / 2,
                    top + point[1] + template.shape[0] / 2]
    screen_center = [origin[i] + image_center[i] * scale for i in range(2)]
    return {"matched": confidence >= threshold, "confidence": round(confidence, 6),
            "image_center": image_center, "screen_center": screen_center,
            "image_bbox": [left + point[0], top + point[1], template.shape[1], template.shape[0]],
            "screen_bbox": [origin[0] + (left + point[0]) * scale,
                            origin[1] + (top + point[1]) * scale,
                            template.shape[1] * scale, template.shape[0] * scale],
            "roi": config["roi"]}


def ocr_engine():
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise RuntimeError("OCR requires rapidocr and onnxruntime; install requirements.txt") from exc
    return RapidOCR(params={"Global.log_level": "warning", "Global.text_score": 0.0,
                            "Global.return_single_char_box": True})


def ocr_variants(crop: np.ndarray, config: dict):
    methods = config["ocr_preprocess"]
    factor = config["ocr_scale"]
    block = config["ocr_adaptive_block_size"]
    adaptive_c = config["ocr_adaptive_c"]
    allowed = {"raw", "upscale", "otsu", "adaptive", "invert"}
    if (not isinstance(methods, list) or not methods or any(m not in allowed for m in methods)
            or type(factor) is not int or factor < 1
            or type(block) is not int or block < 3 or block % 2 != 1
            or type(adaptive_c) not in (int, float) or not math.isfinite(adaptive_c)):
        raise ValueError("invalid OCR preprocessing configuration")
    enlarged = None
    for method in methods:
        if method == "raw":
            yield method, crop, 1
        else:
            if enlarged is None:
                enlarged = cv2.resize(crop, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
            if method == "upscale":
                yield method, enlarged, factor
            elif method == "otsu":
                yield method, cv2.threshold(enlarged, 0, 255,
                                            cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1], factor
            elif method == "adaptive":
                yield method, cv2.adaptiveThreshold(enlarged, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                                     cv2.THRESH_BINARY, block, adaptive_c), factor
            else:
                yield method, cv2.bitwise_not(enlarged), factor


def image_box(polygon, factor: int, roi: list[int]) -> list[float]:
    points = np.asarray(polygon, dtype=float)
    x1, y1 = points.min(axis=0) / factor
    x2, y2 = points.max(axis=0) / factor
    return [float(roi[0] + x1), float(roi[1] + y1), float(x2 - x1), float(y2 - y1)]


def text_result(text: str, score: float, box: list[float], config: dict, method: str) -> dict:
    scale = config["physical_pixels_per_image_pixel"]
    origin = config["screen_origin"]
    center = [box[0] + box[2] / 2, box[1] + box[3] / 2]
    return {"text": text, "confidence": round(score, 6), "image_bbox": box,
            "image_center": center,
            "screen_bbox": [origin[0] + box[0] * scale, origin[1] + box[1] * scale,
                            box[2] * scale, box[3] * scale],
            "screen_center": [origin[i] + center[i] * scale for i in range(2)],
            "preprocess": method}


def locate_text(screenshot: np.ndarray, target: str, config: dict, engine=None) -> dict:
    validate_config(config, screenshot.shape)
    if not isinstance(target, str) or not target.strip():
        raise ValueError("--text must be non-empty")
    threshold = config["ocr_threshold"]
    occurrence = config["ocr_occurrence"]
    if (type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 1
            or occurrence is not None and (type(occurrence) is not int or occurrence < 1)):
        raise ValueError("invalid ocr_threshold or ocr_occurrence")
    engine = engine or ocr_engine()
    left, top, width, height = config["roi"]
    crop = screenshot[top:top + height, left:left + width]
    wanted = "".join(target.casefold().split())
    recognized = []
    partial_matches = []
    for method, prepared, factor in ocr_variants(crop, config):
        output = engine(cv2.cvtColor(prepared, cv2.COLOR_GRAY2BGR), return_word_box=True)
        matches = []
        lines = []
        for index, line in enumerate(output.txts or ()):
            score = float(output.scores[index])
            line_box = image_box(output.boxes[index], factor, config["roi"])
            lines.append(text_result(line, score, line_box, config, method))
            word_results = output.word_results or ()
            words = word_results[index] if index < len(word_results) else ()
            words = words or ()
            line_matches = []
            for start in range(len(words)):
                for end in range(start + 1, len(words) + 1):
                    span = words[start:end]
                    joined = "".join("".join(str(word[0]).casefold().split()) for word in span)
                    if joined == wanted:
                        word_score = min(float(word[1]) for word in span)
                        if word_score >= threshold:
                            points = np.concatenate([np.asarray(word[2], dtype=float) for word in span])
                            line_matches.append(text_result(target, word_score,
                                                            image_box(points, factor, config["roi"]),
                                                            config, method))
                    if len(joined) >= len(wanted):
                        break
            if line_matches:
                matches.extend(line_matches)
            elif "".join(line.casefold().split()) == wanted and score >= threshold:
                matches.append(text_result(target, score, line_box, config, method))
        if len(lines) > len(recognized):
            recognized = lines
        if matches:
            matches.sort(key=lambda item: (item["image_center"][1], item["image_center"][0]))
            if occurrence is not None and occurrence > len(matches):
                if len(matches) > len(partial_matches):
                    partial_matches = matches
                continue
            selected = matches[occurrence - 1] if occurrence is not None and occurrence <= len(matches) else None
            if occurrence is None and len(matches) == 1:
                selected = matches[0]
            return {**(selected or {"confidence": 0.0, "image_bbox": None,
                                    "screen_bbox": None, "image_center": None, "screen_center": None}),
                    "matched": selected is not None, "ambiguous": len(matches) > 1 and occurrence is None,
                    "roi": config["roi"], "recognized": lines, "matches": matches}
    return {"matched": False, "ambiguous": False, "confidence": 0.0,
            "image_bbox": None, "screen_bbox": None, "image_center": None, "screen_center": None,
            "roi": config["roi"], "recognized": recognized, "matches": partial_matches}


def set_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    shcore = ctypes.windll.shcore
    if shcore.SetProcessDpiAwareness(2) != 0:
        current = ctypes.c_int()
        if shcore.GetProcessDpiAwareness(None, ctypes.byref(current)) != 0 or current.value != 2:
            raise RuntimeError("per-monitor DPI awareness is required for live coordinates")


def foreground_window() -> tuple[str, tuple[int, int, int, int]]:
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    hwnd = user32.GetForegroundWindow()
    if not hwnd or user32.IsIconic(hwnd):
        raise RuntimeError("no active, visible window")
    title = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
    user32.GetWindowTextW(hwnd, title, len(title))
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("cannot read active window bounds")
    return title.value, (rect.left, rect.top, rect.right, rect.bottom)


def send_click(x: int, y: int) -> None:
    from pynput.mouse import Button, Controller

    mouse = Controller()
    mouse.position = (x, y)
    mouse.click(Button.left)


def monitor_value(monitor, key: str) -> int:
    return monitor[key] if isinstance(monitor, dict) else getattr(monitor, key)


def execute_click(result: dict, config: dict, monitors: list) -> list[int]:
    if not result["matched"] or not result["stable"]:
        raise ValueError("target is not matched and stable")
    title_expected = config["expected_window_title"]
    offset = config["click_offset"]
    if (not isinstance(title_expected, str) or not title_expected.strip()
            or not isinstance(offset, list) or len(offset) != 2
            or any(type(v) is not int for v in offset)):
        raise ValueError("expected_window_title and click_offset are required for execute")
    x, y = [round(result["screen_center"][i] + offset[i]) for i in range(2)]
    if not any(monitor_value(m, "left") <= x < monitor_value(m, "left") + monitor_value(m, "width")
               and monitor_value(m, "top") <= y < monitor_value(m, "top") + monitor_value(m, "height")
               for m in monitors[1:]):
        raise ValueError("target is outside connected monitors")
    title, (left, top, right, bottom) = foreground_window()
    if title_expected.casefold() not in title.casefold() or not (left <= x < right and top <= y < bottom):
        raise ValueError("target is outside the expected foreground window")
    send_click(x, y)
    return [x, y]


def inspect_desktop() -> dict:
    if sys.platform != "win32":
        raise ValueError("--inspect is supported on Windows only")
    set_dpi_awareness()
    from mss import MSS

    with MSS() as sct:
        monitors = [{key: monitor_value(m, key) for key in ("left", "top", "width", "height")}
                    for m in sct.monitors[1:]]
    title, bounds = foreground_window()
    return {"monitors": monitors, "foreground_window": {"title": title, "bounds": bounds}}


def save_evidence(screenshot: np.ndarray, result: dict, config: dict, path: Path) -> None:
    left, top, width, height = config["roi"]
    canvas = cv2.cvtColor(screenshot[top:top + height, left:left + width], cv2.COLOR_GRAY2BGR)
    color = (0, 180, 0) if result["matched"] and result.get("stable", True) else (0, 120, 255)
    candidates = result.get("matches") or [result]
    for index, candidate in enumerate(candidates, 1):
        if candidate["image_bbox"] is None:
            continue
        x, y, box_width, box_height = candidate["image_bbox"]
        x, y = round(x - left), round(y - top)
        candidate_color = (0, 180, 0) if (result["matched"] and result.get("stable", True)
                                          and candidate["image_bbox"] == result["image_bbox"]) else (0, 120, 255)
        cv2.rectangle(canvas, (x, y), (x + round(box_width), y + round(box_height)), candidate_color, 2)
        if result.get("matches"):
            cv2.putText(canvas, str(index), (x, max(12, y - 3)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, candidate_color, 1)
    cv2.putText(canvas, f"confidence={result['confidence']:.3f}", (4, min(height - 4, 18)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), canvas):
        raise OSError(f"cannot write evidence: {path}")


def run_live(target: np.ndarray | str, config: dict, execute: bool,
             evidence: Path | None = None) -> dict:
    if execute and sys.platform != "win32":
        raise ValueError("--execute is supported on Windows only")
    validate_config(config)
    if config["physical_pixels_per_image_pixel"] != 1:
        raise ValueError("live capture requires physical_pixels_per_image_pixel = 1")
    count = config["stable_frames"]
    interval = config["frame_interval_ms"]
    shift = config["max_center_shift_px"]
    if (type(count) is not int or count < 1
            or type(interval) not in (int, float) or not math.isfinite(interval) or interval < 0
            or type(shift) not in (int, float) or not math.isfinite(shift) or shift < 0):
        raise ValueError("invalid stable_frames, frame_interval_ms, or max_center_shift_px")
    set_dpi_awareness()
    from mss import MSS

    left, top, width, height = config["roi"]
    origin = config["screen_origin"]
    region = {"left": origin[0] + left, "top": origin[1] + top,
              "width": width, "height": height}
    local_config = {**config, "roi": [0, 0, width, height],
                    "screen_origin": [region["left"], region["top"]]}
    engine = ocr_engine() if isinstance(target, str) else None
    first_center = None
    with MSS() as sct:
        for frame in range(count):
            if frame:
                time.sleep(interval / 1000)
            try:
                screenshot = np.asarray(sct.grab(region))
            except Exception as exc:
                raise RuntimeError(f"capture failed: {exc}") from exc
            gray = cv2.cvtColor(screenshot, cv2.COLOR_BGRA2GRAY)
            result = (locate_text(gray, target, local_config, engine)
                      if isinstance(target, str) else locate(gray, target, local_config))
            if not result["matched"]:
                result["stable"] = False
                break
            center = result["screen_center"]
            if first_center and math.dist(center, first_center) > shift:
                result["stable"] = False
                break
            first_center = first_center or center
        else:
            result["stable"] = True
        result["clicked"] = False
        if evidence is not None:
            save_evidence(gray, result, local_config, evidence)
            result["evidence"] = str(evidence)
        if execute and result["stable"]:
            result["click_point"] = execute_click(result, config, sct.monitors)
            result["clicked"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--screenshot", type=Path)
    source.add_argument("--live", action="store_true")
    source.add_argument("--inspect", action="store_true", help="show monitors and foreground window")
    target_group = parser.add_mutually_exclusive_group()
    target_group.add_argument("--template", type=Path)
    target_group.add_argument("--text", help="locate exact text using OCR")
    parser.add_argument("--evidence", type=Path, help="save annotated ROI image")
    parser.add_argument("--execute", action="store_true", help="click only in live mode")
    args = parser.parse_args()
    try:
        if args.inspect:
            if args.execute or args.evidence:
                raise ValueError("--inspect cannot be combined with --execute or --evidence")
            print(json.dumps(inspect_desktop()))
            return 0
        if args.config is None or (args.template is None and args.text is None):
            raise ValueError("--config and either --template or --text are required")
        if args.execute and not args.live:
            raise ValueError("--execute requires --live")
        config = json.loads(args.config.read_text(encoding="utf-8"))
        target = args.text if args.text is not None else cv2.imread(str(args.template), cv2.IMREAD_GRAYSCALE)
        if target is None:
            raise ValueError("cannot read template")
        if args.live:
            result = run_live(target, config, args.execute, args.evidence)
        else:
            screenshot = cv2.imread(str(args.screenshot), cv2.IMREAD_GRAYSCALE)
            if screenshot is None:
                raise ValueError("cannot read screenshot")
            result = (locate_text(screenshot, target, config)
                      if isinstance(target, str) else locate(screenshot, target, config))
            if args.evidence is not None:
                save_evidence(screenshot, result, config, args.evidence)
                result["evidence"] = str(args.evidence)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0 if result["matched"] and result.get("stable", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
