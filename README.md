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

模块速查: `vision`(find/find_all/find_any/find_by_points/Pt/wait_find/changed/
is_display_dead/find_color/count_color/cmp_color/color_at/ave_color/色键) ·
`ocr`(find_text/find_texts/find_lines/raw_texts/engine) · `readout`(enlarge/read_digits) ·
`adb`(AdbDevice/png_size/list_devices) · `actions`(拟人 tap/swipe/pause, wait_tap=轮询出现即点) ·
`ime`(ensure/type_text/restore) · `win32`(GameWindow/capture_client/MouseKeyboard/send_paste)。

**方法决策树/验收门槛见 AGENTS.md(动手前必读); 多点找色选点对接见 多点找色Pt规范.md。**

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
| FindPic / FindPicEx | `vision.find` / `find_all`(find_any≈多模板任一) |
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
