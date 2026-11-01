# -*- coding: utf-8 -*-
"""dx 通道拟人键鼠: 大漠 dx.mouse/dx.keypad 对应物, 复用 win32 侧贝塞尔轨迹。

与 win32.input.MouseKeyboard 同 API 形状(move/click/double_click/type_text),
消息在目标进程内合成(SendMessageTimeout 同步到底), 拟人化(轨迹/延时)仍在
Python 侧做 —— 注入件只管"把一条消息送到目标窗口", 节奏由宿主控制。
"""
from __future__ import annotations

import ctypes
import random
import time

from ..win32.input import cursor_seed, human_path
from ..win32.window import WindowLostError
from .host import (KEY_CHAR, KEY_DOWN, KEY_UP, M_DBLCLK, M_LDOWN, M_LUP,
                   M_MDOWN, M_MOVE, M_MUP, M_RDOWN, M_RUP, M_WHEEL_DOWN,
                   M_WHEEL_UP, DxHost)


class DxMouseKeyboard:
    """一个 DxHost + 一个窗口句柄 = 一套后台键鼠(等价 MouseKeyboard 的 dx 版)。"""

    def __init__(self, host: DxHost, hwnd: int, inter_action_delay: float = 0.05,
                 rng: random.Random | None = None):
        self.host = host
        self.hwnd = hwnd
        self.delay = inter_action_delay
        self.rng = rng or random.Random()
        self._pos = cursor_seed(hwnd)   # 首击起点=真实光标(钳内), 非 (0,0) 会话指纹

    def _alive(self):
        if not ctypes.windll.user32.IsWindow(self.hwnd):
            raise WindowLostError("目标窗口已失效(被关闭?), 需重新绑定")

    def _move(self, x: int, y: int):
        self.host.mouse(x, y, M_MOVE)
        self._pos = (x, y)

    def move(self, x: int, y: int, steps: int = 0, interval: float = 0.018):
        """移动光标; steps>0 时插值(与 MouseKeyboard.move 同语义)。"""
        self._alive()
        if steps <= 0:
            self._move(x, y)
            return
        x0, y0 = self._pos
        for i in range(1, steps + 1):
            t = i / steps
            self._move(round(x0 + (x - x0) * t), round(y0 + (y - y0) * t))
            time.sleep(interval)

    def _glide(self, x: int, y: int):
        """沿拟人贝塞尔轨迹移动到目标(click/hover 共用的接近方式)。"""
        self._alive()
        for px, py, dt in human_path(self._pos[0], self._pos[1], x, y, self.rng):
            self._move(px, py)
            if dt:
                time.sleep(dt)

    def click(self, x: int, y: int, settle: float | None = None):
        """后台点击: 拟人轨迹到位 → 左键按下/抬起(同步送达)。
        settle: 到位停顿秒数覆盖(None=默认 inter_action_delay)。"""
        self._glide(x, y)
        time.sleep(self.delay if settle is None else settle)
        self.host.mouse(x, y, M_LDOWN)
        time.sleep(self.delay)
        self.host.mouse(x, y, M_LUP)
        self._pos = (x, y)

    def right_click(self, x: int, y: int):
        self._glide(x, y)
        time.sleep(self.delay)
        self.host.mouse(x, y, M_RDOWN)
        time.sleep(self.delay)
        self.host.mouse(x, y, M_RUP)
        self._pos = (x, y)

    def double_click(self, x: int, y: int, gap: float = 0.09,
                     settle: float | None = None):
        """后台双击: 单击后紧跟 WM_LBUTTONDBLCLK(第二击用双击语义)。"""
        self.click(x, y, settle=settle)
        time.sleep(gap)                      # 须落在系统双击间隔内(默认 500ms)
        self.host.mouse(x, y, M_DBLCLK)
        time.sleep(self.delay)
        self.host.mouse(x, y, M_LUP)
        self._pos = (x, y)

    def middle_click(self, x: int | None = None, y: int | None = None):
        """中键在指定点(默认当前光标位)按下/抬起(大漠 MiddleClick)。"""
        if (x is None) != (y is None):
            raise ValueError("x/y 须成对给出, 或都缺省用当前光标位")
        px, py = (x, y) if x is not None else self._pos
        self._move(px, py)
        time.sleep(self.delay)
        self.host.mouse(px, py, M_MDOWN)
        time.sleep(self.delay)
        self.host.mouse(px, py, M_MUP)

    def drag(self, x1: int, y1: int, x2: int, y2: int, steps: int = 12,
             interval: float = 0.03):
        """按住左键拖拽(上滑/下拉手势); 直线插值, 手势轨迹不需要拟人弯度。"""
        self._alive()
        self._move(x1, y1)
        time.sleep(self.delay)
        self.host.mouse(x1, y1, M_LDOWN)
        time.sleep(self.delay)
        for i in range(1, steps + 1):
            t = i / steps
            self._move(round(x1 + (x2 - x1) * t), round(y1 + (y2 - y1) * t))
            time.sleep(interval)
        time.sleep(self.delay)
        self.host.mouse(x2, y2, M_LUP)
        self._pos = (x2, y2)

    def wheel(self, up: bool = True):
        self.host.mouse(self._pos[0], self._pos[1],
                        M_WHEEL_UP if up else M_WHEEL_DOWN)

    def key_tap(self, vk: int):
        """按键(按下+抬起, 扫描码/SYSKEY 配方在注入件内)。"""
        self._alive()
        self.host.key(vk, KEY_DOWN)
        time.sleep(self.delay)
        self.host.key(vk, KEY_UP)

    def key_down(self, vk: int):
        self.host.key(vk, KEY_DOWN)

    def key_up(self, vk: int):
        self.host.key(vk, KEY_UP)

    def type_text(self, text: str, per_char_delay: float = 0.03):
        """逐 UTF-16 码元 WM_CHAR(代理对自动拆分, 与 MouseKeyboard 同语义)。"""
        self._alive()
        data = text.encode("utf-16-le")
        for i in range(0, len(data), 2):
            self.host.key(int.from_bytes(data[i:i + 2], "little"), KEY_CHAR)
            time.sleep(per_char_delay)
