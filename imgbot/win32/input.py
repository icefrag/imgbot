# -*- coding: utf-8 -*-
"""后台键鼠(LZG_PMH 移植) — PostMessage 通道.

- 鼠标: WM_MOUSEMOVE / WM_LBUTTONDOWN(MK_LBUTTON) / WM_LBUTTONUP, 坐标进 lParam;
  点击前走贝塞尔拟人轨迹(先快后慢 + 随机弯度), 不瞬移;
- 中文: WM_CHAR 逐码元(UTF-16, 代理对自动拆分) 或 send_paste 剪贴板粘贴;
- 后台键盘(ESC 等)实测无效 → 关闭/取消类操作一律用点击, 不提供 key_tap;
- 前台键鼠 = SendInputMouseKeyboard(SendInput 真事件, DmSoft "normal" 通道)。
"""
import ctypes
import math
import random
import time
from ctypes import wintypes as wt

from .window import WindowLostError

WM_MOUSEMOVE = 0x200
WM_LBUTTONDOWN = 0x201
WM_LBUTTONUP = 0x202
WM_LBUTTONDBLCLK = 0x203
WM_CHAR = 0x102
WM_PASTE = 0x302

MK_LBUTTON = 0x0001

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

# 句柄原型必须声明: 默认 restype 是 32 位 int, ASLR/堆地址超界会截断成野指针(实测崩)
_u32 = ctypes.windll.user32
_k32 = ctypes.windll.kernel32
_k32.GlobalAlloc.restype = ctypes.c_void_p
_k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
_k32.GlobalLock.restype = ctypes.c_void_p
_k32.GlobalLock.argtypes = [ctypes.c_void_p]
_k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_k32.GlobalFree.restype = ctypes.c_void_p
_k32.GlobalFree.argtypes = [ctypes.c_void_p]
_u32.SetClipboardData.restype = ctypes.c_void_p
_u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
_u32.GetClipboardData.restype = ctypes.c_void_p
_u32.GetClipboardData.argtypes = [ctypes.c_uint]


def _lp(x, y):
    """客户区坐标 -> lParam 打包: 高 16 位 y, 低 16 位 x。"""
    return (y & 0xFFFF) << 16 | (x & 0xFFFF)


def human_path(x0, y0, x1, y1, rng, max_s=0.12):
    """拟人移动轨迹: 二次贝塞尔(随机弯度) + 缓动(先快后慢)。

    返回 [(x, y, sleep_s), ...], 不含起点, 终点精确落在目标上;
    距离过近(<4px)退化为单点。总时长 0.1~0.25s 随距离伸缩。
    """
    dist = math.hypot(x1 - x0, y1 - y0)
    if dist < 4:
        return [(x1, y1, 0.0)]
    steps = max(6, min(24, int(dist / 12)))
    bend = rng.uniform(-0.25, 0.25) * dist          # 控制点沿法线随机偏移
    nx, ny = -(y1 - y0) / dist, (x1 - x0) / dist
    cx, cy = (x0 + x1) / 2 + nx * bend, (y0 + y1) / 2 + ny * bend
    total = min(max_s, 0.06 + dist / 2000)
    weights = [rng.uniform(0.5, 1.5) for _ in range(steps)]
    wsum = sum(weights)
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        e = t * (2 - t)                              # ease-out
        u = 1 - e
        out.append((round(u * u * x0 + 2 * u * e * cx + e * e * x1),
                    round(u * u * y0 + 2 * u * e * cy + e * e * y1),
                    total * weights[i - 1] / wsum))
    return out


class MouseKeyboard:
    def __init__(self, window, inter_action_delay=0.05, rng=None):
        self.window = window
        self.delay = inter_action_delay
        self.rng = rng or random.Random()
        self._pos = (0, 0)      # 上次光标位置, 平滑移动的插值起点

    def _ensure_alive(self):
        if not self.window.is_alive():
            raise WindowLostError("目标窗口已失效(被关闭?), 需重新绑定")

    def _post_move(self, x, y):
        self.window.post_message(WM_MOUSEMOVE, 0, _lp(x, y))
        self._pos = (x, y)

    def move(self, x, y, steps=0, interval=0.018):
        """移动光标。steps>0 时平滑插值(实测悬停可见变化的方式)。"""
        self._ensure_alive()
        if steps <= 0:
            self._post_move(x, y)
            return
        x0, y0 = self._pos
        for i in range(1, steps + 1):
            t = i / steps
            self._post_move(round(x0 + (x - x0) * t), round(y0 + (y - y0) * t))
            time.sleep(interval)

    def hover(self, x, y, duration=1.5, jitter=1):
        """拟人移动到位并停留, 期间小抖动触发悬停重算。"""
        self._glide(x, y)
        end = time.time() + duration
        j = 0
        while time.time() < end:
            dx, dy = (jitter, 0) if j % 2 == 0 else (0, jitter)
            self.window.post_message(WM_MOUSEMOVE, 0, _lp(x + dx, y + dy))
            j += 1
            time.sleep(0.2)
        self._post_move(x, y)

    def _glide(self, x, y):
        """沿拟人轨迹发 WM_MOUSEMOVE 直到目标(点击/悬停共用的接近方式)。"""
        self._ensure_alive()
        for px, py, dt in human_path(self._pos[0], self._pos[1], x, y, self.rng):
            self._post_move(px, py)
            if dt:
                time.sleep(dt)

    def click(self, x, y):
        """后台点击: 拟人轨迹接近后按下/抬起(实测生效的顺序与延时)。"""
        self._ensure_alive()
        self._glide(x, y)
        time.sleep(self.delay)
        self.window.post_message(WM_LBUTTONDOWN, MK_LBUTTON, _lp(x, y))
        time.sleep(self.delay)
        self.window.post_message(WM_LBUTTONUP, 0, _lp(x, y))
        self._pos = (x, y)

    def double_click(self, x, y, gap=0.09):
        """后台双击: 单击后紧跟 WM_LBUTTONDBLCLK(第二击用双击语义)。"""
        self.click(x, y)
        time.sleep(gap)                      # 须落在系统双击间隔内(默认 500ms)
        self.window.post_message(WM_LBUTTONDBLCLK, MK_LBUTTON, _lp(x, y))
        time.sleep(self.delay)
        self.window.post_message(WM_LBUTTONUP, 0, _lp(x, y))
        self._pos = (x, y)

    def type_text(self, text, per_char_delay=0.03):
        """逐码元发送 WM_CHAR(UTF-16 码元, 代理对自动拆分)。"""
        self._ensure_alive()
        data = text.encode("utf-16-le")
        for i in range(0, len(data), 2):
            self.window.post_message(WM_CHAR, int.from_bytes(data[i:i + 2], "little"), 1)
            time.sleep(per_char_delay)


def send_paste(window, text):
    """文本经剪贴板 + WM_PASTE 注入(大漠 SendPaste 对应物).

    输入框支持粘贴时比逐字符快且稳(免逐字符焦点节奏坑, 中文一次到位);
    用后剪贴板内容被替换 —— 调用方自己决定要不要保存/还原。
    """
    data = text.encode("utf-16-le") + b"\x00\x00"
    if not _u32.OpenClipboard(None):
        raise OSError("OpenClipboard 失败(剪贴板被占用?)")
    try:
        _u32.EmptyClipboard()
        h = _k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            raise OSError("GlobalAlloc 失败")
        p = _k32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        _k32.GlobalUnlock(h)
        if not _u32.SetClipboardData(CF_UNICODETEXT, h):
            _k32.GlobalFree(h)                # 没交给剪贴板才需要自己释放
            raise OSError("SetClipboardData 失败")
    finally:
        _u32.CloseClipboard()
    window.post_message(WM_PASTE, 0, 0)


# ================= 前台键鼠(SendInput 真实事件) =================
# DmSoft mouse/keypad="normal" 通道: 动真光标/真键盘, 目标必须真前台(动作前
# 自动激活)。占用真实键鼠 —— 跑动时人不能碰机器, 单开专用; 与后台通道
# (PostMessage/潜影)互为补充, API 形状对齐 MouseKeyboard/DxMouseKeyboard。

_INPUT_MOUSE, _INPUT_KEYBOARD = 0, 1
_MF_MOVE, _MF_ABSOLUTE = 0x0001, 0x8000
_MF_VIRTUALDESKTOP = 0x4000
_MF_LEFTDOWN, _MF_LEFTUP = 0x0002, 0x0004
_MF_RIGHTDOWN, _MF_RIGHTUP = 0x0008, 0x0010
_MF_MIDDLEDOWN, _MF_MIDDLEUP = 0x0020, 0x0040
_MF_WHEEL = 0x0800
_KEF_KEYUP, _KEF_SCANCODE, _KEF_EXTENDEDKEY = 0x0002, 0x0008, 0x0001
_KEF_UNICODE = 0x0004                          # VK_PACKET 直接注字符(IME 免疫)
_WHEEL_DELTA = 120
_EXTENDED_VKS = frozenset((
    0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22,          # Ins Del Home End PgUp PgDn
    0x25, 0x26, 0x27, 0x28, 0x90,                # 方向键 NumLock
    0x6F, 0x2C,                                  # Numpad/ PrtSc(RCtrl/RAlt 已被通用映射覆盖)
))
_GENERIC_VK = {0xA0: 0x10, 0xA1: 0x10, 0xA2: 0x11, 0xA3: 0x11,   # L/R→通用修饰
               0xA4: 0x12, 0xA5: 0x12}


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("union", _INPUTUNION)]


def _send_input(inp: _INPUT):
    if _u32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)) != 1:
        raise OSError("SendInput 失败(UIPI? 目标以管理员运行则本进程需同级)")


class SendInputMouseKeyboard:
    """前台键鼠: SendInput 真实事件 + 拟人贝塞尔轨迹(复用 human_path)。

    坐标一律绑定窗口客户区坐标(与其他通道一致); 内部 ClientToScreen 换算后
    发绝对移动(虚拟桌面归一化, 多显示器正确)。
    """

    def __init__(self, window, inter_action_delay=0.05, rng=None):
        self.window = window
        self.delay = inter_action_delay
        self.rng = rng or random.Random()
        self._pos = (0, 0)      # 虚拟光标(客户区), 轨迹插值起点

    def _alive(self):
        if not self.window.is_alive():
            raise WindowLostError("目标窗口已失效(被关闭?), 需重新绑定")

    def _ensure_fg(self):
        """目标必须真前台(SendInput 打的是全局输入流)。
        对比/激活一律用 GA_ROOT 根窗: 绑定目标是子窗口(如 Edit)时,
        GetForegroundWindow 返回的是主窗, 拿子窗比永远"不相等"(实测坑)。
        先直呼 SetForegroundWindow(调用方是前台进程时即可成); 被前台锁定
        拒绝时 AttachThreadInput 挂目标输入队列再激活。不用 ALT 戳法 ——
        ALT 抬起会激活目标菜单栏, 后续按键全进菜单(实测坑)。"""
        root = _u32.GetAncestor(self.window.hwnd, 2) or self.window.hwnd   # GA_ROOT
        if _u32.GetForegroundWindow() == root:
            return
        _u32.SetForegroundWindow(root)
        if _u32.GetForegroundWindow() == root:
            time.sleep(0.1)
            return
        ours = _k32.GetCurrentThreadId()
        tgt = _u32.GetWindowThreadProcessId(root, None)
        if _u32.AttachThreadInput(ours, tgt, True):
            _u32.SetForegroundWindow(root)
            _u32.AttachThreadInput(ours, tgt, False)
        if _u32.GetForegroundWindow() == root:
            time.sleep(0.15)
            return
        # 终极兜底: SwitchToThisWindow(无 ALT 菜单副作用; 前台锁最顽固时可用)
        _u32.SwitchToThisWindow(root, True)
        time.sleep(0.15)

    def _to_screen(self, x, y):
        rx, ry, _, _ = self.window.client_rect()
        return rx + x, ry + y

    def _move_abs(self, x, y):
        """客户区坐标 → 虚拟桌面归一化绝对移动(0..65535)。"""
        sx, sy = self._to_screen(x, y)
        vx, vy = _u32.GetSystemMetrics(76), _u32.GetSystemMetrics(77)
        vw = max(_u32.GetSystemMetrics(78) - 1, 1)
        vh = max(_u32.GetSystemMetrics(79) - 1, 1)
        inp = _INPUT(type=_INPUT_MOUSE)
        inp.union.mi = _MOUSEINPUT(
            (sx - vx) * 65535 // vw, (sy - vy) * 65535 // vh, 0,
            _MF_MOVE | _MF_ABSOLUTE | _MF_VIRTUALDESKTOP, 0, 0)
        _send_input(inp)
        self._pos = (x, y)

    def _glide(self, x, y):
        self._alive()
        self._ensure_fg()
        for px, py, dt in human_path(self._pos[0], self._pos[1], x, y, self.rng):
            self._move_abs(px, py)
            if dt:
                time.sleep(dt)

    def move(self, x, y, steps=0, interval=0.018):
        self._glide(x, y)

    def click(self, x, y):
        self._glide(x, y)
        time.sleep(self.delay)
        self._button(_MF_LEFTDOWN, _MF_LEFTUP)

    def right_click(self, x, y):
        self._glide(x, y)
        time.sleep(self.delay)
        self._button(_MF_RIGHTDOWN, _MF_RIGHTUP)

    def double_click(self, x, y, gap=0.09):
        self.click(x, y)
        time.sleep(gap)
        self._button(_MF_LEFTDOWN, _MF_LEFTUP)      # 系统按双击间隔自己合成 DBLCLK

    def middle_click(self, x=None, y=None):
        px, py = (x, y) if x is not None else self._pos
        self._glide(px, py)
        time.sleep(self.delay)
        self._button(_MF_MIDDLEDOWN, _MF_MIDDLEUP)

    def drag(self, x1, y1, x2, y2, steps=12, interval=0.03):
        self._alive()
        self._ensure_fg()
        self._move_abs(x1, y1)
        time.sleep(self.delay)
        self._button(_MF_LEFTDOWN, 0)
        for i in range(1, steps + 1):
            t = i / steps
            self._move_abs(round(x1 + (x2 - x1) * t), round(y1 + (y2 - y1) * t))
            time.sleep(interval)
        time.sleep(self.delay)
        self._button(0, _MF_LEFTUP)

    def wheel(self, up=True):
        inp = _INPUT(type=_INPUT_MOUSE)
        inp.union.mi = _MOUSEINPUT(
            0, 0, _WHEEL_DELTA if up else -_WHEEL_DELTA, _MF_WHEEL, 0, 0)
        _send_input(inp)

    # -- 按钮/按键的裸事件(DmSoft 的 Down/Up 族走这里) --
    def _button(self, down_flag, up_flag):
        if down_flag:
            inp = _INPUT(type=_INPUT_MOUSE)
            inp.union.mi = _MOUSEINPUT(0, 0, 0, down_flag, 0, 0)
            _send_input(inp)
            time.sleep(self.delay)
        if up_flag:
            inp = _INPUT(type=_INPUT_MOUSE)
            inp.union.mi = _MOUSEINPUT(0, 0, 0, up_flag, 0, 0)
            _send_input(inp)

    def left_down(self):
        self._button(_MF_LEFTDOWN, 0)

    def left_up(self):
        self._button(0, _MF_LEFTUP)

    def right_down(self):
        self._button(_MF_RIGHTDOWN, 0)

    def right_up(self):
        self._button(0, _MF_RIGHTUP)

    def middle_down(self):
        self._button(_MF_MIDDLEDOWN, 0)

    def middle_up(self):
        self._button(0, _MF_MIDDLEUP)

    def _key(self, vk, flags):
        vk = _GENERIC_VK.get(vk, vk)                 # L/R 修饰键→通用码(通道间一致)
        scan = _u32.MapVirtualKeyW(vk, 0)
        if not scan:
            raise ValueError(f"VK 0x{vk:X} 无扫描码(键盘布局不认识)")
        if vk in _EXTENDED_VKS:
            flags |= _KEF_EXTENDEDKEY
        inp = _INPUT(type=_INPUT_KEYBOARD)
        inp.union.ki = _KEYBDINPUT(0, scan, flags | _KEF_SCANCODE, 0, 0)
        _send_input(inp)

    def key_down(self, vk):
        self._ensure_fg()
        self._key(vk, 0)

    def key_up(self, vk):
        self._key(vk, _KEF_KEYUP)

    def key_tap(self, vk):
        self.key_down(vk)
        time.sleep(self.delay)
        self.key_up(vk)

    def type_text(self, text, per_char_delay=0.03):
        """打字: SendInput KEYEVENTF_UNICODE(VK_PACKET)逐 UTF-16 码元注入 ——
        直接生成 WM_CHAR, 绕过键盘布局与输入法(中文系统 IME 活动时, 扫描码/
        VK 注入的字母键会被 IME 吞成组合态, 实测 vk 被改写成 0xE5, 打不出字)。"""
        self._ensure_fg()
        data = text.encode("utf-16-le")          # 代理对自动拆成两个码元
        for i in range(0, len(data), 2):
            unit = int.from_bytes(data[i:i + 2], "little")
            inp = _INPUT(type=_INPUT_KEYBOARD)
            inp.union.ki = _KEYBDINPUT(0xE7, unit, _KEF_UNICODE, 0, 0)
            _send_input(inp)
            inp.union.ki = _KEYBDINPUT(0xE7, unit, _KEF_UNICODE | _KEF_KEYUP, 0, 0)
            _send_input(inp)
            time.sleep(per_char_delay)
