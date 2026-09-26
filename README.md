# Visual Target Check

从已保存的截图或实时屏幕 ROI 定位**图片模板或指定文字**，输出目标的物理屏幕坐标。默认 dry-run；只有显式使用 `--live --execute` 才会点击。

Locate an image template or exact text in a saved screenshot or live screen ROI, then print its physical screen coordinate. Dry-run is the default; only `--live --execute` clicks.

用于排查“识别成功但点击偏移”：ROI 偏移、截图原点、DPI 缩放及负坐标显示器。项目遵循 [Agent Skills 格式](https://agentskills.io/specification)。

It diagnoses the gap between “template matched” and “click landed”: ROI offsets, screenshot origins, DPI scaling, and monitors with negative coordinates. It follows the [Agent Skills format](https://agentskills.io/specification).

**GitHub 简介 / About:** 图片与文字定位、OCR 增强、多屏坐标映射及安全点击 / Image and OCR text targeting, preprocessing, multi-monitor coordinates, and guarded clicks.

## 快速开始 / Quick start

需要 Python 3.11+。实时模式支持 Windows；OCR 使用 [RapidOCR](https://github.com/RapidAI/RapidOCR) 与 ONNX Runtime，无需另装 Tesseract。 / Requires Python 3.11+. Live mode supports Windows; OCR uses [RapidOCR](https://github.com/RapidAI/RapidOCR) and ONNX Runtime without a separate Tesseract install.

```bash
python -m pip install -r requirements.txt
python skills/visual-target-check/scripts/locate.py --inspect
python skills/visual-target-check/scripts/locate.py --config config.example.json --screenshot path/to/screenshot.png --template path/to/button.png
python skills/visual-target-check/scripts/locate.py --config config.example.json --live --template path/to/button.png --evidence match.png
python skills/visual-target-check/scripts/locate.py --config config.example.json --live --text "设置" --evidence text.png
python skills/visual-target-check/scripts/locate.py --config config.example.json --live --watch --text "设置"
python -m unittest discover -s tests -v
```

`--inspect` 输出显示器范围和前台窗口标题/边界，帮助填写配置。`--evidence` 仅保存带匹配框的 ROI 小图，不保存全屏截图。

`--inspect` prints monitor geometry and the foreground window's title/bounds to help configure the tool. `--evidence` saves only the annotated ROI, never the full screen.

## 图片定位 / Image targeting

`template_scales` 支持多个缩放比例，例如 `[0.75, 1.0, 1.25]`，适合 DPI 或界面缩放变化。每个比例的候选框经过 `template_nms_iou` 去重；同一 ROI 内有多个目标时默认阻止点击，先用 `--evidence` 查看编号，再设置从上到下、从左到右的 `template_occurrence`（1 起）。`template_max_candidates` 限制候选数量；超过上限时拒绝点击。比例越多，匹配耗时越长。

`template_scales` accepts multiple sizes, such as `[0.75, 1.0, 1.25]`, for DPI or UI scaling changes. `template_nms_iou` removes overlapping candidates across scales. Multiple targets block clicks by default; inspect their numbers with `--evidence`, then set the 1-based `template_occurrence` from top to bottom and left to right. `template_max_candidates` bounds the search; exceeding it blocks clicks. More scales take longer to match.

## 文字定位 / OCR text targeting

`--text` 精确匹配指定文字。OCR 先尝试原始 ROI，再按 `ocr_preprocess` 顺序尝试放大、CLAHE 局部对比度增强、Otsu 二值化、自适应二值化和反色；找到达到 `ocr_threshold` 且满足 `ocr_occurrence` 的结果才停止。启用单字框以定位词内子串；只有整行完全匹配时才回退到行框。CLAHE 强度与网格由 `ocr_clahe_clip_limit`、`ocr_clahe_grid_size` 控制。

`--text` matches exact text. OCR tries the raw ROI first, then the configured sequence of upscaling, CLAHE local contrast enhancement, Otsu, adaptive thresholding, and inversion. It stops when matches pass `ocr_threshold` and satisfy `ocr_occurrence`. Single-character boxes locate substrings inside words; a full-line box is used only for an exact full-line match. `ocr_clahe_clip_limit` and `ocr_clahe_grid_size` tune CLAHE.

```bash
python skills/visual-target-check/scripts/locate.py --config config.example.json --screenshot path/to/screenshot.png --text "设置"
python skills/visual-target-check/scripts/locate.py --config config.example.json --live --text "设置" --execute
```

结果含识别文字、置信度、`image_bbox`、`screen_bbox`、中心点、预处理方法及同一 ROI 中识别出的文字。若同一文字出现多处，默认返回 `ambiguous: true` 并禁止点击；`--evidence` 会给每个候选框编号。在配置中设置 `ocr_occurrence`（从上到下、从左到右，1 起）才选择其中一个。

Results include recognized text, confidence, image and screen boxes, centers, the preprocessing method, and OCR lines found in the ROI. Duplicate targets return `ambiguous: true` and block clicks by default; `--evidence` numbers every candidate box. Set the 1-based `ocr_occurrence` (top-to-bottom, left-to-right) to select one.

运行前按截图修改 `config.example.json`。`roi` 是截图像素中的 `[left, top, width, height]`；`screen_origin` 是截图左上角的物理屏幕坐标；物理像素截图的 `physical_pixels_per_image_pixel` 为 `1.0`，仅在图片被缩放或以逻辑像素截取时修改。

Edit `config.example.json` for your screenshot. `roi` is `[left, top, width, height]` in image pixels. `screen_origin` is the physical screen coordinate of the screenshot's top-left corner. Use `physical_pixels_per_image_pixel: 1.0` for a physical-pixel screenshot; change it only for resized or logical-pixel images.

实时模式中，`screen_origin + roi` 是物理屏幕上的截图区域；`physical_pixels_per_image_pixel` 必须为 `1.0`。`stable_frames`、`frame_interval_ms` 和 `max_center_shift_px` 控制多帧稳定性。将 `expected_window_title` 改为目标窗口标题的独特片段，再执行真实点击：

In live mode, `screen_origin + roi` defines the physical capture region; the scale must be `1.0`. `stable_frames`, `frame_interval_ms`, and `max_center_shift_px` control stability. Set `expected_window_title` to a distinctive part of the target window title before clicking:

`--live --watch` 持续重截 ROI，每帧输出一行 JSON，包括最新的 `screen_bbox`、`screen_center`、`matched`、`stable` 和帧号；目标移动后坐标随帧更新，丢失时稳定计数清零。`watch_frames: null` 持续运行到 Ctrl+C；设置正整数可限制帧数。只有 `--watch --execute` 会在最新位置连续稳定后点击一次并退出；`matched: false` 时不要使用候选坐标。`--evidence` 在达到帧数上限或点击后保存最后一帧。

`--live --watch` recaptures the ROI and emits one JSON line per frame with the current `screen_bbox`, `screen_center`, `matched`, `stable`, and frame number. Positions follow moving targets; loss resets stability. `watch_frames: null` runs until Ctrl+C; a positive integer limits frames. `--watch --execute` clicks once after fresh stable matches, then exits. Ignore candidate coordinates when `matched: false`. `--evidence` saves the final frame after the frame limit or a click.

```bash
python skills/visual-target-check/scripts/locate.py --config config.example.json --live --template path/to/button.png --execute
```

点击前检查连续匹配、显示器边界和预期前台窗口；不会自动切换窗口。 / Before clicking, the tool checks repeated matches, monitor bounds, and the expected foreground window. It never activates a window.

输出示例 / Example output:

```json
{"matched": true, "confidence": 0.991, "image_center": [148, 72], "screen_center": [148, 72], "roi": [100, 40, 200, 100]}
```

上述数字仅展示格式；实际结果由图片决定。未匹配时返回 `matched: false`，退出码为 `2`；输入无效时退出码为 `1`。

The numbers only illustrate the output shape. An unmatched result has `matched: false` and exits with code `2`; invalid input exits with code `1`.

## 安装 Skill / Install the skill

将 `skills/visual-target-check` 复制到代理的 skills 目录。发布到 GitHub 后，也可运行 `npx skills add <owner>/<repo> --skill visual-target-check`。CLI 可离线诊断或实时定位；Skill 还可指导修改现有 Python 自动化项目。

Copy `skills/visual-target-check` to your agent's skills directory. After publication, you can also run `npx skills add <owner>/<repo> --skill visual-target-check`. The CLI supports offline diagnosis and live targeting; the skill also guides changes to existing Python automation projects.

## 范围 / Scope

实时模式只截取 ROI，不激活窗口；`--execute` 仅支持 Windows。OCR 精度取决于字体、清晰度和模型输出；若字词框缺失，行内部分文字不会按整行框点击。请先确认截图 DPI 模式、图片缩放、ROI、阈值和窗口状态。

Live mode captures only the ROI and never activates a window. `--execute` is Windows-only. OCR accuracy depends on image quality and model output; partial-line text without word boxes will not be clicked using a whole-line box. Check DPI mode, image scale, ROI, threshold, and window state first.

## 许可证 / License

MIT，见 [LICENSE](LICENSE)。 / MIT; see [LICENSE](LICENSE).
