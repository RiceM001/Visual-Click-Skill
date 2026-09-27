# 🎯 Visual-Click-Skill

从任意应用截图识别图片、文字；在 Windows 前台窗口实时定位并按最新位置点击。无需先写配置；默认只识别，显式使用 --execute 才会操作鼠标。

## 🌟 核心能力

- **实时跟踪**：逐帧更新目标框。目标移动时重新定位，丢失时重置稳定计数。
- **缩放稳定校验**：同时比较目标框左上角和右下角，中心不变但尺寸变化时也会重新累计稳定帧。
- **图片与文字识别**：多尺度模板匹配自动选择灰度、去噪、局部对比度增强、二值化或边缘处理；OCR 自动尝试放大、增强、二值化、反色和单字框定位。
- **准确映射**：处理 ROI 偏移、DPI 缩放和多显示器负坐标；窗口 ROI 可随前台窗口移动。
- **安全点击**：重复目标默认不点击；连续稳定后再取一帧复核最新位置；窗口模式校验前台标题，桌面模式校验图标未被遮挡；支持可配置双击。

## 🚀 快速开始

需要 Python 3.11+。静态截图分析可跨平台运行；实时截图和鼠标操作支持 Windows。OCR 依赖 RapidOCR 与 ONNX Runtime。

~~~bash
python -m pip install -r requirements.txt
~~~

Windows 可先运行 `python skills/visual-click-skill/scripts/locate.py --inspect` 查看前台窗口。直接识别保存的截图或当前前台窗口：

~~~bash
# 截图自动搜索整张图片，返回图片内的目标框
python skills/visual-click-skill/scripts/locate.py --screenshot screenshot.png --text "设置"

# 当前窗口自动确定范围；可用 --roi 缩小搜索区域
python skills/visual-click-skill/scripts/locate.py --live --text "设置" --roi 20 30 300 200

# 已授权点击且目标应用处于前台时，连续帧复核后单击
python skills/visual-click-skill/scripts/locate.py --live --text "设置" --execute
~~~

--roi 使用 [左 上 宽 高] 四个整数：实时模式相对当前窗口，截图模式相对图片。未提供配置时自动确定搜索范围；需要自定义阈值、屏幕原点、桌面双击时可使用 --config config.example.json。截图未提供配置时，coordinate_space=image，screen_center 数值也相对图片，不能直接用于桌面点击。

--live 检查连续稳定帧后返回一次结果。持续观察移动目标时加 --watch；一次点击无需先运行持续观察。

## ⏱️ 等待动态目标

默认最多等待 5 秒。页面加载或目标移动时会重试，满足稳定帧条件后立即返回；需要点击时再加 --execute。也可通过命令覆盖等待时间：

~~~bash
python skills/visual-click-skill/scripts/locate.py --live --text "确认" --timeout 8
~~~

--timeout 对应 timeout_seconds，0 保持原有帧数限制；与 --watch 同用时限制观察总时长，watch_frames 仍可提前结束观察。计时不含 OCR 模型加载，单次识别不能强行中断，但识别或复核结束时若已超时不会点击。超时返回 timed_out=true、reason=timeout 和退出码 2。

有限帧 --watch 结束后，最后结果未匹配或未稳定也返回退出码 2；正常完成且最后结果稳定才返回 0。

实时结果包含 frames、elapsed_ms、cache_hits，分别表示已处理帧数、定位循环耗时和省去的重复识别次数。--watch 在等待间隔内超时，会追加一条最后帧的超时状态。

截图、模板和 --evidence 输出支持中文路径；路径含空格时用引号包裹。

## 📍 选择截图区域

| 场景 | 关键配置 | 坐标含义 |
| --- | --- | --- |
| 应用窗口 | roi_relative_to 为 window；填写 expected_window_title | roi 相对当前前台窗口左上角，每帧随窗口位置更新 |
| 桌面图标 | roi_relative_to 为 screen；target_surface 为 desktop | screen_origin + roi 为物理屏幕截图区域 |
| 已保存截图 | 使用 --screenshot | roi 为图片像素；screen_origin 为图片左上角的物理屏幕坐标 |

无配置的已保存截图默认 screen_origin=[0,0]，只表示图片内坐标。若知道截图实际屏幕原点，可通过 --config 写入 screen_origin。

roi 格式为 [左, 上, 宽, 高]。实时模式中 physical_pixels_per_image_pixel 必须为 1.0。--inspect 可查看显示器及前台窗口范围；桌面没有可用前台窗口时返回 null。

参考图片中的红框只用于确定目标和缩小搜索区域，不能把参考图片坐标当成当前屏幕坐标。窗口移动时优先使用窗口相对 ROI；目标在 ROI 内移动时由实时识别更新点击位置。

## 🖱️ 单击、双击与核验

应用按钮设置 target_surface 为 window、click_count 为 1，并填写 expected_window_title。桌面文件夹设置 target_surface 为 desktop、click_count 为 2；双击间隔由 click_interval_ms 控制，必须短于系统双击时间。

执行前要求唯一匹配、置信度达标、连续帧稳定，且点击点位于显示器内。桌面图标若被其他窗口遮挡，操作会被拒绝。操作后应独立核对结果，例如识别新页面标题，或检查资源管理器是否打开目标文件夹；结果不明时重新截图定位，最多重试一次。

~~~bash
# 桌面文件夹：先在配置中选 desktop 和 click_count=2
python skills/visual-click-skill/scripts/locate.py --config config.example.json --live --text "1.6.3" --execute

# 图片模板：先在配置中填写目标窗口标题和窗口相对 ROI
python skills/visual-click-skill/scripts/locate.py --config config.example.json --live --template path/to/button.png --execute
~~~

## 🔎 识别不稳时

- 先缩小 ROI，核对模板或文字、阈值、DPI 与窗口位置。
- 图片尺寸变化时配置 template_scales；图片和文字预处理默认均为 auto，无需选择方法。识别结果的 preprocess 字段会显示本次采用的方法。
- 图片识别先尝试灰度；未命中才尝试边缘、去噪、CLAHE 和 Otsu。实时模式优先复用上帧成功的方法；画面及截图位置完全不变时复用识别结果。增强方法可能放大噪声，先用 --evidence 核对结果再点击。
- OCR 会在原图失败后优先尝试上次成功的方法，并跳过像素及尺寸完全相同的增强结果，减少重复推理。
- 点击前会再次截图比较 ROI，阻止识别耗时期间画面变化造成的旧位置点击。出现 frame_changed_before_click 时重新定位；ROI 应避开无关动画。--execute 未发送点击时退出码为 2，发送后仍需核验应用结果。
- 同一 ROI 出现多个相同目标时，先用 --evidence 查看编号，再设置 template_occurrence 或 ocr_occurrence。
- template_max_candidates 在跨尺度候选去重前限制原始峰值总数，超限直接拒绝匹配，避免低阈值或重复纹理产生大量候选。此时证据图不是完整候选列表；应缩小 ROI 或提高 threshold，必要时再调整数量上限。
- max_center_shift_px 现用于限制目标框两角相对本轮首帧的位移，同时覆盖平移、缩放和缓慢漂移。
- --evidence 只保存带识别框的 ROI 小图，不保存全屏。

## 📦 安装技能

将 skills/visual-click-skill 复制到代理的技能目录，或运行：

~~~bash
npx skills add RiceM001/Visual-Click-Skill --skill visual-click-skill
~~~

技能文件遵循 [Agent Skills 规范](https://agentskills.io/specification)。

技能目录自带 requirements.txt 和 config.example.json，单独安装后也可运行。技能内命令以技能目录为工作目录；预处理保持 auto，由代理根据目标窗口填写 ROI。

## 🧪 验证

~~~bash
python -m unittest discover -s tests -q
~~~

## 🔗 参考项目

- [Airtest](https://github.com/AirtestProject/Airtest/blob/master/airtest/core/api.py)：参考 wait 的超时和轮询间隔设计，加入动态目标等待。
- [PyScreeze](https://github.com/asweigart/pyscreeze/blob/master/pyscreeze/__init__.py)：参考重复截图搜索；本项目结合稳定帧和点击前复核。
- [PyAutoGUI](https://github.com/asweigart/pyautogui/blob/master/docs/screenshot.rst)：参考 ROI 搜索和候选框定位思路，继续复用局部截图以减少计算。

## 📄 许可证

MIT，详见 [LICENSE](LICENSE)。
