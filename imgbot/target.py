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

    @property
    def serial(self) -> str:
        return self.device.serial

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
        if not ime.ensure(self.device):
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
        self._dx = None
        self._capture = capture
        if capture not in ("printwindow", "dx"):
            raise NotImplementedError(f"capture 通道未实现: {capture}(留位: gdi/前台bitblt)")
        if input not in ("postmessage", "dx"):
            raise NotImplementedError(f"input 通道未实现: {input}(留位: 前台sendinput/驱动)")
        if "dx" in (capture, input):
            from .dx.host import DxHost
            self._dx = DxHost(self.window.pid())
            self._dx.bind()
            if capture == "dx":
                self._dx.hook_auto()       # 按已加载图形栈选 gl/d3d9; dxgi 走阶段 3
        if input == "dx":
            from .dx.input import DxMouseKeyboard
            self._dx.bind_input(self.window.hwnd)
            self.mk = DxMouseKeyboard(self._dx, self.window.hwnd)
        else:
            self.mk = MouseKeyboard(self.window)

    def close(self):
        """解绑 dx 注入件(不调则靠目标进程退出兜底收口)。"""
        if self._dx is not None:
            self._dx.unbind()
            self._dx = None

    def screenshot(self) -> np.ndarray:
        if self._capture == "dx":
            return self._dx.capture()
        return capture_client(self.window)

    def tap(self, x: int, y: int):
        self.mk.click(x, y)

    def drag(self, x1: int, y1: int, x2: int, y2: int, ms: int = 300):
        """后台拖拽(dx 通道); postmessage 通道未实现。"""
        if hasattr(self.mk, "drag"):
            self.mk.drag(x1, y1, x2, y2, steps=max(6, ms // 25))
        else:
            raise NotImplementedError("postmessage 通道拖拽未实现, 用 input='dx'")

    def keyevent(self, code: int):
        """按键(统一入口)。dx 通道可用; postmessage 通道键盘是历史死案, 不提供。"""
        self.key_tap(code)

    def key_tap(self, vk: int):
        """后台按键(dx 通道); postmessage 通道键盘是历史死案, 不提供。"""
        if hasattr(self.mk, "key_tap"):
            self.mk.key_tap(vk)
        else:
            raise NotImplementedError("postmessage 通道键盘实测无效, 用 input='dx' 或点击")

    def double_click(self, x: int, y: int):
        self.mk.double_click(x, y)

    def hover(self, x: int, y: int, duration: float = 1.5):
        self.mk.hover(x, y, duration)

    def type_text(self, text: str):
        self.mk.type_text(text)

    def paste(self, text: str):
        send_paste(self.window, text)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 300):
        self.drag(x1, y1, x2, y2, ms)
