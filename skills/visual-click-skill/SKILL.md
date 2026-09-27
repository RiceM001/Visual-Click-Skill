---
name: visual-click-skill
description: 通用图片和文字定位：跨平台分析截图，在 Windows 任意前台窗口实时跟踪并安全单击或双击；处理 ROI、DPI 和目标移动。
---

# 视觉定位与安全点击

## 操作流程

1. 从用户截图确认目标文字或模板。参考框只用于缩小搜索范围，不直接使用旧坐标。静态图片用 --screenshot；Windows 当前窗口用 --live。两者均可不写配置文件。
2. 默认搜索整张截图或当前前台窗口；区域已知时加 --roi X Y W H。无配置且不传 --roi 时，整窗口 ROI 每帧随窗口大小变化；显式 ROI 只随窗口移动。截图模式的 ROI 相对图片。桌面图标等特殊目标可通过 --config 指定 target_surface 为 desktop。
3. 用 --live 获取连续稳定帧后的当前目标框、置信度和屏幕坐标。示例配置最多等待 5 秒；--timeout 可覆盖等待时间，目标暂时缺失或移动时自动重试。持续观察用 --watch；重复目标先用 --evidence 查看编号并配置 occurrence。
4. 默认只识别。用户明确要求操作时，再加 --execute：应用按钮用 click_count=1，桌面文件夹用 click_count=2，并配置 click_interval_ms。稳定后复核最新图像，再在点击前抓取 ROI 比较像素；画面改变则停止本次点击。窗口相对 ROI 模式还会核对窗口边界是否移动。--execute 只有真正发送点击后才返回退出码 0。
5. 操作后独立核验新页面标题或资源管理器路径。结果不明时重新识别，最多重试一次，不连续盲点。

## 模型分工

- 首次确定目标、处理歧义或异常时使用当前主模型。流程确认后，若需要对多个目标或页面重复相同的识别与结果整理，且运行环境支持指定模型，则交给 `gpt-6-luna`（推理强度 `xhigh`）；每次操作仍按上述定位、复核规则执行。
- 截图、OCR、模板匹配和循环由本地脚本完成，不因重复运行而逐次调用语言模型。无法指定模型时沿用当前模型，不声称已经切换。

## 示例

以下命令以本技能目录为工作目录。预处理自动选择；只有需要自定义阈值、坐标原点、桌面双击等行为时才使用 --config。

~~~bash
python -m pip install -r requirements.txt
python scripts/locate.py --inspect
python scripts/locate.py --screenshot screenshot.png --text "设置"
python scripts/locate.py --live --text "设置" --roi 20 30 300 200
python scripts/locate.py --live --text "设置" --execute
~~~

桌面双击需将 target_surface 改为 desktop、click_count 改为 2，并设定桌面 ROI：

~~~bash
python scripts/locate.py --config config.example.json --live --text "1.6.3" --execute
~~~

识别图片时把 --text 换成 --template 模板路径。截图若未提供 --config，结果的 coordinate_space 为 image；screen_center 数值此时也相对图片，不能用于桌面点击。点击前改用 --live 重新定位。安装验证：python scripts/locate.py --help；完整测试在项目根目录执行 python -m unittest discover -s tests -q。

## 坐标与排障

- 实时截图使用物理像素，physical_pixels_per_image_pixel 必须为 1.0。窗口模式的 roi 相对窗口左上角；屏幕模式使用 screen_origin + roi。副屏原点可能为负。
- 多屏关开后，窗口模式逐帧读取当前边界；点击前重新枚举显示器，避免使用 MSS 缓存的旧范围。显式指定的屏幕相对 ROI 仍需按当前布局核对。
- 不传 --config 的实时模式取当前窗口标题与边界，支持不同应用；执行前先让目标应用处于前台。若截图有已知桌面原点，使用 --config 设置 screen_origin 才能输出真实屏幕坐标。
- 图片与 OCR 预处理默认使用 auto；模板尺寸默认尝试 0.5～2 倍的常见比例，超出范围时再配置 template_scales 数字列表。实时 OCR 先尝试原图，再优先使用上次成功的增强方法；相同像素和尺寸的预处理结果只识别一次。画面及位置不变时复用识别结果。用 template_scale、preprocess 和 --evidence 核验。
- 出现 frame_changed_before_click 时重新定位；--watch 会继续等待稳定。ROI 应避开无关动画、视频和计时器，避免画面持续变化导致无法点击。退出码 0 只说明输入已发送，操作是否成功仍需按第 5 步核验。
- timeout_seconds 或 --timeout 为正数时，普通实时模式也会自动重试；超时返回 timed_out=true、reason=timeout、退出码 2。0 保持原有帧数限制。计时不含 OCR 模型加载，无法中断正在运行的识别；超时后不开始点击。观察模式还受 watch_frames 限制。
- 用 frames、elapsed_ms、cache_hits 判断定位耗时与缓存是否有效。中文文件名可直接用于模板、截图与证据图；空格路径加引号。
- 置信度不足或目标重复时不点击。点击偏移先核对 ROI、窗口边框、DPI 和坐标映射；桌面被弹窗遮挡时不绕过桌面命中校验。
- 稳定性比较目标框两角与本轮首帧的距离，沿用 max_center_shift_px 作为像素容差，避免中心不变的缩放被误判为稳定。有限帧观察结束时，最后一帧未匹配或未稳定返回退出码 2。
- template_max_candidates 限制去重前的原始峰值总数；candidate_limit_reached=true 时不能依赖不完整列表选择 occurrence，应先缩小 ROI 或提高 threshold。

## 代码复用

scripts/locate.py 中，location_result 统一坐标映射；select_match 统一目标选择；capture_gray 统一截图；read_gray 处理中文路径；run_live 管理稳定帧、超时等待与耗时统计。修改这些规则时复用对应函数，验证命令见上文。
