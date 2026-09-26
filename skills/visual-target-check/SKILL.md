---
name: visual-target-check
description: Use when building or debugging Python automation that locates an image template or exact OCR text and optionally clicks it, especially with ROI, DPI, or multiple monitors. 适用于图片或精确文字定位、可选点击及 ROI、DPI、多屏坐标排查。
---

# Visual Target Check

## 使用 / Use

1. 阅读项目现有的截图、匹配、坐标与输入路径，复用原有库和配置。缺少素材时只索取小范围 ROI 截图与模板。 / Read existing capture, matching, coordinate, and input paths. Reuse project libraries and configuration. Request only a small ROI screenshot and template if missing.
2. 先用 `--inspect` 查看显示器与前台窗口范围。记录截图左上角的**物理**屏幕坐标、图片尺寸、图片像素中的 ROI、比例、模板路径和阈值；副屏原点可能为负。 / Use `--inspect` for monitor and foreground-window geometry. Record physical origin, image size, ROI, scale, template path, and threshold; secondary monitors can have negative origins.
3. 用 `--template` 定位固定外观目标；用 `--text` 定位确切文字。`--screenshot` 处理保存的图片，`--live` 连续截取 ROI；两者默认不点击。 / Use `--template` for fixed visual targets and `--text` for exact OCR text. `--screenshot` handles saved images; `--live` captures repeated ROI frames. Both default to dry-run.
4. 报告置信度、阈值、ROI、图片与屏幕框/中心及坐标假设。需要复核时用 `--evidence` 保存带框 ROI。OCR 先跑原图，失败再按配置尝试放大、二值化和反色；行内部分文字必须由字词框定位。 / Report confidence, threshold, ROI, image/screen boxes and centers. Use `--evidence` for an annotated ROI. OCR tries raw input first, then configured preprocessing; partial-line text requires word boxes.
5. 仅在用户明确要求或配置允许时使用 `--live --execute`。先确认多帧稳定、目标在显示器及预期前台窗口内；Windows 上须启用 per-monitor DPI awareness。修改项目代码时保留可配置的阈值、ROI、偏移、延时和帧数。 / Use `--live --execute` only when explicitly requested or enabled. Check multi-frame stability, monitor bounds, expected foreground window, and Windows per-monitor DPI awareness. Keep thresholds, ROI, offset, interval, and frame count configurable.

## 坐标规则 / Coordinate rule

每个轴均使用 `screen_center = screen_origin + image_center * physical_pixels_per_image_pixel`。`image_center` 已包含 ROI 偏移，不要重复相加。物理像素截图通常使用 `1.0`。 / Apply the formula on each axis. `image_center` already includes the ROI offset; do not add it twice. Physical-pixel screenshots normally use `1.0`.

## 示例 / Example

```bash
python skills/visual-target-check/scripts/locate.py --inspect
python skills/visual-target-check/scripts/locate.py --config config.example.json --screenshot screenshot.png --template button.png
python skills/visual-target-check/scripts/locate.py --config config.example.json --live --template button.png --evidence match.png
python skills/visual-target-check/scripts/locate.py --config config.example.json --live --text "设置" --evidence text.png
# 真实点击 / Real click, after setting expected_window_title:
python skills/visual-target-check/scripts/locate.py --config config.example.json --live --text "设置" --execute
python -m unittest discover -s tests -v
```

## 故障线索 / Failure clues

- 常量模板或模板大于 ROI：更换模板或 ROI；归一化匹配不可靠或无法执行。 / Constant or oversized template: replace the template or ROI.
- 置信度高但点击偏移：检查截图原点、图片缩放、DPI aware、窗口边框和负坐标显示器。 / Good confidence but wrong click: check origin, resizing, DPI, borders, and negative monitor coordinates.
- 置信度低：检查 ROI 和模板外观，再按目标考虑灰度、多模板或 OCR。 / Low confidence: inspect ROI and template, then consider grayscale, multiple templates, or OCR.
- OCR 未命中：缩小 ROI，检查 `recognized`、`ocr_threshold` 与预处理顺序。相同文字出现多处时设置 `ocr_occurrence`；默认拒绝含糊点击。 / OCR miss: narrow the ROI and inspect `recognized`, threshold, and preprocessing order. Use `ocr_occurrence` for repeated text; ambiguous clicks are blocked by default.
