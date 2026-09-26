---
name: visual-target-check
description: 用于 Windows 桌面或应用窗口中的实时图片、文字定位与安全单击、双击；处理 ROI、DPI、多显示器坐标和目标移动。
---

# 视觉定位与安全点击

## 操作流程

1. 从用户截图确认目标文字或模板。框选只用于缩小搜索范围，不直接使用截图中的坐标。用 --inspect 查看显示器和前台窗口；只截目标附近的 ROI。
2. 应用窗口优先设 roi_relative_to 为 window，填写 expected_window_title，让 ROI 随窗口移动；桌面图标设 roi_relative_to 为 screen、target_surface 为 desktop。
3. 用 --live 获取连续稳定帧后的当前目标框、置信度和屏幕坐标。需要持续观察移动目标时才用 --watch；重复目标先用 --evidence 查看编号并配置 occurrence。
4. 默认只识别。用户明确要求操作或配置明确允许时，再加 --execute：应用按钮用 click_count=1，桌面文件夹用 click_count=2，并配置 click_interval_ms。脚本在稳定帧后再取一帧复核最新位置，并校验显示器边界、前台窗口或桌面图标视图。
5. 操作后独立核验新页面标题或资源管理器路径。结果不明时重新识别，最多重试一次，不连续盲点。

## 示例

先按当前窗口修改 config.example.json 中的 roi、roi_relative_to、expected_window_title：

~~~bash
python skills/visual-target-check/scripts/locate.py --inspect
python skills/visual-target-check/scripts/locate.py --config config.example.json --live --text "代理"
python skills/visual-target-check/scripts/locate.py --config config.example.json --live --text "代理" --execute
~~~

桌面双击需将 target_surface 改为 desktop、click_count 改为 2，并设定桌面 ROI：

~~~bash
python skills/visual-target-check/scripts/locate.py --config config.example.json --live --text "1.6.3" --execute
~~~

识别图片时把 --text 替换为 --template 模板路径。对已保存截图使用 --screenshot。验证命令：python -m unittest discover -s tests -q。

## 坐标与排障

- 实时截图使用物理像素，physical_pixels_per_image_pixel 必须为 1.0。窗口模式的 roi 相对窗口左上角；屏幕模式使用 screen_origin + roi。副屏原点可能为负。
- OCR 先处理原始 ROI，再按 ocr_preprocess 尝试增强；模糊文字先缩小 ROI。模板大小变化时调整 template_scales。
- 置信度不足或目标重复时不点击。点击偏移先核对 ROI、窗口边框、DPI 和坐标映射；桌面被弹窗遮挡时不绕过桌面命中校验。
