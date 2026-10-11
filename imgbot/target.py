"""操作目标抽象(大漠 BindWindow 思想): 绑定一次目标, 之后截图/输入全打它.

双后端: AdbTarget(安卓模拟器/真机) / Win32Target(PC 窗口).
capture/input 通道参数留位(加新通道不改调用方), 一期各只实现一种组合:
- AdbTarget:   capture=adb exec-out screencap, input=adb shell input
- Win32Target: capture=PrintWindow 后台,       input=PostMessage 后台

图色/找字/读数层(vision/ocr/readout)吃 BGR ndarray, 与 Target 无关 ——
新项目典型用法:
    t = Win32Target(GameWindow.find(class_name="XXX"))
    hit = vision.find(t.screenshot(), vision.load("btn.png"))
    actions 拟人层按目标各有实现(adb: actions.py / win32: MouseKeyboard 贝塞尔).
"""
from __future__ import annotations

import numpy as np

from . import ime, vision
from .adb import AdbDevice
from .win32.capture import capture_client
from .win32.input import MouseKeyboard, send_paste
from .win32.window import GameWindow


class Target:
    """最小公共接口: 截图 + 坐标输入. 后端特有能力直接挂各自类上."""

    def screenshot(self) -> np.ndarray:
        raise NotImplementedError

    def tap(self, x: int, y: int):
        raise NotImplementedError


class AdbTarget(Target):
    def __init__(self, serial: str, adb_path: str = "adb"):
        self.device = AdbDevice(serial, adb_path)
        self.serial = serial

    def screenshot(self) -> np.ndarray:
        return vision.decode_png(self.device.screenshot())

    def save_screenshot(self, path) -> tuple[int, int]:
        return self.device.save_screenshot(path)

    def tap(self, x: int, y: int):
        self.device.tap(x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 300):
        self.device.swipe(x1, y1, x2, y2, ms)

    def keyevent(self, code: int):
        self.device.keyevent(code)

    def shell(self, *args: str) -> str:
        return self.device.shell(*args)

    def type_text(self, text: str):
        """中文注入(ADBKeyboard 广播); 首次调用自动装输入法, 用完 ime.restore 还原."""
        if not ime.ensure(self.device, self.serial):
            raise RuntimeError("输入法组件未就绪, 无法注入文字")
        ime.type_text(self.device, text)

    def start_app(self, package: str, activity: str):
        self.device.start_app(package, activity)

    def stop_app(self, package: str):
        self.device.stop_app(package)


class Win32Target(Target):
    def __init__(self, hwnd_or_window, capture: str = "printwindow", input: str = "postmessage"):
        self.window = hwnd_or_window if isinstance(hwnd_or_window, GameWindow) \
            else GameWindow(hwnd_or_window)
        if capture != "printwindow":
            raise NotImplementedError(f"capture 通道未实现: {capture}(留位: gdi/前台bitblt)")
        if input != "postmessage":
            raise NotImplementedError(f"input 通道未实现: {input}(留位: 前台sendinput)")
        self.mk = MouseKeyboard(self.window)

    def screenshot(self) -> np.ndarray:
        return capture_client(self.window)

    def tap(self, x: int, y: int):
        self.mk.click(x, y)

    def double_click(self, x: int, y: int):
        self.mk.double_click(x, y)

    def hover(self, x: int, y: int, duration: float = 1.5):
        self.mk.hover(x, y, duration)

    def type_text(self, text: str):
        self.mk.type_text(text)

    def paste(self, text: str):
        send_paste(self.window, text)

    def keyevent(self, code: int):
        raise NotImplementedError("后台键盘实测无效(ESC 类), 关闭/取消操作用点击")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 300):
        raise NotImplementedError("PC 后台拖拽未实现(WM_MOUSEMOVE+按住序列), 用到再加")
