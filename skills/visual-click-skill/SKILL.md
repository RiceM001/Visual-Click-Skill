---
name: visual-click-skill
description: 用于 Windows 桌面或应用窗口中的实时图片、文字定位与安全单击、双击；处理 ROI、DPI、多显示器坐标和目标移动。
---

# 视觉定位与安全点击

## 操作流程

1. 从用户截图确认目标文字或模板。框选只用于缩小搜索范围，不直接使用截图中的坐标。用 --inspect 查看显示器和前台窗口；只截目标附近的 ROI。
2. 应用窗口优先设 roi_relative_to 为 window，填写 expected_window_title，让 ROI 随窗口移动；桌面图标设 roi_relative_to 为 screen、target_surface 为 desktop。
3. 用 --live 获取连续稳定帧后的当前目标框、置信度和屏幕坐标。示例配置最多等待 5 秒；--timeout 可覆盖等待时间，目标暂时缺失或移动时自动重试。持续观察用 --watch；重复目标先用 --evidence 查看编号并配置 occurrence。
4. 默认只识别。用户明确要求操作时，再加 --execute：应用按钮用 click_count=1，桌面文件夹用 click_count=2，并配置 click_interval_ms。稳定后复核最新图像，再在点击前抓取 ROI 比较像素；画面改变则停止本次点击。窗口相对 ROI 模式还会核对窗口边界是否移动。--execute 只有真正发送点击后才返回退出码 0。
5. 操作后独立核验新页面标题或资源管理器路径。结果不明时重新识别，最多重试一次，不连续盲点。

## 示例

以下命令以本技能目录为工作目录，或将文件路径改为本技能目录下的绝对路径。配置与依赖清单随技能提供；由代理根据当前窗口填写 ROI、坐标原点和窗口标题，不要求用户选择预处理算法。

~~~bash
python -m pip install -r requirements.txt
python scripts/locate.py --inspect
python scripts/locate.py --config config.example.json --live --text "代理"
python scripts/locate.py --config config.example.json --live --text "代理" --execute
~~~

桌面双击需将 target_surface 改为 desktop、click_count 改为 2，并设定桌面 ROI：

~~~bash
python scripts/locate.py --config config.example.json --live --text "1.6.3" --execute
~~~

识别图片时把 --text 替换为 --template 模板路径。对已保存截图使用 --screenshot。安装验证：python scripts/locate.py --help；完整测试在项目根目录执行 python -m unittest discover -s tests -q。

## 坐标与排障

- 实时截图使用物理像素，physical_pixels_per_image_pixel 必须为 1.0。窗口模式的 roi 相对窗口左上角；屏幕模式使用 screen_origin + roi。副屏原点可能为负。
- 图片与 OCR 预处理默认使用 auto。实时 OCR 先尝试原图，再优先使用上次成功的增强方法；相同像素和尺寸的预处理结果只识别一次。画面及位置不变时复用识别结果。用 preprocess 和 --evidence 核验，模板大小变化时调整 template_scales。
- 出现 frame_changed_before_click 时重新定位；--watch 会继续等待稳定。ROI 应避开无关动画、视频和计时器，避免画面持续变化导致无法点击。退出码 0 只说明输入已发送，操作是否成功仍需按第 5 步核验。
- timeout_seconds 或 --timeout 为正数时，普通实时模式也会自动重试；超时返回 timed_out=true、reason=timeout、退出码 2。0 保持原有帧数限制。计时不含 OCR 模型加载，无法中断正在运行的识别；超时后不开始点击。观察模式还受 watch_frames 限制。
- 用 frames、elapsed_ms、cache_hits 判断定位耗时与缓存是否有效。中文文件名可直接用于模板、截图与证据图；空格路径加引号。
- 置信度不足或目标重复时不点击。点击偏移先核对 ROI、窗口边框、DPI 和坐标映射；桌面被弹窗遮挡时不绕过桌面命中校验。

## 代码复用

scripts/locate.py 中，location_result 统一坐标映射；select_match 统一目标选择；capture_gray 统一截图；read_gray 处理中文路径；run_live 管理稳定帧、超时等待与耗时统计。修改这些规则时复用对应函数，验证命令见上文。
