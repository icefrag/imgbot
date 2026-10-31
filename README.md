# imgbot · 双目标图色脚本基础库

PC 窗口(Win32) + 安卓模拟器(adb) 双后端的图色自动化基础库:
模板匹配 / 色键透明模板 / 多点找色 / 区域找色族 / OCR / 模板逐字读数 / 拟人键鼠 / 中文输入。
源自两个实战项目的合并: 模拟器-魔神(安卓侧) + LZG_PMH(PC 侧)。

## 引用(消费项目)

    # pyproject.toml
    [project]
    dependencies = ["imgbot"]
    [tool.uv.sources]
    imgbot = { path = "../imgbot" }        # 同机开发; 跨机器改 git+tag

    # 入口处
    from imgbot import vision
    vision.set_templates_dir(PROJECT_ROOT / "templates")

## 快速上手

```python
from imgbot.target import AdbTarget, Win32Target
from imgbot.win32.window import GameWindow
from imgbot import vision

# 安卓模拟器
t = AdbTarget("127.0.0.1:5555")
# PC 窗口(大漠 BindWindow 思想: 绑定一次, 全打它)
t = Win32Target(GameWindow.find(class_name="XXX"))

hit = vision.find(t.screenshot(), vision.load("btn.png"))   # (x, y, score) 或 None
t.tap(*hit[:2])
```

**方法决策树/验收门槛见 AGENTS.md(动手前必读); 多点找色选点对接见 多点找色Pt规范.md。**

## 能力清单(按模块目录, 供 AI 直接查用)

通道序**一律 BGR**(cv2/adb/PrintWindow 统一, 别插 RGB 转换)。阈值默认 `0.85` 不动。
下列签名均由 `imgbot/` 源码实际导出, 非笔录。

### `imgbot/target.py` —— 绑定目标(大漠 BindWindow 思想: 绑定一次, 截图/输入全打它)

| API | 说明 |
|---|---|
| `AdbTarget(serial, adb_path="adb")` | 安卓模拟器/真机后端 |
| `Win32Target(hwnd_or_window, capture="printwindow", input="postmessage")` | PC 窗口后端(通道参数留位) |
| `Target` | 最小公共接口基类: `screenshot() -> BGR ndarray` / `tap(x, y)` |

- `AdbTarget` 另有: `serial`(属性)、`save_screenshot(path)`、`swipe(...)`、`keyevent(code)`、
  `shell(*args)`、`type_text(text)`、`start_app(pkg, act)`、`stop_app(pkg)`。
- `Win32Target` 另有: `double_click(x, y)`、`hover(x, y, duration=1.5)`、`type_text(text)`、
  `paste(text)`、`swipe(...)`(未实现, 会抛)、`keyevent(code)`(windows 通道 = 带扫描码
  消息键, 只达从消息循环读键的目标; 轮询型游戏免疫 → 用 `input="dx"`)。

### `imgbot/vision.py` —— 图色层(吃 BGR ndarray, 与后端无关)

| API | 返回 | 说明 |
|---|---|---|
| `set_templates_dir(path)` | None | 入口设一次模板目录(默认 `./templates` 相对 CWD) |
| `load(name) -> ndarray` | 模板图 | 按名加载并缓存; **四角≥3/4同色即自动键控成 RGBA** |
| `decode_png(png_bytes) -> ndarray` | BGR | adb PNG bytes → 图像 |
| `key_out_background(name, tol=8) -> float` | 保留笔画占比 | 手动键控(旧流程兼容, 一般不用) |
| `find(screen, tpl, threshold=0.85, region=None)` | `(x, y, score)` \| None | 单目标模板匹配; `region=(x1,y1,x2,y2)` 限矩形, 返回**全屏**坐标 |
| `find_any(screen, names, threshold=0.85)` | `(x, y, score)` \| None | 多模板任一命中, 取最高分; 缺素材跳过 |
| `find_all(screen, tpl, threshold=0.85, min_dist=12)` | `[(x, y, score)]` | 多目标(数个数), 近邻去重, 按 x 排序 |
| `find_by_points(screen, rect, points, direction=0)` | `(x, y)` \| None | **多点找色**: 全点命中才返回锚点; `points=[Pt(...)]` |
| `Pt(dx, dy, col, tol=8, alts=(), anti=False)` | — | 多点找色点阵项(对接规范见 `多点找色Pt规范.md`) |
| `wait_find(screenshot_fn, name, timeout=30, interval=0.4, threshold=0.85, stop_event=None)` | `(x, y, score)` \| None | 轮询等模板出现; `name` 可传文件名或已加载图; **停止优先** |
| `changed(a, b, diff=6.0)` | bool | 两屏是否明显不同(判切屏/加载) |
| `is_display_dead(screenshot_fn, timeout=3.0, interval=0.5, diff=6.0)` | bool | 画面卡死判定(大漠 IsDisplayDead) |

区域找色族(大漠 FindColor/GetColorNum/GetAveRGB/CmpColor 对应物, `region` 同为 `(x1,y1,x2,y2)`):

| API | 返回 |
|---|---|
| `find_color(screen, colors, tol=10, region=None)` | `[(x, y)]` —— colors 单色或列表(任一命中) |
| `count_color(screen, colors, tol=10, region=None)` | `int` 命中像素数 |
| `cmp_color(screen, x, y, colors, tol=8)` | `bool` 单点比色 |
| `color_at(screen, x, y)` | `(B, G, R)` |
| `ave_color(screen, region=None)` | `(B, G, R)` 均值 |

### `imgbot/ocr.py` —— OCR(RapidOCR 单例)

| API | 返回 | 说明 |
|---|---|---|
| `find_text(screen, keyword, region=None, min_ratio=0.7)` | `(x, y, ratio)` \| None | 最像 keyword 的文字 |
| `find_texts(screen, keyword, region=None, min_ratio=0.7)` | `[(x, y, ratio)]` | 所有像的(按 y 排序) |
| `find_lines(screen, region=None, fast=False)` | `[(x, y, text)]` | 全部文本行, 不筛选 |
| `raw_texts(screen, region=None, fast=False)` | `[(text, conf)]` | 原始文本+置信度(自己解析数值) |
| `best_match(candidates, keyword, min_ratio=0.7)` | `(x, y, ratio)` \| None | 纯逻辑, 可脱离 OCR 单测 |
| `engine(fast=False)` | RapidOCR | 引擎单例; `fast=True` 只给"大字窄条"(小字会漏读) |

关键词规矩: ≥4 字、去标点; 比例匹配容忍个别错字(7 字错 1 字仍 0.83), 2 字短词上限 0.44 基本无效。

### `imgbot/readout.py` —— 模板逐字读数(游戏内 ~14px 小字)

| API | 说明 |
|---|---|
| `read_digits(scene, templates, threshold=0.8) -> str` | `templates={字符: ndarray 或 [ndarray,...]}`; 放大 4x + 逐字匹配 + NMS, 按 x 拼字符串 |
| `enlarge(scene, factor=4)` | 放大(读数管线内用) |

### `imgbot/ops.py` —— 图色门面(DmSoft 之上的高频组合层, 通道无关)

`Ops(dm)`: 构造吃一个 DmSoft 绑定(normal/gdi/windows/dx 任选)。
规矩: 等待必有 timeout 且超时安静返回; 等待族拒绝进 same_frame 窗口(防复读旧帧);
停止信号先于命中检查; wait_pic/wait_pic_any/wait_text 超时自动截图留痕 logs/shots/。
裸输入不进门面(直接 `dm.MoveTo/dm.LeftClick/KeyPress`)。

| API | 返回 | 说明 |
|---|---|---|
| `wait_until(fn, timeout, interval=0.3, stop=None)` | bool | 通用轮询底座(A/B 族地基), 停止优先 |
| `same_frame()` | 上下文 | 一帧多查窗口: 窗内查询共用冻结帧, 出窗现截 |
| `retry(fn, times=3, backoff=1.0)` | bool | 动作级重试(退避), 管"做到为止" |
| `shot(tag="")` | Path | 当前帧存档 logs/shots/(wait 族失败自动调) |
| `wait_pic(pic, region=None, timeout=30, threshold=None, stop=None)` | `(x,y,score)` \| None | 等图出现 |
| `wait_pic_any(pics, region=None, timeout=30, ...)` | `(名字,x,y,score)` \| None | 等一组里任一张出现(缺素材跳过) |
| `wait_pic_gone(pic, region=None, timeout=30, ...)` | bool | 等图消失(加载页结束) |
| `wait_color(color, region=None, timeout=30, stop=None)` | `(x,y)` \| None | 等色出现("RRGGBB[-DRDGDB]") |
| `wait_color_gone(color, region=None, timeout=30, ...)` | bool | 等色消失 |
| `wait_text(text, region=None, timeout=30, min_ratio=0.7, ...)` | `(x,y,相似度)` \| None | OCR 等文字出现 |
| `wait_stable(region, timeout=30, frames=5, ...)` | bool | 等区域画面静止(加载完成) |
| `wait_change(region, timeout=30, ...)` | bool | 等区域变化(过场/结算) |
| `find_tap(pic, region=None, timeout=0, verify=None, stop=None)` | bool | 找到就拟人点; verify=点后等确认图(二次确认) |
| `tap_until_gone(pic, region=None, timeout=10, ...)` | int(次数) | 连点直到消失(确定/领取) |
| `swipe_find(pic, direction, region=None, timeout=30, ...)` | `(x,y)` \| None | 列表滚动查找(direction=up/down/left/right) |
| `exists(pic, region=None, threshold=None)` | bool | 单帧存在性(冻结帧感知) |
| `probe(*pics, region=None, threshold=None)` | 名字 \| None | 单帧多图探测, 返回命中模板名(多张同屏取分数最高) |
| `find_all(pic, region=None, threshold=None)` | `[(x,y)]` | 找全部命中(列表项/多目标) |
| `check_color(x, y, color, tol=0)` | bool | 单点比色 |
| `check_points(points, region)` | bool | 多点找色校验(动画元素, 全点命中才真) |
| `read_number(region, digit_templates)` | int \| None | 数字读数(模板逐字, 小字体别硬 OCR) |
| `read_text(region=None)` | str | OCR 读区域全文 |

### `imgbot/nav.py` —— 界面导航图(通用机制: 定位两层 + 边表 + BFS 自愈)

`Nav(ops)`: 一个 Ops 门面一张图, 机制零游戏知识, 界面/边/弹窗全由消费项目注册。
任务代码只声明目的地(`go`), 路径/恢复是图的事 —— 崩溃重启后从任意屏调 go 自动
找回位置(挂机长跑的恢复线)。图只管界面间移动, 界面内操作(战斗/领奖)不进图
(判据: 做完需换界面验证的才是边)。锚点资产约定 `nav/屏名.png`, 一屏一锚点。

| API | 返回 | 说明 |
|---|---|---|
| `screen(name, anchor, region=None, threshold=None)` | — | 注册界面(一屏一锚点, 同名重注册=更新; 注册序=where 扫描序) |
| `edge(src, dst, action, timeout=15, retries=1)` | — | 注册边: action 组 ops 原语, 走完自动验证目标屏锚点; 引用未注册界面 ValueError |
| `on_interrupt(pics)` | — | 通用打断表: 任意屏都可能冒的弹窗, 走边前/迷路时自动轰掉 |
| `at(name)` | bool | 期望验证: 我是不是在这屏(单帧一张锚点, 便宜高频) |
| `where()` | 名 \| None | 全局重定位: 一帧(same_frame)扫全部锚点(冷启动/迷路兜底) |
| `go(name, timeout=120, stop=None)` | bool | BFS 带路: 到达 True / 超时安静 False / 迷路抛 `LostError` / 无路径 ValueError |
| `cur` | 名 \| None | 当前假设所在屏(go 成功后即目标屏) |

`LostError`: 迷路且重定位失败(黑屏/掉登录/更新弹窗) —— 结构性失败, 调用方决定
重登或报警; 边验证失败与迷路现场自动截图留痕 logs/shots/。

### `imgbot/actions.py` —— 操作层(拟人点击/轮询点击)

| API | 返回 | 说明 |
|---|---|---|
| `tap(device, x, y, jitter_px=3, delay_ms=(300,800))` | None | **拟人点击**: 随机偏移 + 前置随机延迟 |
| `swipe(device, x1, y1, x2, y2, ms=350, jitter_px=3, delay_ms=(300,800))` | None | 拟人滑动(两端各自偏移) |
| `pause(seconds=(1.0,3.0), stop_event=None)` | None | 随机停顿, stop_event 可打断 |
| `wait_tap(device, name, timeout=30, interval=0.4, threshold=0.85, stop_event=None, log=None)` | `(x, y, score)` \| None | **等一个模板出现即点**; 超时/停止返 None(不抛不存失败截图) |
| `wait_tap_any(device, targets, timeout=30, interval=0.4, until=None, stop_event=None, log=None)` | `set[str]` | **表驱动: 一组模板谁出现点谁**(不讲顺序); `until` 集合集齐即提前收口; 返回点过的模板名 |
| `TapTarget(name, point=None, region=None, threshold=None)` | — | 表项: 模板名 + 固定点击点 + 限矩形 + 阈值 |

`TapTarget` 各字段用法: `point=None`=点命中中心; 固定坐标=认模板但点别处(点命中处会误触) ;
`region` 限矩形=位置固定元素**必须带**; `threshold=None`=用默认 0.85。

### `imgbot/adb.py` —— ADB 封装

| API | 说明 |
|---|---|
| `AdbDevice(serial, adb_path="adb")` | 设备命令全带 `-s <serial>`; `screenshot() -> bytes`(必须 exec-out)、`save_screenshot(path)`、`tap`、`swipe`、`keyevent`、`shell(*args)`、`install(apk)`、`start_app`、`stop_app` |
| `list_devices(adb_path="adb") -> list[str]` | 在线设备序列号 |
| `adb(adb_path, *args) -> str` | 无 `-s` 的全局 adb 命令 |
| `png_size(png_bytes) -> (w, h)` | 从 PNG 头解析(不引 numpy/cv2) |
| `parse_devices(text) -> list[str]` | 解析 `adb devices` 输出 |
| `AdbError` | adb 命令失败/超时抛的异常 |

### `imgbot/ime.py` —— 安卓中文输入

| API | 说明 |
|---|---|
| `ensure(device, log=_noop) -> bool` | 幂等: 缺 ADBKeyboard 就装+切换; 返回 True 才可广播输入 |
| `type_text(device, text)` | 向当前聚焦输入框注入中文(广播) |
| `restore(device)` | 还原原输入法(用完即恢复, 模拟器零残留) |

`adb input text` 只能打 ASCII, 中文**必须**走这里。

### `imgbot/win32/` —— PC 窗口侧

| API | 说明 |
|---|---|
| `window.GameWindow.find(class_name=None, title_keyword=None, parent=None)` | 按类名(优先)/标题关键字找窗口, 找不到 None; 给 parent hwnd 时只搜其子孙窗口(模拟器内层渲染窗口) |
| `window.GameWindow.from_cursor(root=True)` | 鼠标所在窗口; root=False 取光标下直接窗口(大漠式拾取, 模拟器拿到内层渲染窗口) |
| `window.list_windows()` | 所有可见有标题的顶层窗口 `[(hwnd, 类名, 标题)]`, 人工选绑列表用 |
| `window.xor_frame(rect)` | 屏幕坐标矩形(l,t,r,b) XOR 反色描 2px 框(拾取高亮); 同矩形再调一次即擦除 |
| `window.GameWindow` 实例 | `client_rect()`、`window_rect()`(完整窗口矩形, 拾取高亮用)、`is_alive()`、`is_hung()`、`info()`、`pid()`、`process_path()`、`resize_client_to((w,h))`、`post_message(...)`、`is_pseudo_minimized`(属性) |
| `window.GameWindow.park_offscreen(park: bool)` | 伪后台停靠: 挪到屏外只留 2×30px 缝(**完全离屏会触发 DXGI 节流致截图变暗**) |
| `window.is_elevated()` / `window.ensure_dpi_aware()` | UIPI 同级检查 / DPI 感知(非 100% 缩放时坐标会错) |
| `capture.capture_client(window) -> BGR ndarray` | 后台截图(PrintWindow); 真最小化自动 SW_RESTORE 重试 |
| `capture.capture_screen(rect=None) -> BGR ndarray` | 前台屏幕截取(微信截图底层); `rect=(x,y,w,h)` 屏幕坐标(可为负原点), None=整个虚拟屏幕; 遮挡即所得 |
| `capture.virtual_screen_rect() -> (x, y, w, h)` | 多屏虚拟屏幕矩形(原点可为负, 主屏左上恒 0,0) |
| `input.MouseKeyboard(window, inter_action_delay=0.05, rng=None)` | `click`/`double_click`/`move`/`hover`/`type_text`(WM_CHAR 逐码元) —— 拟人贝塞尔轨迹 |
| `input.SendInputMouseKeyboard(window, inter_action_delay=0.05, rng=None)` | 前台键鼠(SendInput 真事件): 同 API 形状 + `key_tap`/`key_down`/`key_up`(扫描码裸键); `type_text` 用 KEYEVENTF UNICODE(IME 免疫); 动真光标, 单开专用 |
| `input.send_paste(window, text)` | 剪贴板 + WM_PASTE(中文一次到位, 比逐字符稳) |
| `input.human_path(x0, y0, x1, y1, rng, max_s=0.12)` | 拟人轨迹计算(纯函数, 可测) |

异常类: `window.UipiError`(目标权限高于本进程, 需管理员同级)、`window.WindowLostError`(窗口被关/句柄过期)、
`capture.CaptureError`(截图失败: 窗口无效/客户区为 0)。

PC 侧坑(实机结论): 目标以管理员运行时本进程必须同级(否则 PostMessage err=5 且截图全黑);
后台键盘 = 带扫描码消息键, 只达从消息循环读键的目标, 轮询型游戏(GetAsyncKeyState/
DirectInput)免疫(换 dx 通道); 真最小化不可截。

### 常见坑速查

- **零方差虚高**: 纯色模板/纯色命中区上相关系数会满分, 库内已判未命中 —— 别自己重写匹配。
- **阈值 0.85 不许降**: 分数在阈值附近跳动说明**方法选错了**, 回 AGENTS.md 决策树换路。
- **位置固定的 UI 必须限矩形**: 全屏搜关闭X曾在世界翅膀上得 0.94 误报。
- **一帧多查**: 同一张图上判 N 个东西要复用同一帧, 别把旧帧拿进循环反复匹配。
- **停止优先**: `stop_event` 置位后 `wait_find`/`wait_tap`/`wait_tap_any` 不再命中也不再点。

## 色键模板约定

制作模板时, 把不要的背景填充成**任意统一颜色**(pixpick 默认 #8080ff),
裁块**≥3/4 个角像素同色即代表该色是背景**; `vision.load` 载入时**自动键控**成
RGBA(alpha=掩码), `find`/`find_all` 对键控模板只比对非底色笔画 — 无需再手动调
`key_out_background`(仍保留, 兼容旧流程)。
制作规矩: ①底色选目标里没有的颜色 ②笔画别顶到角上(污染≤1个角, 多数票容忍)
③目标自身别大面积使用与底色相同的颜色(会被一起抠掉)。

## 与大漠命令的对照(AI 亲和: 按大漠心智模型查 API)

| 大漠 | imgbot |
|---|---|
| FindPic / FindPicEx | `vision.find` / `find_all`(find_any≈多模板任一; region=大漠的x1,y1,x2,y2) |
| FindMultiColor | `vision.find_by_points` + `Pt`(alts≈变体色, direction≈方向) |
| FindColor / FindColorEx | `vision.find_color`(colors 列表=多色任一) |
| CmpColor / GetColor / GetColorNum / GetAveRGB | `cmp_color` / `color_at` / `count_color` / `ave_color` |
| IsDisplayDead | `vision.is_display_dead` |
| Capture / CapturePre | `Target.screenshot` / 消费项目 ops.frame(一帧多查) |
| FindStr | `ocr.find_text` / `find_texts`(字库被 RapidOCR 替代) |
| MoveTo/LeftClick(后台) | `win32.input.MouseKeyboard`(贝塞尔拟人轨迹) |
| SendString / SendPaste | `type_text`(WM_CHAR / ADBKeyboard) / `send_paste` |
| BindWindow(display,mouse,keypad) | `Win32Target(hwnd, capture=…, input=…)` / `AdbTarget(serial)` |
| FindWindow / EnumWindow | `win32.window.GameWindow.find`(类名/标题) |

明确不学: 驱动级键鼠(dx)、防护盾 DmGuard、HackSpeed/DownCpu、字库、Mem 内存图管理
(见 AGENTS.md 红线); EnableFakeActive 的替代是 `park_offscreen` 细缝停靠。
