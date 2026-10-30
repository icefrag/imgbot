# -*- coding: utf-8 -*-
"""imgbot 接口文档生成器: 单一元数据源 → 两种产物。

- docs/接口说明.md            给 AI 看(markdown, 全量签名+参数表+示例)
- docs/chm/                  CHM 工程(大漠五段式页面: 函数简介/原型/参数定义/返回值/示例)
- docs/imgbot-接口说明.html   单文件版(浏览器直接开, 顶部目录导航)
- docs/imgbot-接口说明.chm    编译好的 CHM(便携 hhc 在 D:/tools/hhw)

用法: uv run scripts/build_docs.py

参数表权威 = Python 签名内省(inspect.signature): 必传/可选与默认值从代码实时
提取, 元数据只提供每个参数的类型词与说明 —— 参数与代码永不漂移。
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"

I, S, F, L = "整形数", "字符串", "浮点数", "列表"

BIND_MODES = """
| 通道 | display(图色) | mouse/keypad(键鼠) | 注入 | 适用 |
|---|---|---|---|---|
| normal | BitBlt 截屏裁客户区 | SendInput 真事件(动真光标) | 零 | 单开/强反作弊; IME 免疫打字 |
| gdi/windows | PrintWindow 只读 | PostMessage 消息 | 零 | 后台窗口, 消息型目标 |
| dx(潜影) | GL/d3d9 钩子取帧 | 进程内消息配方+键态钩子 | 注入 | 游戏 6 开后台, 最高档 |

坐标全模式统一**绑定窗口客户区坐标**; 通道序 BGR。
"""

FIRST_SCRIPT = '''\
from imgbot.dmsoft import DmSoft
from imgbot.ops import Ops

dm = DmSoft()
assert dm.BindWindow(hwnd, "dx", "dx", "dx", 0) == 1
dm.SetPath(r"D:/proj/templates")            # 模板目录(PNG 素材)
ops = Ops(dm)                               # 门面: 高频组合

ops.find_tap("领取按钮.png", region=(800, 500, 1200, 700), verify="已领取.png")
hit = ops.wait_pic_any(["升级弹窗.png", "断线弹窗.png"], region=POPUP, timeout=10)
with ops.same_frame():                      # 一帧多查
    if ops.exists("boss血条.png", region=TOP) and ops.check_color(10, 10, "00FF00"):
        dm.KeyPress(0x70)                   # F1 裸键(游戏技能)
dm.UnBindWindow()
'''


def _m(name, proto, brief, params=(), ret="", example=""):
    """方法条目。params: (参数名, 类型词, 说明)—— 必传/默认由签名内省决定。"""
    return dict(name=name, proto=proto, brief=brief, params=list(params),
                ret=ret, example=example)


def _c(name, body="", code=""):
    return dict(kind="concept", name=name, body=body, code=code)


CATEGORIES = [
    # ==================== 入门 ====================
    ("入门", [
        _c("安装与构建", body="""
**Python 侧**(消费项目):
```
uv add imgbot            # 基础(图色/键鼠/窗口)
uv add "imgbot[ocr]"     # 加 OCR
```

**潜影注入件**(仅 dx 通道需要, 其余模式零依赖):
```
uv run imgbot/dx/native/build.py        # 需 zig(默认 D:/tools/zig/zig.exe)
```
产物 `imgbot/dx/bin/`: dmcore32/64.dll + injector32/64.exe。
Defender 需加一次排除(管理员):
`Add-MpPreference -ExclusionPath <repo>\\imgbot\\dx\\bin`
"""),
        _c("第一个脚本", code=FIRST_SCRIPT),
        _c("BindWindow 通道模式", body=BIND_MODES),
    ]),
    # ==================== 绑定与后台设置 ====================
    ("绑定与后台设置", [
        _m("BindWindow", "BindWindow(hwnd, display, mouse, keypad, mode=0)",
           "绑定窗口并选通道, 返回 1 成功/0 失败。重复绑定前先 UnBindWindow。",
           [("hwnd", I, "目标窗口句柄(FindWindow 获取)"),
            ("display", S, "图色通道: normal / gdi / gdi2 / dx 开头的串"),
            ("mouse", S, "鼠标通道: normal / windows / dx 开头的串"),
            ("keypad", S, "键盘通道: 同 mouse"),
            ("mode", I, "保留(大漠兼容位)")],
           f"{I}: 1 成功 / 0 失败(异常带原因)",
           'dm.BindWindow(hwnd, "dx", "dx", "dx", 0)'),
        _m("UnBindWindow", "UnBindWindow()",
           "解绑释放资源(dx 通道收回注入钩子)。脚本退出前调用。", (),
           f"{I}: 1", "dm.UnBindWindow()"),
        _m("ForceUnBindWindow", "ForceUnBindWindow(hwnd)",
           "强制解绑指定窗口(本对象绑定的)。",
           [("hwnd", I, "窗口句柄")], f"{I}: 1/0",
           "dm.ForceUnBindWindow(hwnd)"),
        _m("GetBindWindow", "GetBindWindow()",
           "当前绑定窗口句柄, 未绑定 0。", (), I,
           "hwnd = dm.GetBindWindow()"),
        _m("IsBind", "IsBind(hwnd)", "查询是否绑定了某窗口。",
           [("hwnd", I, "窗口句柄")], f"{I}: 1/0",
           "if dm.IsBind(hwnd): ..."),
        _m("SwitchBindWindow", "SwitchBindWindow(hwnd)",
           "不解绑切换**同进程**另一窗口。",
           [("hwnd", I, "新窗口句柄")], f"{I}: 1/0",
           "dm.SwitchBindWindow(other_hwnd)"),
        _m("EnableFakeActive", "EnableFakeActive(enable)",
           "后台假激活(潜影): 子类化目标吞失焦族消息, 后台窗口自认为有焦点。",
           [("enable", I, "1 开 / 0 关")], f"{I}: 1",
           "dm.EnableFakeActive(1)"),
        _m("GetFps", "GetFps()",
           "绑定窗口帧率(潜影图色: 一秒帧序号增量)。", (), I,
           "print(dm.GetFps())"),
        _m("EnableRealMouse", "EnableRealMouse(enable, mousedelay, mousestep)",
           "鼠标拟人开关 —— 拟人在本库默认常开(贝塞尔轨迹+ease-out), "
           "此开关为兼容而设, 恒返回 1。",
           [("enable", I, "1/0"),
            ("mousedelay", I, "兼容位(忽略)"),
            ("mousestep", I, "兼容位(忽略)")], f"{I}: 1",
           "dm.EnableRealMouse(1, 10, 10)"),
        _m("EnableRealKeypad", "EnableRealKeypad(enable)",
           "键盘拟人开关 —— 同上, 恒返回 1。",
           [("enable", I, "1/0")], f"{I}: 1",
           "dm.EnableRealKeypad(1)"),
        _m("EnableMouseSync", "EnableMouseSync(enable, time_out)",
           "鼠标消息同步开关 —— 潜影通道本就同步送达(SendMessageTimeout), 恒返回 1。",
           [("enable", I, "1/0"), ("time_out", I, "毫秒(兼容位)")], f"{I}: 1",
           "dm.EnableMouseSync(1, 2000)"),
        _m("EnableKeypadSync", "EnableKeypadSync(enable, time_out)",
           "键盘消息同步开关 —— 同上, 恒返回 1。",
           [("enable", I, "1/0"), ("time_out", I, "毫秒(兼容位)")], f"{I}: 1",
           "dm.EnableKeypadSync(1, 2000)"),
    ]),
    # ==================== 图色 ====================
    ("图色", [
        _m("FindPic",
           "FindPic(x1, y1, x2, y2, pic_name, delta_color, sim=0.9, dir=0)",
           "模板匹配找图。pic_name 支持多模板(竖线分隔, 按序取首个命中)。",
           [("x1,y1,x2,y2", I, "搜索区域(客户区坐标, 尽量小别全屏)"),
            ("pic_name", S, "模板文件名(带 .png), 相对 SetPath 目录; "
                            "多模板写 a.png|b.png"),
            ("delta_color", S, "色偏容差 RRGGBB-DRDGDB(保留位)"),
            ("sim", F, "相似度阈值 0~1, 默认 0.9; 禁止降阈值硬凑"),
            ("dir", I, "搜索方向(保留, 现只支持 0)")],
           "元组: (模板序号, x, y); 未找到 (-1, -1, -1)",
           'idx, x, y = dm.FindPic(0, 0, 800, 600, "按钮.png", "000000", 0.9)'),
        _m("FindPicE", "FindPicE(x1, y1, x2, y2, pic_name, delta_color, sim, dir)",
           "同 FindPic, 返回字符串 idx|x|y。",
           [("x1,y1,x2,y2", I, "搜索区域"),
            ("pic_name", S, "模板名(可多模板)"),
            ("delta_color", S, "色偏容差(保留位)"),
            ("sim", F, "相似度阈值"),
            ("dir", I, "搜索方向(保留)")], S,
           'ret = dm.FindPicE(0, 0, 800, 600, "按钮.png", "000000", 0.9)'),
        _m("FindColor", "FindColor(x1, y1, x2, y2, color, sim=1.0, dir=0)",
           "区域找色。",
           [("x1,y1,x2,y2", I, "搜索区域"),
            ("color", S, "RRGGBB 或 RRGGBB-DRDGDB"),
            ("sim", F, "相似度(经容差换算)"),
            ("dir", I, "方向(0 首个/其他取末个)")],
           "元组: (x, y); 未找到 (-1, -1)",
           'x, y = dm.FindColor(0, 0, 500, 500, "FF0000-000010", 1.0)'),
        _m("FindColorE", "FindColorE(x1, y1, x2, y2, color, sim, dir)",
           "同 FindColor, 返回 x|y 字符串。",
           [("x1,y1,x2,y2", I, "搜索区域"),
            ("color", S, "颜色串"),
            ("sim", F, "相似度"),
            ("dir", I, "方向")], S,
           'ret = dm.FindColorE(0, 0, 500, 500, "FF0000", 1.0)'),
        _m("FindColorEx", "FindColorEx(x1, y1, x2, y2, color, sim, dir)",
           "同 FindColor, 返回所有命中 x,y|x,y 字符串。",
           [("x1,y1,x2,y2", I, "搜索区域"),
            ("color", S, "颜色串"),
            ("sim", F, "相似度"),
            ("dir", I, "方向")], S,
           'ret = dm.FindColorEx(0, 0, 500, 500, "FF0000", 1.0)'),
        _m("FindMultiColor",
           "FindMultiColor(x1, y1, x2, y2, first_color, offset_color, sim, dir)",
           "多点找色(动画元素首选)。锚点+点阵全命中才返回。",
           [("x1,y1,x2,y2", I, "搜索矩形(尽量小)"),
            ("first_color", S, "锚点色 RRGGBB"),
            ("offset_color", S, "点阵 x|y|RRGGBB,x|y|RRGGBB,...(pixpick 选点)"),
            ("sim", F, "相似度"),
            ("dir", I, "方向(保留)")],
           "元组: (x, y); 未找到 (-1, -1)",
           'x, y = dm.FindMultiColor(0, 0, 800, 600, "FF0000", '
           '"10|0|FF0000,0|10|00FF00", 1.0, 0)'),
        _m("GetColor", "GetColor(x, y)",
           "取某点颜色, 返回 RRGGBB(大漠同序)。", [("x,y", I, "客户区坐标")], S,
           'col = dm.GetColor(100, 100)   # "1A2B3C"'),
        _m("GetColorBGR", "GetColorBGR(x, y)", "取色返回 BBGGRR。",
           [("x,y", I, "客户区坐标")], S, "col = dm.GetColorBGR(100, 100)"),
        _m("CmpColor", "CmpColor(x, y, color, sim=1.0)",
           "比较坐标颜色。",
           [("x,y", I, "客户区坐标"),
            ("color", S, "RRGGBB-DRDGDB"),
            ("sim", F, "相似度")], f"{I}: 1/0",
           'if dm.CmpColor(100, 100, "FF0000-000010", 1.0): ...'),
        _m("GetColorNum", "GetColorNum(x1, y1, x2, y2, color, sim=1.0)",
           "区域内颜色数量统计。",
           [("x1,y1,x2,y2", I, "统计区域"),
            ("color", S, "颜色串"),
            ("sim", F, "相似度")], I,
           'n = dm.GetColorNum(0, 0, 100, 100, "FFFFFF", 1.0)'),
        _m("Capture", "Capture(x1, y1, x2, y2, file)",
           "区域截图存文件(BMP)。",
           [("x1,y1,x2,y2", I, "截取区域"),
            ("file", S, "文件路径(按扩展名定格式)")], f"{I}: 1",
           'dm.Capture(0, 0, 800, 600, "screen.bmp")'),
        _m("CapturePng", "CapturePng(x1, y1, x2, y2, file)",
           "同 Capture, PNG 格式。",
           [("x1,y1,x2,y2", I, "截取区域"), ("file", S, "文件路径")], f"{I}: 1",
           'dm.CapturePng(0, 0, 800, 600, "screen.png")'),
        _m("CaptureJpg", "CaptureJpg(x1, y1, x2, y2, file, quality=90)",
           "同 Capture, JPG 格式。",
           [("x1,y1,x2,y2", I, "截取区域"),
            ("file", S, "文件路径"),
            ("quality", I, "压缩质量 1-100")], f"{I}: 1",
           'dm.CaptureJpg(0, 0, 800, 600, "screen.jpg", 85)'),
        _m("IsDisplayDead", "IsDisplayDead(x1, y1, x2, y2, t=2)",
           "区域 t 秒画面不变返回 1(卡屏检测)。",
           [("x1,y1,x2,y2", I, "监测区域"),
            ("t", I, "判定秒数")], f"{I}: 1/0",
           "if dm.IsDisplayDead(0, 0, 800, 600, 3): ...  # 卡了3秒"),
    ]),
    # ==================== 键鼠 ====================
    ("键鼠", [
        _m("MoveTo", "MoveTo(x, y)",
           "移动虚拟光标到客户区坐标(拟人贝塞尔轨迹); 越出客户区拒绝返回 0。",
           [("x,y", I, "客户区坐标")], f"{I}: 1/0", "dm.MoveTo(500, 300)"),
        _m("MoveR", "MoveR(rx, ry)", "相对当前虚拟光标移动。",
           [("rx,ry", I, "偏移量")], f"{I}: 1", "dm.MoveR(10, -5)"),
        _m("LeftClick", "LeftClick()",
           "在当前虚拟光标位单击(先 MoveTo 定位)。", (), f"{I}: 1",
           "dm.MoveTo(500, 300); dm.LeftClick()"),
        _m("LeftDoubleClick", "LeftDoubleClick()",
           "双击(第二击带双击语义)。", (), f"{I}: 1",
           "dm.MoveTo(500, 300); dm.LeftDoubleClick()"),
        _m("LeftDown", "LeftDown()", "左键按下(拖拽起始)。",
           (), f"{I}: 1", "dm.LeftDown()"),
        _m("LeftUp", "LeftUp()", "左键抬起(拖拽结束)。",
           (), f"{I}: 1", "dm.LeftUp()"),
        _m("RightClick", "RightClick()", "在当前虚拟光标位右键单击。",
           (), f"{I}: 1", "dm.RightClick()"),
        _m("RightDown", "RightDown()", "右键按下。", (), f"{I}: 1",
           "dm.RightDown()"),
        _m("RightUp", "RightUp()", "右键抬起。", (), f"{I}: 1", "dm.RightUp()"),
        _m("MiddleClick", "MiddleClick()", "在当前虚拟光标位中键单击。",
           (), f"{I}: 1", "dm.MiddleClick()"),
        _m("MiddleDown", "MiddleDown()", "中键按下。", (), f"{I}: 1",
           "dm.MiddleDown()"),
        _m("MiddleUp", "MiddleUp()", "中键抬起。", (), f"{I}: 1", "dm.MiddleUp()"),
        _m("WheelUp", "WheelUp()", "滚轮上滚一格(+120)。",
           (), f"{I}: 1", "dm.WheelUp()"),
        _m("WheelDown", "WheelDown()", "滚轮下滚一格(-120)。",
           (), f"{I}: 1", "dm.WheelDown()"),
        _m("KeyPress", "KeyPress(vk)",
           "按一次键(按下+抬起, 扫描码配方+手动 TranslateMessage)。"
           "前台通道打字走 UNICODE(IME 免疫); L/R 修饰键自动归一化为通用码。",
           [("vk", I, "虚拟键码(0x41=A, 0x70=F1)")], f"{I}: 1",
           "dm.KeyPress(0x1B)  # ESC"),
        _m("KeyDown", "KeyDown(vk)", "键按下(长按/组合起始)。",
           [("vk", I, "VK 码")], f"{I}: 1",
           "dm.KeyDown(0x11)  # Ctrl 按住"),
        _m("KeyUp", "KeyUp(vk)", "键抬起。",
           [("vk", I, "VK 码")], f"{I}: 1", "dm.KeyUp(0x11)  # Ctrl 松开"),
        _m("KeyPressChar", "KeyPressChar(key_str)",
           "按字符键(单字符, US 布局)。", [("key_str", S, "单字符, 如 a")],
           f"{I}: 1", 'dm.KeyPressChar("a")'),
        _m("KeyDownChar", "KeyDownChar(key_str)", "字符键按下。",
           [("key_str", S, "单字符")], f"{I}: 1", 'dm.KeyDownChar("a")'),
        _m("KeyUpChar", "KeyUpChar(key_str)", "字符键抬起。",
           [("key_str", S, "单字符")], f"{I}: 1", 'dm.KeyUpChar("a")'),
        _m("KeyPressStr", "KeyPressStr(key_str, delay=50)",
           "逐字符按键串。",
           [("key_str", S, "字符串"),
            ("delay", I, "每字符间隔毫秒")], f"{I}: 1",
           'dm.KeyPressStr("abc", 50)'),
        _m("SendString", "SendString(hwnd, str)",
           "向窗口打字(中文/代理对自动拆)。潜影=WM_CHAR 注入; 前台=UNICODE 注入。",
           [("hwnd", I, "窗口句柄"), ("str", S, "文本")], f"{I}: 1",
           'dm.SendString(edit_hwnd, "你好")'),
        _m("SendPaste", "SendPaste(hwnd)",
           "剪贴板粘贴注入(输入框支持时中文一次到位)。",
           [("hwnd", I, "窗口句柄")], f"{I}: 1",
           "dm.SendPaste(edit_hwnd)"),
        _m("GetCursorPos", "GetCursorPos()",
           "**虚拟**光标位置(客户区坐标) —— 后台自动化时真人动鼠标不影响读数。",
           (), "元组: (x, y)", "x, y = dm.GetCursorPos()"),
    ]),
    # ==================== 窗口 ====================
    ("窗口", [
        _m("FindWindow", "FindWindow(class_name, title='')",
           "按类名(可含标题)找窗口。",
           [("class_name", S, "窗口类名, 如 Notepad"),
            ("title", S, "标题包含词, 空=不限")], f"{I}: 句柄/0",
           'hwnd = dm.FindWindow("Notepad")'),
        _m("FindWindowEx", "FindWindowEx(parent, class_name, title='')",
           "在父窗口下找子窗口。",
           [("parent", I, "父句柄, 0=桌面"),
            ("class_name", S, "子窗口类名"),
            ("title", S, "标题包含词")], f"{I}: 句柄/0",
           'edit = dm.FindWindowEx(hwnd, "Edit")'),
        _m("GetWindowClass", "GetWindowClass(hwnd)", "取窗口类名。",
           [("hwnd", I, "句柄")], S, 'cls = dm.GetWindowClass(hwnd)'),
        _m("GetWindowTitle", "GetWindowTitle(hwnd)", "取窗口标题。",
           [("hwnd", I, "句柄")], S, 't = dm.GetWindowTitle(hwnd)'),
        _m("GetWindowRect", "GetWindowRect(hwnd)", "完整窗口矩形。",
           [("hwnd", I, "句柄")], "元组: (x1, y1, x2, y2)",
           "x1, y1, x2, y2 = dm.GetWindowRect(hwnd)"),
        _m("GetClientRect", "GetClientRect(hwnd)", "客户区矩形。",
           [("hwnd", I, "句柄")], "元组: (x1, y1, x2, y2)",
           "x1, y1, x2, y2 = dm.GetClientRect(hwnd)"),
        _m("GetClientSize", "GetClientSize(hwnd)", "客户区宽高。",
           [("hwnd", I, "句柄")], "元组: (w, h)",
           "w, h = dm.GetClientSize(hwnd)"),
        _m("ClientToScreen", "ClientToScreen(hwnd, x, y)",
           "客户区坐标 → 屏幕坐标。",
           [("hwnd", I, "句柄"), ("x,y", I, "客户区坐标")], "元组: (x, y)",
           "sx, sy = dm.ClientToScreen(hwnd, 100, 100)"),
        _m("ScreenToClient", "ScreenToClient(hwnd, x, y)",
           "屏幕坐标 → 客户区坐标。",
           [("hwnd", I, "句柄"), ("x,y", I, "屏幕坐标")], "元组: (x, y)",
           "cx, cy = dm.ScreenToClient(hwnd, 500, 400)"),
        _m("MoveWindow", "MoveWindow(hwnd, x, y)",
           "移动窗口(细缝停靠用)。",
           [("hwnd", I, "句柄"), ("x,y", I, "屏幕坐标")], f"{I}: 1",
           "dm.MoveWindow(hwnd, 0, 0)"),
        _m("GetWindowProcessId", "GetWindowProcessId(hwnd)",
           "窗口归属进程 PID。", [("hwnd", I, "句柄")], I,
           "pid = dm.GetWindowProcessId(hwnd)"),
        _m("GetWindowProcessPath", "GetWindowProcessPath(hwnd)",
           "窗口进程可执行完整路径。", [("hwnd", I, "句柄")], S,
           "path = dm.GetWindowProcessPath(hwnd)"),
        _m("GetForegroundWindow", "GetForegroundWindow()",
           "当前前台窗口。", (), I, "fg = dm.GetForegroundWindow()"),
    ]),
    # ==================== Ops 门面 ====================
    ("Ops门面(图色组合层)", [
        _c("Ops 总则", body="""
`ops = Ops(dm)` —— 构造吃一个 DmSoft 绑定, 通道无关。

**机制即规矩**(违反直接抛错):
- 等待必有 timeout, 超时安静返回 None/False, 失败语义调用方交代;
- 等待族拒绝进 same_frame 窗口(防「等 5 秒变复读同一张旧照片」);
- 停止信号(stop=Event 或回调)先于命中检查 —— 已叫停就不会再点击;
- wait_pic / wait_pic_any / wait_text 超时**自动截图**存 logs/shots/。

裸输入不进门面: dm.MoveTo / dm.LeftClick / dm.KeyPress 直接用。
所有带默认值的参数均可覆盖, 不传走默认。
"""),
        _m("wait_until", "ops.wait_until(fn, timeout, interval=0.3, stop=None)",
           "通用轮询底座: fn() 为真返回 True, 超时/停止 False。A/B 族地基。",
           [("fn", "回调", "无参函数, 返回真值即命中"),
            ("timeout", F, "超时秒数"),
            ("interval", F, "轮询间隔秒"),
            ("stop", "对象", "threading.Event 或无参回调, 触发即安静退出")],
           "bool",
           "ops.wait_until(lambda: dm.GetColor(x, y) == 'FF0000', timeout=60, stop=stop_ev)"),
        _m("same_frame", "ops.same_frame()",
           "一帧多查窗口: 窗内所有查询(exists/probe/check_*/find_all/read_*)共用"
           "同一帧 —— 省截图且判断出自同一瞬间; 出窗后查询各自现截。"
           "「点了要看新画面」的查找写在窗外(按键精灵语义)。",
           (), "上下文管理器",
           'with ops.same_frame():\n'
           '    if ops.exists("血条.png") and ops.check_color(1, 1, "FF0000"):\n'
           '        dm.KeyPress(0x70)'),
        _m("retry", "ops.retry(fn, times=3, backoff=1.0)",
           "动作级重试: fn() 返回真即成功; 失败退避 backoff*i 秒重来。管「做到为止」。",
           [("fn", "回调", "整个动作(含点击+验证)"),
            ("times", I, "尝试次数"),
            ("backoff", F, "第 i 次失败后的等待系数(秒=i*backoff)")], "bool",
           'ok = ops.retry(lambda: ops.find_tap("领取.png", region=R), times=3)'),
        _m("shot", "ops.shot(tag='')",
           "当前帧存档到 logs/shots/时间_标签.png(wait 族失败自动调)。",
           [("tag", S, "文件名标签, 空=shot")], "Path",
           'p = ops.shot("军团_领取失败")'),
        _m("wait_pic",
           "ops.wait_pic(pic, region=None, timeout=30, interval=0.4, "
           "threshold=None, stop=None)",
           "等图出现。模板=SetPath 目录下 PNG(四角同色自动键控成透明模板)。",
           [("pic", S, "模板文件名, 如 领取.png"),
            ("region", "元组", "(x1,y1,x2,y2) 固定 UI 必须限矩形"),
            ("timeout", F, "超时秒数, 超时返 None 并自动留痕"),
            ("interval", F, "轮询间隔秒"),
            ("threshold", F, "相似度, None=库默认 0.85"),
            ("stop", "对象", "停止信号")],
           "(x, y, 分数) 或 None",
           'hit = ops.wait_pic("主界面.png", region=(0, 0, 300, 200), timeout=15)'),
        _m("wait_pic_any", "ops.wait_pic_any(pics, region=None, timeout=30, "
           "interval=0.4, threshold=None, stop=None)",
           "等一组模板里任一张出现(弹窗表)。缺素材自动跳过, 可渐进配齐。",
           [("pics", L, "模板名列表"),
            ("region", "元组", "搜索矩形"),
            ("timeout", F, "超时秒数"),
            ("interval", F, "轮询间隔秒"),
            ("threshold", F, "相似度, None=默认"),
            ("stop", "对象", "停止信号")],
           "(名字, x, y, 分数) 或 None",
           'which = ops.wait_pic_any(["升级.png", "断线.png"], region=POPUP)'),
        _m("wait_pic_gone", "ops.wait_pic_gone(pic, region=None, timeout=30, "
           "interval=0.4, threshold=None, stop=None)",
           "等图消失(加载页结束判定)。",
           [("pic", S, "模板名"),
            ("region", "元组", "搜索矩形"),
            ("timeout", F, "超时秒数"),
            ("interval", F, "轮询间隔秒"),
            ("threshold", F, "相似度"),
            ("stop", "对象", "停止信号")], "bool",
           'ok = ops.wait_pic_gone("加载中.png", region=(400, 300, 700, 400), timeout=20)'),
        _m("wait_color", "ops.wait_color(color, region=None, timeout=30, "
           "interval=0.4, stop=None)",
           "等色出现 —— 色比图快一个量级(转圈/倒计时)。",
           [("color", S, "RRGGBB 或 RRGGBB-DRDGDB"),
            ("region", "元组", "搜索矩形"),
            ("timeout", F, "超时秒数"),
            ("interval", F, "轮询间隔秒"),
            ("stop", "对象", "停止信号")], "(x, y) 或 None",
           'xy = ops.wait_color("FF0000", region=(0, 0, 300, 200), timeout=5)'),
        _m("wait_color_gone", "ops.wait_color_gone(color, region=None, "
           "timeout=30, interval=0.4, stop=None)",
           "等色消失。",
           [("color", S, "颜色串"),
            ("region", "元组", "搜索矩形"),
            ("timeout", F, "超时秒数"),
            ("interval", F, "轮询间隔秒"),
            ("stop", "对象", "停止信号")], "bool",
           'ok = ops.wait_color_gone("0000FF", region=(0, 0, 300, 200), timeout=10)'),
        _m("wait_text", "ops.wait_text(text, region=None, timeout=30, "
           "interval=0.5, min_ratio=0.7, stop=None)",
           "OCR 等文字出现。注意关键词要与整行文字足够相似(短词配长行过不了 0.7)。",
           [("text", S, "关键词(尽量整词)"),
            ("region", "元组", "先裁区域再 OCR 更稳"),
            ("timeout", F, "超时秒数"),
            ("interval", F, "轮询间隔秒"),
            ("min_ratio", F, "相似度门槛"),
            ("stop", "对象", "停止信号")],
           "(x, y, 相似度) 或 None(自动留痕)",
           'ops.wait_text("领奖", region=(800, 500, 1200, 700))'),
        _m("wait_stable", "ops.wait_stable(region, timeout=30, interval=0.3, "
           "frames=5, stop=None)",
           "等区域画面静止(连续 frames 帧哈希一致 = 加载完成)。"
           "注意: 逐字节全等, 区域内有轻微动画会永远不稳。",
           [("region", "元组", "监测矩形"),
            ("timeout", F, "超时秒数"),
            ("interval", F, "采样间隔秒(默认 0.3s 采一次)"),
            ("frames", I, "连续一致帧数(默认 5, 最快约 1.2s 判稳)"),
            ("stop", "对象", "停止信号")], "bool",
           'ok = ops.wait_stable((100, 100, 800, 600), timeout=20, frames=3)'),
        _m("wait_change", "ops.wait_change(region, timeout=30, interval=0.3, "
           "stop=None)",
           "等区域发生变化(过场结束/结算弹出), 基线=调用时第一帧。",
           [("region", "元组", "监测矩形"),
            ("timeout", F, "超时秒数"),
            ("interval", F, "采样间隔秒"),
            ("stop", "对象", "停止信号")], "bool",
           'ok = ops.wait_change((0, 0, 800, 600), timeout=15)'),
        _m("find_tap", "ops.find_tap(pic, region=None, timeout=0, interval=0.4, "
           "threshold=None, verify=None, stop=None)",
           "**找到就拟人点**(最高频原语)。timeout=0 单帧试; verify=点后要等出现的"
           "确认图(二次确认, 需新帧故不能在 same_frame 窗口用)。",
           [("pic", S, "模板名"),
            ("region", "元组", "搜索矩形"),
            ("timeout", F, "0=单帧试一次; >0=先等到出现(秒)"),
            ("interval", F, "轮询间隔秒"),
            ("threshold", F, "相似度, None=默认"),
            ("verify", S, "点后确认模板名"),
            ("stop", "对象", "停止信号")],
           "bool(点到且验证过)",
           'ops.find_tap("领取.png", region=R, verify="已领取.png")'),
        _m("tap_until_gone", "ops.tap_until_gone(pic, region=None, timeout=10, "
           "interval=0.5, threshold=None, stop=None)",
           "连点直到图消失(确定/领取类), 返回点击次数; 先等它出现(至多 5s)。",
           [("pic", S, "模板名"),
            ("region", "元组", "搜索矩形"),
            ("timeout", F, "连点阶段超时秒数"),
            ("interval", F, "两次点击间隔秒"),
            ("threshold", F, "相似度"),
            ("stop", "对象", "停止信号")], I,
           'n = ops.tap_until_gone("确定.png", region=DIALOG, timeout=8)'),
        _m("swipe_find", "ops.swipe_find(pic, direction, region=None, timeout=30, "
           "threshold=None, stop=None)",
           "列表滚动查找: 逐屏拖动, 每屏找一次。",
           [("pic", S, "目标模板名"),
            ("direction", S, "up / down / left / right"),
            ("region", "元组", "找图区域(拖动恒在窗口中央)"),
            ("timeout", F, "总超时秒数"),
            ("threshold", F, "相似度"),
            ("stop", "对象", "停止信号")], "(x, y) 或 None",
           'xy = ops.swipe_find("好友头像.png", "up", timeout=20)'),
        _m("exists", "ops.exists(pic, region=None, threshold=None)",
           "单帧存在性(same_frame 窗内用冻结帧)。",
           [("pic", S, "模板名"),
            ("region", "元组", "搜索矩形"),
            ("threshold", F, "相似度, None=默认")], "bool",
           'if ops.exists("红名.png", region=(0, 0, 200, 100)): ...'),
        _m("probe", "ops.probe(*pics, region=None, threshold=None)",
           "单帧多图探测: 返回第一张存在的模板名, 全无 None。",
           [("pics", L, "模板名(可传多个)"),
            ("region", "元组", "搜索矩形"),
            ("threshold", F, "相似度")], "名字 或 None",
           'which = ops.probe("升级.png", "断线.png", region=POPUP)'),
        _m("find_all", "ops.find_all(pic, region=None, threshold=None)",
           "找全部命中中心坐标(列表项/多目标), 近邻去重。",
           [("pic", S, "模板名"),
            ("region", "元组", "搜索矩形"),
            ("threshold", F, "相似度")], "[(x, y)]",
           'pts = ops.find_all("金星.png", region=(100, 200, 400, 260))'),
        _m("check_color", "ops.check_color(x, y, color, tol=0)",
           "单点比色(冻结帧感知)。",
           [("x,y", I, "客户区坐标"),
            ("color", S, "RRGGBB 或 RRGGBB-DRDGDB"),
            ("tol", I, "附加容差")], "bool",
           'if ops.check_color(455, 320, "00FF00"): ...'),
        _m("check_points", "ops.check_points(points, region)",
           "多点找色单帧校验(动画元素): 首点为锚, 全点命中才真。",
           [("points", L, "[(dx,dy,RRGGBB[,tol]), ...] pixpick 选点"),
            ("region", "元组", "搜索矩形")], "bool",
           'ok = ops.check_points([(0, 0, "FF0000"), (10, 0, "00FF00")], R)'),
        _m("read_number", "ops.read_number(region, digit_templates)",
           "数字读数(小字体别硬 OCR): 模板逐字+NMS。",
           [("region", "元组", "数字所在矩形"),
            ("digit_templates", "字典", "{字符: ndarray}, pixpick 制作")],
           "int 或 None",
           "hp = ops.read_number((60, 20, 130, 40), DIGITS)"),
        _m("read_text", "ops.read_text(region=None)",
           "OCR 读区域全文(行间空格连接)。",
           [("region", "元组", "None=全客户区")], S,
           "txt = ops.read_text((0, 0, 400, 80))"),
    ]),
    # ==================== Nav 界面导航图 ====================
    ("Nav导航图(界面带路)", [
        _c("Nav 总则", body="""
`nav = Nav(ops)` —— 构造吃一个 Ops 门面, 机制零游戏知识, 界面/边/弹窗全由
消费项目注册。任务代码只声明目的地(go), 怎么走过去是图的事; 崩溃重启后从
任意屏调 go 自动找回位置(挂机长跑的恢复线)。图只管界面间移动, 界面内操作
(战斗/领奖)不进图 —— 判据: 做完需"换界面验证"的动作才是边。
锚点资产约定 `nav/屏名.png`, 一屏一锚点, 位置固定的界面必须限矩形。
```python
nav = Nav(ops)
nav.screen("city", "nav/city_hud.png", region=(20, 20, 180, 160))
nav.screen("dungeon", "nav/dungeon_title.png")
nav.edge("city", "dungeon",
         lambda: ops.find_tap("nav/dungeon_btn.png", timeout=8))
nav.on_interrupt(["popup/reward_ok.png"])   # 任意屏都可能冒的, 走边前轰掉
nav.go("dungeon")                           # 不管现在在哪, 图带路
```
定位两层缺一不可: at() 期望验证(单帧一张锚点, 高频便宜); where() 全局重定位
(same_frame 一帧扫全部锚点, 冷启动/迷路兜底)。go 循环: 已在即返 → 迷路先
重定位 → BFS 求路 → 逐边[打断钩子→动作→验证落点], 边重试尽 → 重定位自愈
(落在别的屏重算路径), 全锚点扫不到 = LostError。
"""),
        _m("screen", "nav.screen(name, anchor, region=None, threshold=None)",
           "注册界面: 一屏一锚点。同名重注册=更新锚点(不用改边表); 注册序即 where 扫描序, 判别力强的锚点先注册。",
           [("name", S, "界面名(任务代码/go 用它引用)"),
            ("anchor", S, "锚点模板名(nav/屏名.png 约定)"),
            ("region", "元组", "(x1,y1,x2,y2) 位置固定 UI 必须限矩形"),
            ("threshold", F, "相似度, None=库默认 0.85")]),
        _m("edge", "nav.edge(src, dst, action, timeout=15, retries=1)",
           "注册边: action 组 ops 原语, 返回假/抛错=本边失败整边重来; 走完自动验证目标屏锚点。",
           [("src", S, "源界面名"),
            ("dst", S, "目标界面名"),
            ("action", "函数", "无参可调用, 组 ops.find_tap/wait_pic 等"),
            ("timeout", F, "动作成功后等目标屏锚点的验证秒数(加载屏算在内)"),
            ("retries", I, "额外重试次数")]),
        _m("on_interrupt", "nav.on_interrupt(pics)",
           "通用打断表: 任意界面都可能冒的弹窗(奖励/公告/升级), 每次走边前和迷路时自动轰掉。可多次调用追加。",
           [("pics", L, "弹窗模板名列表")]),
        _m("at", "nav.at(name)", "期望验证: 我是不是在这屏(单帧一张锚点, 冻结窗内用冻结帧)。",
           [("name", S, "界面名")], "bool"),
        _m("where", "nav.where()", "全局重定位: 一帧(same_frame 共帧)扫全部锚点, 返回界面名, 全不中 None。",
           (), "界面名 或 None"),
        _m("go", "nav.go(name, timeout=120, stop=None)",
           "走到目标界面。到达 True; 总超时/停止安静 False(失败语义调用方交代); 迷路抛 LostError(黑屏/掉登录, 需重登); 无可达路径/未注册 ValueError(建图错误启动即大声)。边验证失败与迷路现场自动截图留痕。",
           [("name", S, "目标界面名"),
            ("timeout", F, "总超时秒数"),
            ("stop", "对象", "threading.Event 或无参回调, 触发即安静退出")],
           "bool", "ok = nav.go('dungeon', timeout=60)"),
    ]),
    # ==================== 基本设置与模板 ====================
    ("基本设置与模板", [
        _m("SetPath", "SetPath(path)",
           "设置模板/图色资源目录(每个项目入口设一次)。",
           [("path", S, "目录")], f"{I}: 1",
           'dm.SetPath(r"D:/proj/templates")'),
        _m("Ver", "Ver()", "版本串。", (), S, "print(dm.Ver())"),
        _m("GetLastError", "GetLastError()", "最近错误码。", (), I,
           "err = dm.GetLastError()"),
        _c("模板制作约定", body="""
- 一律 **PNG**(无损+透明通道), 文件名即模板名;
- **四角即底色自动键控**: 3 通道 PNG 四角(≥3/4)同色 → 载入自动转透明模板,
  匹配只比笔画 —— 悬浮元素裁图后背景填统一纯色即可, 不用手工抠图;
- 元素自带闪光/呼吸灯 → 只裁稳定核心(文字笔画/图标主体);
- **位置固定的 UI(弹窗角上的 X 等)必须在固定矩形内搜索**(region),
  否则画面其他亮色图案会误报(实测关 X 全屏搜在翅膀上得 0.94);
- 多点找色素材用 pixpick 选点(tools/pixpick)。
"""),
        _c("常见坑速查", body="""
- **UIPI**: 目标以管理员运行时本进程必须同级(PostMessage err=5 / PrintWindow 全黑);
- **真最小化不可截**(PrintWindow); 完全离屏触发 DXGI 遮挡节流(细缝停靠 park_offscreen);
- **模拟器宿主窗口对合成消息免疫** → 模拟器一律走 adb(AdbTarget);
- **中文 IME 吞前台 SendInput 字母键**(vk 改写 0xE5) → 打字走 UNICODE 路径(库内已处理);
- **轮询型游戏**(GetAsyncKeyState)对消息注入免疫 → 潜影键态钩子 hook_keystate();
- **DPI**: 非 100% 缩放先 ensure_dpi_aware()(BindWindow 内已调);
- 全量坑清单见仓库 AGENTS.md。
"""),
        _c("未实现方法留位", body="{{STUBS}}"),
    ]),
]

# 未实现留位清单(方法名: 留位原因) —— 调用即 raise NotImplementedError, 明确报错不装懂
STUBS = {
    "EnableBind": "暂停/恢复后台绑定",
    "LockInput": "锁定物理键鼠输入",
    "LockDisplay": "锁定图色不刷新",
    "HackSpeed": "变速齿轮(IAT hook 时间函数)",
    "SetAero": "关闭 Aero 特效",
    "DownCpu": "降低本进程 CPU 占用",
    "SetInputDm": "多开输入坐标基点",
    "EnableSpeedDx": "高速 dx 键鼠",
    "EnableIme": "IME 开关联动",
    "EnableKeypadMsg": "键盘消息投递开关",
    "EnableMouseMsg": "鼠标消息投递开关",
    "EnableKeypadPatch": "键盘补丁",
    "SetDisplayDelay": "显示延迟",
    "SetDisplayRefreshDelay": "显示刷新延迟",
    "LockMouseRect": "前台鼠标活动矩形",
}


# ==================== 签名内省(必传/默认值的权威来源) ====================
def _func_of(name: str):
    from imgbot.dmsoft import DmSoft
    from imgbot.nav import Nav
    from imgbot.ops import Ops
    for cls in (DmSoft, Ops, Nav):
        f = getattr(cls, name, None)
        if f is not None:
            return f
    return None


def _sig_records(name: str):
    """[(参数名, 是否必传, 默认值显示, 是否变参)] —— 从真实签名提取。"""
    f = _func_of(name)
    if f is None:
        return None
    try:
        sig = inspect.signature(f)
    except (TypeError, ValueError):
        return None
    recs = []
    for p in sig.parameters.values():
        if p.name == "self" or p.name.startswith("_"):
            continue          # _bpp/_ 这类兼容位不进"权威"参数表
        if p.kind in (inspect.Parameter.VAR_POSITIONAL,
                      inspect.Parameter.VAR_KEYWORD):
            recs.append((p.name, True, "—", True))
            continue
        req = p.default is inspect.Parameter.empty
        d = "—" if req else repr(p.default).strip("'")
        recs.append((p.name, req, d, False))
    return recs


def _expand_aliases():
    """把 "A / B" 合写条目拆成一方法一页(大漠格式), 共享简介/参数/示例。"""
    out = []
    for cat, items in CATEGORIES:
        ni = []
        for it in items:
            if it.get("kind") != "concept" and " / " in it["name"]:
                args = it["proto"].split("(", 1)[1]
                for pname in (x.strip() for x in it["name"].split(" / ")):
                    ni.append({**it, "name": pname,
                               "proto": f"{pname}({args})"})
            else:
                ni.append(it)
        out.append((cat, ni))
    return out


CATEGORIES = _expand_aliases()


def _stubs_md() -> str:
    lines = ["| 方法 | 参数 | 留位原因 |", "|---|---|---|"]
    for name, reason in STUBS.items():
        recs = _sig_records(name) or []
        ps = ", ".join(n for n, _r, _d, _v in recs)
        lines.append(f"| {name} | {ps} | {reason} |")
    lines.append("")
    lines.append("调用即 raise NotImplementedError(带说明) —— 明确报错优先, 不装懂。")
    return "\n".join(lines)


# ============================= MD 生成 =============================
def gen_md() -> str:
    out = ["# imgbot 接口说明",
           "",
           "> 图色脚本基础库(潜影自研后台插件 + DmSoft 接口壳 + Ops 门面)。",
           "> 坐标一律绑定窗口客户区; 通道序 BGR; 本文档由 scripts/build_docs.py 生成。",
           "> 参数的必传/默认值取自 Python 签名内省, 与代码同源。",
           ""]
    for cat, items in CATEGORIES:
        out += [f"## {cat}", ""]
        for it in items:
            out.append(f"### {it['name']}")
            out.append("")
            if it.get("kind") == "concept":
                body = it.get("body", "").replace("{{STUBS}}", _stubs_md())
                out.append(body.strip())
                if it.get("code"):
                    out.append("```python\n" + it["code"] + "\n```")
                out.append("")
                continue
            out.append(f"**简介**: {it['brief']}")
            out.append("")
            out.append("```python\n" + it['proto'] + "\n```")
            recs = _sig_records(it["name"])
            meta = {}
            for names, t, d in it["params"]:
                for n in names.replace(" ", "").split(","):
                    meta[n] = (t, d)
            if recs:
                out += ["**参数**:", "",
                        "| 参数 | 必传 | 类型 | 默认值 | 说明 |",
                        "|---|---|---|---|---|"]
                for n, req, d, _v in recs:
                    t, dd = meta.get(n, ("", ""))
                    out.append(f"| {n} | {'**必传**' if req else '可选'} "
                               f"| {t} | {d} | {dd} |")
                out.append("")
            elif it["params"]:
                out += ["**参数**:", "", "| 参数 | 类型 | 说明 |", "|---|---|---|"]
                for n, t, d in it["params"]:
                    out.append(f"| {n} | {t} | {d} |")
                out.append("")
            out.append(f"**返回**: {it['ret']}")
            if it["example"]:
                out.append("")
                out.append("```python\n" + it["example"] + "\n```")
            out.append("")
    return "\n".join(out)


# ============================= HTML 生成(大漠五段式) =============================
_CSS = (
    "body{font-family:'Microsoft YaHei',SimSun,sans-serif;margin:24px auto;"
    "max-width:860px;color:#222;line-height:1.65;padding:0 16px}"
    "h1{border-bottom:2px solid #2b5797;color:#2b5797;padding-bottom:6px}"
    "h2{border-left:5px solid #2b5797;padding-left:8px;margin-top:2em}"
    "h3{color:#1a3d6d;margin-bottom:2px}"
    ".sec{color:blue;font-weight:bold;margin:14px 0 4px}"
    "pre{background:#f5f5f5;border:1px solid #ddd;border-radius:4px;padding:10px;"
    "overflow:auto}"
    "code{background:#f0f0f0;padding:1px 4px;border-radius:3px}"
    "table{border-collapse:collapse;width:100%;margin:8px 0}"
    "td,th{border:1px solid #ccc;padding:6px 8px;text-align:left}"
    "th{background:#eef}"
    ".maroon{color:maroon}"
    ".req{color:#c00;font-weight:bold}"
    ".opt{color:#060}"
    ".nav{background:#f8f8f8;border:1px solid #ddd;padding:10px;border-radius:6px}"
    ".nav a{margin-right:12px;text-decoration:none}"
)


def _head(title: str) -> str:
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>{title}</title><style>" + _CSS + "</style></head><body>")


def _md_inline(s: str) -> str:
    """行内 markdown 子集: **粗体** 与 `代码`(先转义 HTML 字符)。"""
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    while "**" in s:
        s = s.replace("**", "<b>", 1).replace("**", "</b>", 1)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", s)


def _md_text(text: str) -> str:
    """块级 markdown 子集: 表格 / 无序列表 / 普通段落(围栏由调用方剥离)。"""
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r"[-: ]+", c) for c in cells):
                    rows.append(cells)          # 跳过分隔行 |---|
                i += 1
            out.append("<table>")
            for ri, cells in enumerate(rows):
                tag = "th" if ri == 0 else "td"
                out.append("<tr>" + "".join(
                    f"<{tag}>{_md_inline(c)}</{tag}>" for c in cells) + "</tr>")
            out.append("</table>")
            continue
        if ln.startswith("- "):
            out.append("<ul>")
            while i < len(lines) and lines[i].startswith("- "):
                out.append(f"<li>{_md_inline(lines[i][2:])}</li>")
                i += 1
            out.append("</ul>")
            continue
        out.append(f"<p>{_md_inline(ln)}</p>" if ln.strip() else "")
        i += 1
    return "\n".join(out)


def _body_html(it) -> str:
    if it.get("kind") == "concept":
        parts = it.get("body", "").replace("{{STUBS}}", _stubs_md()).split("```")
        body = "".join(
            _md_text(p) if i % 2 == 0
            else "<pre>" + p.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;") + "</pre>"
            for i, p in enumerate(parts))
        b = [body]
        if it.get("code"):
            b.append(f"<pre>{it['code']}</pre>")
        return "\n".join(b)
    b = [f"<p class='sec'>函数简介:</p>", f"<p>{_md_inline(it['brief'])}</p>",
         f"<p class='sec'>函数原型:</p>", f"<pre>{it['proto']}</pre>"]
    recs = _sig_records(it["name"])
    meta = {}
    for names, t, d in it["params"]:
        for n in names.replace(" ", "").split(","):
            meta[n] = (t, d)
    if recs:
        lines = []
        for n, req, d, _v in recs:
            t, dd = meta.get(n, ("", ""))
            tag = ("<b class='req'>【必传】</b>" if req
                   else f"<b class='opt'>【可选, 默认 {d}】</b>")
            lines.append(f"{_md_inline(n)} <span class='maroon'>{t}</span> {tag}: "
                         f"{_md_inline(dd)}")
        b += ["<p class='sec'>参数定义:</p>", "<p>" + "<br>".join(lines) + "</p>"]
    elif it["params"]:
        b += ["<p class='sec'>参数定义:</p>",
              "<p>" + "<br>".join(f"{_md_inline(n)} <span class='maroon'>{t}</span>: "
                                  f"{_md_inline(d)}"
                                  for n, t, d in it["params"]) + "</p>"]
    b += ["<p class='sec'>返回值:</p>",
          f"<p><span class='maroon'>{_md_inline(it['ret'])}</span></p>"]
    if it["example"]:
        b += ["<p class='sec'>示例:</p>", f"<pre>{it['example']}</pre>"]
    return "\n".join(b)


def page_html(it) -> str:
    b = [_head(it["name"]), f"<h1>{it['name']}</h1>", _body_html(it),
         "<hr><a href='../index.html'>← 目录</a>", "</body></html>"]
    return "\n".join(b)


def _fname(it):
    """落盘文件名: Windows 禁 / 空格等, 显示名保留原样。"""
    return "".join(c if c not in r'/:*?"<>| ' else "_" for c in it["name"])


def _href(cat, it):
    from urllib.parse import quote
    return quote(f"{cat}/{_fname(it)}.html")


def _local(cat, it):
    """hhc 的 Local: 纯 ASCII 内部路径(显示名 Name 才用中文)。"""
    return it["_id"] + ".html"


def _assign_ids():
    """CHM 内部路径全 ASCII(c0/p0-1.html): hhc 是 ANSI 程序, 中文路径会造成
    内部 TOC 与文件名对不上 → 导航树空面板塌缩(实测坑)。中文只留在显示名。"""
    for ci, (_cat, items) in enumerate(CATEGORIES):
        for ii, it in enumerate(items):
            it["_id"] = f"c{ci}/p{ci}-{ii}"


_assign_ids()


def gen_index_html() -> str:
    b = [_head("imgbot 接口说明"),
         "<h1>imgbot 接口说明</h1>",
         "<p>图色脚本基础库: 潜影(自研后台插件) + DmSoft 接口壳 + Ops 门面。"
         "坐标=绑定窗口客户区, 通道序 BGR。</p>"]
    for cat, items in CATEGORIES:
        b.append(f"<h2>{cat}</h2><p>")
        b.extend(f"<a href='{it['_id']}.html'>{it['name']}</a> " for it in items)
        b.append("</p>")
    b.append("</body></html>")
    return "\n".join(b)


def gen_hhc() -> str:
    # hh 的 TOC 解析器不是完整 HTML 引擎: OBJECT 收尾只能一个 '>'
    # (PARAM 已带 '>', 再拼 '">' 变 '>>' = 条目全灭, 实测坑), GBK 编码=大漠同款
    Q = '"'                                    # 属性引号
    LI = "\t\t<LI> <OBJECT type=\"text/sitemap\">"
    PARAM = "<param name={q}{k}{q} value={q}{v}{q}>"
    entries = []
    for cat, items in CATEGORIES:
        kids = "".join(
            LI + "\n\t\t"
            + PARAM.format(q=Q, k="Name", v=it["name"]) + "\n\t\t"
            + PARAM.format(q=Q, k="Local", v=_local(cat, it)) + "\n\t\t</OBJECT>\n"
            for it in items)
        entries.append(
            "\t<LI> <OBJECT type=\"text/sitemap\">\n\t\t"
            + PARAM.format(q=Q, k="Name", v=cat) + "\n\t</OBJECT>\n"
            + "\t<UL>\n" + kids + "\t</UL>\n")
    head = ('<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML//EN">\n'
            '<HTML><HEAD></HEAD><BODY>\n<UL>\n')
    return head + "".join(entries) + "</UL>\n</BODY></HTML>\n"


def build():
    chm_dir = DOCS / "chm"
    if chm_dir.exists():
        import shutil
        shutil.rmtree(chm_dir)          # 清旧产物(历史中文目录名残留)
    chm_dir.mkdir(parents=True, exist_ok=True)
    (DOCS / "接口说明.md").write_text(gen_md(), encoding="utf-8")
    for _cat, items in CATEGORIES:
        (chm_dir / items[0]["_id"].split("/")[0]).mkdir(exist_ok=True)
        for it in items:
            (chm_dir / f"{it['_id']}.html").write_text(
                page_html(it), encoding="utf-8")
    (chm_dir / "index.html").write_text(gen_index_html(), encoding="utf-8")
    (chm_dir / "imgbot.hhc").write_text(gen_hhc(), encoding="gbk")
    # hhc 只认 ANSI: hhp 全 ASCII(中文经 GBK 的 .hhc 显示名进), 编译目标 ASCII 名
    # [WINDOWS] 字段序(chmspec): 类型,"标题","toc","索引","默认页","主页",jump×4,
    # 导航样式(0x20=三面板),导航宽,按钮掩码,[l,t,r,b]位置,样式,扩展样式,显示态,
    # 面板开合(0=开!),默认页签,页签位,notify id —— 错一格=面板创建失败(实测坑)
    (chm_dir / "imgbot.hhp").write_text(
        "[OPTIONS]\nCompatibility=1.1 or later\nCompiled file=imgbot_doc.chm\n"
        "Contents file=imgbot.hhc\nDefault topic=index.html\nDefault Window=main\n"
        "Language=0x804\nTitle=imgbot API\n\n"
        "[WINDOWS]\n"
        'main="imgbot API","imgbot.hhc",,"index.html",,,,,,0x20,200,0x1006,'
        "[50,30,1300,820],0,0,1,0,0,0,0\n"
        "\n[FILES]\nindex.html\n", encoding="ascii")
    # 编译(便携 hhc 在 D:/tools/hhw; 无则留工程手动编)
    hhc = Path("D:/tools/hhw/hhc.exe")
    if hhc.exists():
        import shutil
        import subprocess
        subprocess.run([str(hhc), "imgbot.hhp"], cwd=chm_dir, capture_output=True)
        out = (chm_dir / "imgbot_doc.chm").resolve()
        if out.exists():
            dst = DOCS / "imgbot-接口说明.chm"
            shutil.move(str(out), dst)
            print(f"  chm : {dst}")
    # 单文件合并版(浏览器直接开)
    single = [_head("imgbot 接口说明"),
              "<h1>imgbot 接口说明</h1><div class='nav'>"]
    single.extend(f"<a href='#{cat}'>{cat}</a>" for cat, _ in CATEGORIES)
    single.append("</div>")
    for cat, items in CATEGORIES:
        single.append(f"<h2 id='{cat}'>{cat}</h2>")
        for it in items:
            single.append(f"<a id='{_href(cat, it)}'></a>")
            single.append(f"<h3>{it['name']}</h3>")
            single.append(_body_html(it))
    single.append("</body></html>")
    (DOCS / "imgbot-接口说明.html").write_text("\n".join(single), encoding="utf-8")
    print("生成完毕:")
    print(f"  md  : {DOCS / '接口说明.md'}")
    print(f"  html: {DOCS / 'imgbot-接口说明.html'}")
    print(f"  chm 工程: {DOCS / 'chm'}")


if __name__ == "__main__":
    build()
