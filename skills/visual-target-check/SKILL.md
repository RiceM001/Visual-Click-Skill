---
name: visual-target-check
description: Use for live Python image or OCR targeting and guarded clicks on Windows application windows or desktop icons, including ROI, DPI, and multi-monitor coordinates. 适用于 Windows 窗口或桌面图标的实时图片、文字定位与安全点击。
---

# Visual Target Check

## 使用 / Use

1. 阅读项目现有的截图、匹配、坐标与输入路径，复用原有库和配置。缺少素材时只索取小范围 ROI 截图与模板。 / Read existing capture, matching, coordinate, and input paths. Reuse project libraries and configuration. Request only a small ROI screenshot and template if missing.
2. 先用 `--inspect` 查看显示器与前台窗口范围。记录截图左上角的**物理**屏幕坐标、图片尺寸、图片像素中的 ROI、比例、模板路径和阈值；副屏原点可能为负。 / Use `--inspect` for monitor and foreground-window geometry. Record physical origin, image size, ROI, scale, template path, and threshold; secondary monitors can have negative origins.
3. 用 `--template` 定位图片；目标缩放时配置 `template_scales`。多个模板候选默认阻止点击，用 `--evidence` 和 `template_occurrence` 选定目标。用 `--text` 定位确切文字；低对比度时将 `clahe` 加入 `ocr_preprocess`。`--screenshot` 和 `--live` 默认都不点击。 / Use `--template` for images and `template_scales` when their size varies. Multiple candidates block clicks until checked with `--evidence` and selected by `template_occurrence`. Use `--text` for exact OCR text and add `clahe` for low contrast. Saved screenshot and live modes both default to dry-run.
4. 报告置信度、阈值、ROI、图片与屏幕框/中心及坐标假设。用 `--evidence` 保存带编号候选框的 ROI，再用 `ocr_occurrence` 选择重复文字。OCR 启用单字框定位词内子串；先跑原图，候选数量不足时继续尝试放大、二值化和反色。 / Report confidence, threshold, ROI, image/screen boxes and centers. Use `--evidence` to number candidate boxes before selecting repeated text with `ocr_occurrence`. OCR uses character boxes for substrings inside words, then preprocessing if raw input yields too few candidates.
5. 仅在用户明确要求或配置允许时使用 `--live --execute`。先确认多帧稳定、目标在显示器内；窗口模式还要确认预期前台窗口，桌面模式要确认点击点属于桌面图标列表。Windows 上须启用 per-monitor DPI awareness。 / Use `--live --execute` only when explicitly requested or enabled. Check stability and monitor bounds; window mode checks the foreground window, and desktop mode checks the desktop icon view. Enable per-monitor DPI awareness on Windows.
6. 目标可能移动时用 `--live --watch` 逐帧读取最新 `screen_bbox`，仅在 `matched: true` 时使用坐标；`watch_frames` 可限制帧数，`null` 表示持续观察。 / For moving targets, use `--live --watch` to read the current `screen_bbox` per frame; use coordinates only when `matched: true`. `watch_frames` bounds the stream, or `null` keeps observing.

## 实时操作 / Live actions

- 参考截图的框选只说明目标，不提供可直接点击的屏幕坐标。先用 `--inspect`、当前窗口边界和小范围 ROI 建立物理坐标，再以 `--live --watch` 核对唯一目标及连续稳定帧。 / A marked reference image identifies the target, not a click coordinate. Use `--inspect`, current window bounds, and a small ROI to establish physical coordinates; confirm a unique, stable live match.
- 应用按钮：`target_surface: "window"`，填写 `expected_window_title`，`click_count: 1`。桌面文件夹：显示桌面后设置 `target_surface: "desktop"`，`click_count: 2` 与 `click_interval_ms`；桌面被弹窗遮挡时停止。 / App button: window surface, expected title, one click. Desktop folder: reveal desktop, select desktop surface, two clicks with a configured interval; stop if an overlay covers it.
- 执行后用独立信号核验：应用的新页面标题用 OCR，文件夹用资源管理器窗口名称或路径。结果不明时先重新截图定位，最多重试一次；不得连续盲点。 / Verify with an independent signal: OCR the destination page heading or check the Explorer window name/path. If unclear, reacquire and retry at most once; never click blindly in a loop.

## 坐标规则 / Coordinate rule

每个轴均使用 `screen_center = screen_origin + image_center * physical_pixels_per_image_pixel`。`image_center` 已包含 ROI 偏移，不要重复相加。物理像素截图通常使用 `1.0`。 / Apply the formula on each axis. `image_center` already includes the ROI offset; do not add it twice. Physical-pixel screenshots normally use `1.0`.

## 示例 / Example

```bash
python skills/visual-target-check/scripts/locate.py --inspect
python skills/visual-target-check/scripts/locate.py --config config.example.json --screenshot screenshot.png --template button.png
python skills/visual-target-check/scripts/locate.py --config config.example.json --live --template button.png --evidence match.png
python skills/visual-target-check/scripts/locate.py --config config.example.json --live --text "设置" --evidence text.png
python skills/visual-target-check/scripts/locate.py --config config.example.json --live --watch --text "设置"
# 窗口点击 / Window click, after setting expected_window_title:
python skills/visual-target-check/scripts/locate.py --config config.example.json --live --text "设置" --execute
# 桌面双击 / Desktop double-click, after setting target_surface=desktop and click_count=2:
python skills/visual-target-check/scripts/locate.py --config config.example.json --live --watch --text "1.6.3" --execute
python -m unittest discover -s tests -v
```

## 故障线索 / Failure clues

- 常量模板或模板大于 ROI：更换模板或 ROI；归一化匹配不可靠或无法执行。 / Constant or oversized template: replace the template or ROI.
- 置信度高但点击偏移：检查截图原点、图片缩放、DPI aware、窗口边框和负坐标显示器。 / Good confidence but wrong click: check origin, resizing, DPI, borders, and negative monitor coordinates.
- 置信度低：检查 ROI 和模板外观，再按目标考虑灰度、多模板或 OCR。 / Low confidence: inspect ROI and template, then consider grayscale, multiple templates, or OCR.
- 模板大小变化或重复：配置 `template_scales`；用证据图确认编号后设置 `template_occurrence`。超过 `template_max_candidates` 上限时缩小 ROI 或提高阈值。 / Resized or repeated templates: configure scales; inspect numbered evidence before setting occurrence. If the candidate limit is exceeded, narrow the ROI or raise the threshold.
- OCR 未命中：缩小 ROI，检查 `recognized`、`ocr_threshold` 与预处理顺序。相同文字出现多处时设置 `ocr_occurrence`；默认拒绝含糊点击。 / OCR miss: narrow the ROI and inspect `recognized`, threshold, and preprocessing order. Use `ocr_occurrence` for repeated text; ambiguous clicks are blocked by default.
