# -*- coding: utf-8 -*-
"""dx 通道拟人键鼠: 大漠 dx.mouse/dx.keypad 对应物。

与 win32.input 两通道共用 BaseMouseKeyboard(节奏/光标记账单源在基类), 本类
只实现投递原语: 消息在目标进程内合成(SendMessageTimeout 同步到底), 拟人化
(轨迹/延时)仍在 Python 侧做 —— 注入件只管"把一条消息送到目标窗口"。
"""
from __future__ import annotations

import ctypes
import random
import time

from ..win32.input import BaseMouseKeyboard, cursor_seed
from ..win32.window import WindowLostError
from .host import (KEY_CHAR, KEY_DOWN, KEY_UP, M_DBLCLK, M_LDOWN, M_LUP,
                   M_MDOWN, M_MOVE, M_MUP, M_RDOWN, M_RUP, M_WHEEL_DOWN,
                   M_WHEEL_UP, DxHost)

_M_OPS = {"left": (M_LDOWN, M_LUP), "right": (M_RDOWN, M_RUP),
          "middle": (M_MDOWN, M_MUP)}


class DxMouseKeyboard(BaseMouseKeyboard):
    """一个 DxHost + 一个窗口句柄 = 一套后台键鼠(等价 MouseKeyboard 的 dx 版)。"""

    def __init__(self, host: DxHost, hwnd: int, inter_action_delay: float = 0.05,
                 rng: random.Random | None = None):
        self.host = host
        self.hwnd = hwnd
        super().__init__(inter_action_delay, rng)
        self._pos = cursor_seed(hwnd)   # 首击起点=真实光标(钳内), 非 (0,0) 会话指纹

    def _alive(self):
        if not ctypes.windll.user32.IsWindow(self.hwnd):
            raise WindowLostError("目标窗口已失效(被关闭?), 需重新绑定")

    def _move_raw(self, x: int, y: int):
        self.host.mouse(x, y, M_MOVE)
        self._pos = (x, y)

    def _press(self, btn, down, x, y):
        op_down, op_up = _M_OPS[btn]
        self.host.mouse(x, y, op_down if down else op_up)

    def _second_press(self, x, y):
        self.host.mouse(x, y, M_DBLCLK)     # 第二击用双击语义(WM_LBUTTONDBLCLK)
        time.sleep(self.delay)
        self.host.mouse(x, y, M_LUP)

    def _wheel(self, delta):
        x, y = self._pos
        self.host.mouse(x, y, M_WHEEL_UP if delta > 0 else M_WHEEL_DOWN)

    def _key_down(self, vk: int):
        """按键投递(扫描码/SYSKEY 配方在注入件内按大漠 0x837 合成)。"""
        self.host.key(vk, KEY_DOWN)

    def _key_up(self, vk: int):
        self.host.key(vk, KEY_UP)

    def _char(self, unit: int):
        self.host.key(unit, KEY_CHAR)
