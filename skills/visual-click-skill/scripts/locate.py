"""实时定位图片或文字；默认只识别。"""

import argparse
import ctypes
import hashlib
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


def box_iou(a: list[float], b: list[float]) -> float:
    overlap_width = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    overlap_height = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    intersection = overlap_width * overlap_height
    return intersection / (a[2] * a[3] + b[2] * b[3] - intersection)


def location_result(box: list[float], score: float, config: dict, method: str, **metadata) -> dict:
    """将原图目标框映射到物理屏幕；图片与 OCR 共用原点和 DPI 换算。"""
    scale, origin = config["physical_pixels_per_image_pixel"], config["screen_origin"]
    x, y, width, height = box
    center = [x + width / 2, y + height / 2]
    return {**metadata, "confidence": round(float(score), 6), "image_bbox": box,
            "image_center": center, "preprocess": method,
            "screen_bbox": [origin[0] + x * scale, origin[1] + y * scale, width * scale, height * scale],
            "screen_center": [origin[i] + center[i] * scale for i in range(2)]}


def select_match(matches: list[dict], occurrence: int | None) -> dict | None:
    """统一按从上到下、从左到右编号；未指定编号时只允许唯一目标。"""
    matches.sort(key=lambda item: (item["image_center"][1], item["image_center"][0]))
    if occurrence is None:
        return matches[0] if len(matches) == 1 else None
    return matches[occurrence - 1] if occurrence <= len(matches) else None


def template_variant(image: np.ndarray, method: str, config: dict) -> np.ndarray:
    if method == "gray":
        return image
    if method == "clahe":
        return cv2.createCLAHE(clipLimit=config.get("template_clahe_clip_limit", 3.0),
                               tileGridSize=(config.get("template_clahe_grid_size", 8),) * 2).apply(image)
    if method == "otsu":
        return cv2.threshold(image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    if method == "gaussian":
        return cv2.GaussianBlur(image, (config.get("template_denoise_kernel", 3),) * 2, 0)
    if method == "median":
        return cv2.medianBlur(image, config.get("template_denoise_kernel", 3))
    return cv2.Canny(image, config.get("template_canny_low", 40),
                     config.get("template_canny_high", 100))


def locate_once(screenshot: np.ndarray, template: np.ndarray, config: dict, method: str,
                prepared: dict | None = None) -> dict:
    validate_config(config, screenshot.shape)
    left, top, width, height = config["roi"]
    threshold = config["threshold"]
    scales = config.get("template_scales", [1.0])
    occurrence = config.get("template_occurrence")
    nms_iou = config.get("template_nms_iou", 0.3)
    max_candidates = config.get("template_max_candidates", 100)
    if (not isinstance(scales, list) or not scales
            or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                   for value in scales)
            or occurrence is not None and (type(occurrence) is not int or occurrence < 1)
            or type(nms_iou) not in (int, float) or not math.isfinite(nms_iou)
            or not 0 < nms_iou < 1
            or type(max_candidates) is not int or max_candidates < 1):
        raise ValueError("invalid template scales, occurrence, NMS IoU, or candidate limit")
    if np.std(template) == 0:
        raise ValueError("constant template cannot be matched reliably")

    crop = template_variant(screenshot[top:top + height, left:left + width], method, config)
    best = None
    candidates = []
    for factor in scales:
        scaled_width = round(template.shape[1] * factor)
        scaled_height = round(template.shape[0] * factor)
        if min(scaled_width, scaled_height) < 2 or scaled_width > width or scaled_height > height:
            continue
        key = (method, scaled_width, scaled_height)
        if prepared is not None and key in prepared:
            scaled = prepared[key]
        else:
            scaled = (template if scaled_width == template.shape[1] and scaled_height == template.shape[0]
                      else cv2.resize(template, (scaled_width, scaled_height), interpolation=(
                          cv2.INTER_AREA if factor < 1 else cv2.INTER_CUBIC)))
            scaled = template_variant(scaled, method, config)
            if np.std(scaled) == 0:
                scaled = None
            if prepared is not None:
                prepared[key] = scaled
        if scaled is None:
            continue
        scores = cv2.matchTemplate(crop, scaled, cv2.TM_CCOEFF_NORMED)

        def candidate(point, confidence):
            box = [left + point[0], top + point[1], scaled_width, scaled_height]
            return location_result(box, confidence, config, method, template_scale=factor)

        _, confidence, _, point = cv2.minMaxLoc(scores)
        if best is None or confidence > best["confidence"]:
            best = candidate(point, confidence)
        if confidence < threshold:
            continue
        peaks = (scores >= threshold) & (scores == cv2.dilate(scores, np.ones((3, 3), np.uint8)))
        ys, xs = np.where(peaks)
        candidates.extend(candidate((int(x), int(y)), scores[y, x]) for y, x in zip(ys, xs))
    if best is None:
        raise ValueError("all template scales are larger than roi or constant")
    matches = []
    truncated = False
    # 多尺度可能重复命中同一目标，先按重叠面积去重，再判断是否存在多个目标。
    for item in sorted(candidates, key=lambda value: value["confidence"], reverse=True):
        if all(box_iou(item["image_bbox"], kept["image_bbox"]) < nms_iou for kept in matches):
            matches.append(item)
            if len(matches) > max_candidates:
                matches.pop()
                truncated = True
                break
    selected = select_match(matches, occurrence)
    if truncated:  # 候选被截断时，不能确认编号或唯一性。
        selected = None
    if selected is None:
        return {**best, "matched": False, "ambiguous": truncated or len(matches) > 1 and occurrence is None,
                "candidate_limit_reached": truncated,
                "image_center": None if matches else best["image_center"],
                "screen_center": None if matches else best["screen_center"],
                "image_bbox": None if matches else best["image_bbox"],
                "screen_bbox": None if matches else best["screen_bbox"],
                "roi": config["roi"], "matches": matches}
    return {**selected, "matched": True, "ambiguous": False, "candidate_limit_reached": False,
            "roi": config["roi"],
            "matches": matches}


def locate(screenshot: np.ndarray, template: np.ndarray, config: dict,
           prepared: dict | None = None) -> dict:
    setting = config.get("template_preprocess", "auto")
    automatic = isinstance(setting, str) and setting == "auto"
    methods = (["gray", "edges", "gaussian", "median", "clahe", "otsu"]
               if automatic else setting)
    denoise = config.get("template_denoise_kernel", 3)
    clip = config.get("template_clahe_clip_limit", 3.0)
    grid = config.get("template_clahe_grid_size", 8)
    canny_low = config.get("template_canny_low", 40)
    canny_high = config.get("template_canny_high", 100)
    if (not isinstance(methods, list) or not methods
            or any(type(method) is not str or method not in ("gray", "clahe", "otsu", "edges",
                                                          "gaussian", "median")
                   for method in methods)
            or len(methods) != len(set(methods))
            or any(method in methods for method in ("gaussian", "median"))
            and (type(denoise) is not int or denoise < 3 or denoise % 2 != 1)
            or "clahe" in methods and (type(clip) not in (int, float) or not math.isfinite(clip)
                                       or clip <= 0 or type(grid) is not int or grid < 1)
            or "edges" in methods and (type(canny_low) is not int or type(canny_high) is not int
                                       or not 0 <= canny_low < canny_high <= 255)):
        raise ValueError("invalid template preprocessing configuration")
    if automatic and prepared is not None and prepared.get("_preferred") in methods[1:]:
        preferred = prepared["_preferred"]
        methods = ["gray", preferred] + [method for method in methods[1:] if method != preferred]
    fallback = None
    for method in methods:
        try:
            result = locate_once(screenshot, template, config, method, prepared)
        except ValueError as exc:
            if str(exc) != "all template scales are larger than roi or constant":
                raise
            continue
        if result["matched"] or result["ambiguous"]:
            if automatic and result["matched"] and prepared is not None:
                prepared["_preferred"] = method
            return result
        if fallback is None or result["confidence"] > fallback["confidence"]:
            fallback = result
    if fallback is None:
        raise ValueError("all template scales are larger than roi or constant")
    return fallback


def ocr_engine():
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise RuntimeError("OCR requires rapidocr and onnxruntime; install requirements.txt") from exc
    return RapidOCR(params={"Global.log_level": "warning", "Global.text_score": 0.0,
                            "Global.return_single_char_box": True})


def ocr_variants(crop: np.ndarray, config: dict, state: dict | None = None):
    """按需生成增强图；命中即停止，避免一次性计算全部预处理。"""
    setting = config.get("ocr_preprocess", "auto")
    methods = (["raw", "upscale", "clahe", "otsu", "adaptive", "invert"]
               if setting == "auto" else setting)
    factor = config["ocr_scale"]
    block = config["ocr_adaptive_block_size"]
    adaptive_c = config["ocr_adaptive_c"]
    allowed = {"raw", "upscale", "clahe", "otsu", "adaptive", "invert"}
    clip = config.get("ocr_clahe_clip_limit", 3.0) if isinstance(methods, list) and "clahe" in methods else None
    grid = config.get("ocr_clahe_grid_size", 8) if isinstance(methods, list) and "clahe" in methods else None
    if (not isinstance(methods, list) or not methods or any(m not in allowed for m in methods)
            or type(factor) is not int or factor < 1
            or type(block) is not int or block < 3 or block % 2 != 1
            or type(adaptive_c) not in (int, float) or not math.isfinite(adaptive_c)
            or "clahe" in methods and (type(clip) not in (int, float) or not math.isfinite(clip)
                                       or clip <= 0 or type(grid) is not int or grid < 1)):
        raise ValueError("invalid OCR preprocessing configuration")
    if setting == "auto" and state is not None and state.get("preferred") in methods[1:]:
        preferred = state["preferred"]
        methods = ["raw", preferred] + [method for method in methods[1:] if method != preferred]
    enlarged = None
    seen = set()
    for method in methods:
        if method == "raw":
            image, image_scale = crop, 1
        else:
            if enlarged is None:
                enlarged = cv2.resize(crop, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
            if method == "upscale":
                image = enlarged
            elif method == "clahe":
                image = cv2.createCLAHE(clipLimit=clip, tileGridSize=(grid, grid)).apply(enlarged)
            elif method == "otsu":
                image = cv2.threshold(enlarged, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
            elif method == "adaptive":
                image = cv2.adaptiveThreshold(enlarged, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                              cv2.THRESH_BINARY, block, adaptive_c)
            else:
                image = cv2.bitwise_not(enlarged)
            image_scale = factor
        # 不同增强方法可能得到相同图像，按尺寸和像素摘要跳过重复推理。
        key = (image.shape, hashlib.blake2b(np.ascontiguousarray(image), digest_size=16).digest())
        if key not in seen:
            seen.add(key)
            yield method, image, image_scale


def image_box(polygon, factor: int, roi: list[int]) -> list[float]:
    """撤销 OCR 放大并加回 ROI 偏移，保持输出为原图坐标。"""
    points = np.asarray(polygon, dtype=float)
    x1, y1 = points.min(axis=0) / factor
    x2, y2 = points.max(axis=0) / factor
    return [float(roi[0] + x1), float(roi[1] + y1), float(x2 - x1), float(y2 - y1)]


def locate_text(screenshot: np.ndarray, target: str, config: dict, engine=None,
                state: dict | None = None) -> dict:
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
    for method, prepared, factor in ocr_variants(crop, config, state):
        output = engine(cv2.cvtColor(prepared, cv2.COLOR_GRAY2BGR), return_word_box=True)
        matches = []
        lines = []
        word_results = output.word_results or ()
        for index, line in enumerate(output.txts or ()):
            score = float(output.scores[index])
            line_box = image_box(output.boxes[index], factor, config["roi"])
            lines.append(location_result(line_box, score, config, method, text=line))
            words = (word_results[index] or ()) if index < len(word_results) else ()
            # 每个字符只规范化一次，随后复用以匹配连续子串及其精确字符框。
            normalized_words = ["".join(str(word[0]).casefold().split()) for word in words]
            line_matches = []
            for start in range(len(words)):
                joined = ""
                word_score = 1.0
                for end in range(start, len(words)):
                    joined += normalized_words[end]
                    word_score = min(word_score, float(words[end][1]))
                    if joined == wanted and word_score >= threshold:
                        points = np.concatenate([np.asarray(word[2], dtype=float)
                                                 for word in words[start:end + 1]])
                        line_matches.append(location_result(image_box(points, factor, config["roi"]),
                                                            word_score, config, method, text=target))
                    if len(joined) >= len(wanted):
                        break
            if line_matches:
                matches.extend(line_matches)
            elif "".join(line.casefold().split()) == wanted and score >= threshold:
                matches.append(location_result(line_box, score, config, method, text=target))
        if len(lines) > len(recognized):
            recognized = lines
        if matches:
            selected = select_match(matches, occurrence)
            if occurrence is not None and occurrence > len(matches):
                if len(matches) > len(partial_matches):
                    partial_matches = matches
                continue
            if selected is not None and state is not None:
                state["preferred"] = method
            return {**(selected or {"confidence": 0.0, "image_bbox": None,
                                    "screen_bbox": None, "image_center": None, "screen_center": None}),
                    "matched": selected is not None, "ambiguous": len(matches) > 1 and occurrence is None,
                    "roi": config["roi"], "recognized": lines, "matches": matches}
    return {"matched": False, "ambiguous": False, "confidence": 0.0,
            "image_bbox": None, "screen_bbox": None, "image_center": None, "screen_center": None,
            "roi": config["roi"], "recognized": recognized, "matches": partial_matches}


def set_dpi_awareness() -> None:
    """使窗口边界、截图和鼠标统一使用物理像素，避免高 DPI 点击偏移。"""
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


def window_classes_at(x: int, y: int) -> list[str]:
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    classes = []
    while hwnd:
        name = ctypes.create_unicode_buffer(256)
        if not user32.GetClassNameW(hwnd, name, len(name)):
            break
        classes.append(name.value)
        hwnd = user32.GetParent(hwnd)
    return classes


def execute_click(result: dict, config: dict, monitors: list) -> list[int]:
    """执行前检查目标、显示器和窗口；调用方负责最新画面与稳定帧校验。"""
    if not result["matched"] or not result["stable"]:
        raise ValueError("target is not matched and stable")
    surface = config.get("target_surface", "window")
    title_expected = config.get("expected_window_title")
    offset = config["click_offset"]
    count = config.get("click_count", 1)
    interval = config.get("click_interval_ms", 0)
    if (surface not in ("window", "desktop")
            or (surface == "window" and (not isinstance(title_expected, str) or not title_expected.strip()))
            or not isinstance(offset, list) or len(offset) != 2
            or type(count) is not int or count not in (1, 2)
            or type(interval) not in (int, float) or not math.isfinite(interval)
            or interval < 0 or (count == 2 and interval == 0)
            or any(type(v) is not int for v in offset)):
        raise ValueError("invalid target_surface, expected_window_title, click_offset, click_count, or click_interval_ms")
    if count == 2 and sys.platform == "win32" and interval >= ctypes.windll.user32.GetDoubleClickTime():
        raise ValueError("click_interval_ms exceeds the Windows double-click time")
    x, y = [round(result["screen_center"][i] + offset[i]) for i in range(2)]
    if not any(monitor_value(m, "left") <= x < monitor_value(m, "left") + monitor_value(m, "width")
               and monitor_value(m, "top") <= y < monitor_value(m, "top") + monitor_value(m, "height")
               for m in monitors[1:]):
        raise ValueError("target is outside connected monitors")
    if surface == "desktop":
        if window_classes_at(x, y)[:2] != ["SysListView32", "SHELLDLL_DefView"]:
            raise ValueError("target is not on the Windows desktop")
    else:
        title, (left, top, right, bottom) = foreground_window()
        if title_expected.casefold() not in title.casefold() or not (left <= x < right and top <= y < bottom):
            raise ValueError("target is outside the expected foreground window")
        if (result.get("capture_window_bounds") is not None
                and result["capture_window_bounds"] != [left, top, right, bottom]):
            raise ValueError("window moved after capture; locate again")
    for index in range(count):
        if index:
            time.sleep(interval / 1000)
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
    try:
        title, bounds = foreground_window()
        foreground = {"title": title, "bounds": bounds}
    except RuntimeError as exc:
        if str(exc) != "no active, visible window":
            raise
        foreground = None
    return {"monitors": monitors, "foreground_window": foreground}


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
        candidate_color = color if candidate["image_bbox"] == result["image_bbox"] else (0, 120, 255)
        cv2.rectangle(canvas, (x, y), (x + round(box_width), y + round(box_height)), candidate_color, 2)
        if result.get("matches"):
            cv2.putText(canvas, str(index), (x, max(12, y - 3)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, candidate_color, 1)
    cv2.putText(canvas, f"confidence={result['confidence']:.3f}", (4, min(height - 4, 18)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(path.suffix, canvas)
    if not success:
        raise OSError(f"cannot write evidence: {path}")
    encoded.tofile(path)  # 用文件字节读写支持 Windows 中文路径。


def read_gray(path: Path) -> np.ndarray:
    """OpenCV 负责解码，numpy 负责路径读写，兼容中文文件名。"""
    data = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE) if data.size else None
    if image is None:
        raise ValueError(f"cannot read image: {path}")
    return image


def capture_gray(sct, region: dict) -> np.ndarray:
    """正常识别和点击前复核共用截图入口，统一截图失败信息。"""
    try:
        screenshot = np.asarray(sct.grab(region))
    except Exception as exc:
        raise RuntimeError(f"capture failed: {exc}") from exc
    return cv2.cvtColor(screenshot, cv2.COLOR_BGRA2GRAY)


def run_live(target: np.ndarray | str, config: dict, execute: bool,
             evidence: Path | None = None, on_frame=None) -> dict:
    """按帧定位；默认只返回结果，显式执行时还需稳定帧和新截图复核。"""
    if execute and sys.platform != "win32":
        raise ValueError("--execute is supported on Windows only")
    validate_config(config)
    if config["physical_pixels_per_image_pixel"] != 1:
        raise ValueError("live capture requires physical_pixels_per_image_pixel = 1")
    count = config["stable_frames"]
    interval = config["frame_interval_ms"]
    shift = config["max_center_shift_px"]
    relative_to = config.get("roi_relative_to", "screen")
    timeout = config.get("timeout_seconds", 0)
    limit = config.get("watch_frames") if on_frame else (None if timeout else count + bool(execute))
    if (type(count) is not int or count < 1
            or type(interval) not in (int, float) or not math.isfinite(interval) or interval < 0
            or type(shift) not in (int, float) or not math.isfinite(shift) or shift < 0
            or (limit is not None and (type(limit) is not int or limit < 1))
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0
            or relative_to not in ("screen", "window")):
        raise ValueError("invalid stable_frames, frame_interval_ms, max_center_shift_px, watch_frames, timeout_seconds, or roi_relative_to")
    if relative_to == "window" and config.get("target_surface", "window") != "window":
        raise ValueError("window-relative ROI requires target_surface = window")
    set_dpi_awareness()
    from mss import MSS

    left, top, width, height = config["roi"]
    engine = ocr_engine() if isinstance(target, str) else None
    ocr_state = {}
    prepared = None if isinstance(target, str) else {}
    previous_region = None
    previous_gray = None
    previous_result = None
    first_center = None
    streak = 0
    ready_to_click = False
    # 模型加载后开始计时；单次 OCR 不强行中断，但超时后绝不发送点击。
    started = time.monotonic()
    deadline = started + timeout if timeout else math.inf
    cache_hits = 0
    timed_out = False
    with MSS() as sct:
        frame = 0
        while limit is None or frame < limit:
            if frame:
                time.sleep(min(interval / 1000, max(0, deadline - time.monotonic())))
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
            origin = config["screen_origin"]
            if relative_to == "window":
                title, (window_left, window_top, window_right, window_bottom) = foreground_window()
                expected = config.get("expected_window_title")
                if (not isinstance(expected, str) or not expected.strip()
                        or expected.casefold() not in title.casefold()
                        or left + width > window_right - window_left
                        or top + height > window_bottom - window_top):
                    raise ValueError("ROI is outside the expected foreground window")
                origin = [window_left, window_top]
            region = {"left": origin[0] + left, "top": origin[1] + top,
                      "width": width, "height": height}
            local_config = {**config, "roi": [0, 0, width, height],
                            "screen_origin": [region["left"], region["top"]]}
            gray = capture_gray(sct, region)
            # 原点和像素都不变才复用结果，窗口移动后必须重新映射屏幕坐标。
            if region == previous_region and np.array_equal(gray, previous_gray):
                result = previous_result.copy()
                cache_hits += 1
            else:
                result = (locate_text(gray, target, local_config, engine, ocr_state)
                          if isinstance(target, str) else locate(gray, target, local_config, prepared))
                previous_region, previous_gray, previous_result = region, gray, result.copy()
            moved = False
            if result["matched"]:
                center = result["screen_center"]
                # 相对本轮首帧判断稳定性，避免缓慢漂移被相邻帧比较漏掉。
                moved = first_center is not None and math.dist(center, first_center) > shift
                if moved:
                    streak = 0
                first_center = center if first_center is None or moved else first_center
                streak += 1
            else:
                first_center = None
                streak = 0
            result["stable"] = streak >= count
            result["clicked"] = False
            if relative_to == "window":
                result["capture_window_bounds"] = [window_left, window_top, window_right, window_bottom]
            timed_out = time.monotonic() >= deadline
            if execute and result["stable"] and ready_to_click and not moved and not timed_out:
                # OCR 耗时期间目标可能改变，输入前再次确认 ROI 像素未变。
                latest = capture_gray(sct, region)
                timed_out = time.monotonic() >= deadline
                if timed_out:
                    result["stable"] = False
                elif not np.array_equal(gray, latest):
                    result["stable"] = False
                    result["reason"] = "frame_changed_before_click"
                    first_center, streak = None, 0
                else:
                    result["click_point"] = execute_click(result, config, sct.monitors)
                    result["clicked"] = True
                    result["click_count"] = config.get("click_count", 1)
            ready_to_click = result["stable"]
            if timed_out:
                result.update(stable=False, timed_out=True, reason="timeout")
            result.update(frames=frame + 1, cache_hits=cache_hits,
                          elapsed_ms=round((time.monotonic() - started) * 1000, 3))
            if on_frame:
                result["frame"] = frame + 1
                on_frame(result)
            frame += 1
            if (timed_out or result["clicked"]
                    or not on_frame and not execute and result["stable"]
                    or not on_frame and not timeout and (not result["matched"] or moved)):
                break
        # 等待间隔内到达截止时间时，最后一帧仍用于提供诊断信息。
        if timed_out and not result.get("timed_out"):
            result = {**result, "stable": False, "timed_out": True, "reason": "timeout",
                      "elapsed_ms": round((time.monotonic() - started) * 1000, 3)}
            if on_frame:
                on_frame(result)
        if evidence is not None:
            save_evidence(gray, result, local_config, evidence)
            result["evidence"] = str(evidence)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--screenshot", type=Path)
    source.add_argument("--live", action="store_true")
    source.add_argument("--inspect", action="store_true", help="查看显示器和前台窗口")
    target_group = parser.add_mutually_exclusive_group()
    target_group.add_argument("--template", type=Path)
    target_group.add_argument("--text", help="使用 OCR 精确定位文字")
    parser.add_argument("--evidence", type=Path, help="保存带识别框的 ROI 图片")
    parser.add_argument("--execute", action="store_true", help="在实时模式下执行点击")
    parser.add_argument("--watch", action="store_true", help="逐帧输出最新位置")
    parser.add_argument("--timeout", type=float, help="实时等待秒数；0 使用原有帧数限制")
    args = parser.parse_args()
    try:
        if args.inspect:
            if args.execute or args.evidence or args.watch or args.timeout is not None:
                raise ValueError("--inspect cannot be combined with --execute, --evidence, --watch, or --timeout")
            print(json.dumps(inspect_desktop()))
            return 0
        if args.config is None or (args.template is None and args.text is None):
            raise ValueError("--config and either --template or --text are required")
        if args.execute and not args.live:
            raise ValueError("--execute requires --live")
        if args.watch and not args.live:
            raise ValueError("--watch requires --live")
        if args.timeout is not None and not args.live:
            raise ValueError("--timeout requires --live")
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if args.timeout is not None:
            config["timeout_seconds"] = args.timeout
        target = args.text if args.text is not None else read_gray(args.template)
        if args.live:
            emit = (lambda item: print(json.dumps(item), flush=True)) if args.watch else None
            result = run_live(target, config, args.execute, args.evidence, emit)
        else:
            screenshot = read_gray(args.screenshot)
            result = (locate_text(screenshot, target, config)
                      if isinstance(target, str) else locate(screenshot, target, config))
            if args.evidence is not None:
                save_evidence(screenshot, result, config, args.evidence)
                result["evidence"] = str(args.evidence)
    except KeyboardInterrupt:
        return 2 if args.execute else 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, cv2.error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not args.watch:
        print(json.dumps(result))
    if args.execute:
        return 0 if result.get("clicked", False) else 2
    if args.watch:
        return 2 if result.get("timed_out", False) else 0
    return 0 if result["matched"] and result.get("stable", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
