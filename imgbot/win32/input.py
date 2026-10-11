# -*- coding: utf-8 -*-
"""后台键鼠(LZG_PMH 移植) — PostMessage 通道.

- 鼠标: WM_MOUSEMOVE / WM_LBUTTONDOWN(MK_LBUTTON) / WM_LBUTTONUP, 坐标进 lParam;
  点击前走贝塞尔拟人轨迹(先快后慢 + 随机弯度), 不瞬移;
- 中文: WM_CHAR 逐码元(UTF-16, 代理对自动拆分) 或 send_paste 剪贴板粘贴;
- 后台键盘(ESC 等)实测无效 → 关闭/取消类操作一律用点击, 不提供 key_tap;
- 前台 SendInput 通道仅作保留退路, 暂不实现。
"""
import ctypes
import math
import random
import time

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
    u32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32
    data = text.encode("utf-16-le") + b"\x00\x00"
    if not u32.OpenClipboard(None):
        raise OSError("OpenClipboard 失败(剪贴板被占用?)")
    try:
        u32.EmptyClipboard()
        h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            raise OSError("GlobalAlloc 失败")
        p = k32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        k32.GlobalUnlock(h)
        if not u32.SetClipboardData(CF_UNICODETEXT, h):
            k32.GlobalFree(h)                # 没交给剪贴板才需要自己释放
            raise OSError("SetClipboardData 失败")
    finally:
        u32.CloseClipboard()
    window.post_message(WM_PASTE, 0, 0)
