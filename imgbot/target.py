"""操作目标抽象: 绑定一次目标, 之后截图/输入全打它(大漠 BindWindow 思想).

adb 后端: AdbTarget(安卓模拟器/真机) —— capture=adb exec-out screencap,
input=adb shell input(天然后台); 满足 FrameSource 帧源协议, 可直接喂 Ops 门面
(screen/keymouse/GetBindWindow/GetClientSize 四件齐, 拟人点击走 actions 层).
PC 窗口侧不设 Target 门面, 统一走 DmSoft.BindWindow(大漠全模式字符串:
normal/gdi/dx × windows/windows2/dx/normal, 见 imgbot.dmsoft —— 潜影 dx 通道
也在那里, 一处绑定全管).

图色/找字/读数层(vision/ocr/readout)吃 BGR ndarray, 与 Target 无关 ——
新项目典型用法:
    t = AdbTarget("127.0.0.1:5555")
    hit = vision.find(t.screenshot(), vision.load("btn.png"))
拟人层按目标各有实现(adb: actions.py 偏移+延迟 / win32: DmSoft.keymouse 贝塞尔).
"""
from __future__ import annotations

import random
import time

import numpy as np

from . import actions, ime, vision
from .adb import AdbDevice


class Target:
    """最小公共接口: 截图 + 坐标输入. 后端特有能力直接挂各自类上."""

    def screenshot(self) -> np.ndarray:
        raise NotImplementedError

    def tap(self, x: int, y: int):
        raise NotImplementedError


class _AdbKeyMouse:
    """adb 键鼠鸭子(对齐 BaseMouseKeyboard 的 Ops 用面): click/double_click/drag
    全走 actions 拟人层(±3px 偏移 + 前置随机停顿); settle=调用方追加的到位停顿
    (点击前摇, 与 win32 通道同名参数同语义)。双击两击间 80~120ms —— 零间隔
    真机不认双击(imgbot-template _AdbMk 同款配方下沉)。"""

    def __init__(self, device: AdbDevice):
        self._d = device

    def click(self, x, y, settle=None):
        if settle:
            time.sleep(settle)
        actions.tap(self._d, x, y)

    def double_click(self, x, y, gap=0.09, settle=None):
        """双击: 单击后隔 gap 秒跟第二击(与 win32 通道同形; 0.09 在真机认双击的
        80~120ms 窗口内 —— 零间隔真机不认, imgbot-template _AdbMk 配方下沉)。"""
        self.click(x, y, settle)
        time.sleep(gap)
        self.click(x, y)

    def drag(self, x1, y1, x2, y2, steps=12, interval=0.03):
        """线性滑动(steps*interval 拍 → swipe 时长, 下限 150ms 防瞬移)。"""
        actions.swipe(self._d, x1, y1, x2, y2,
                      ms=max(150, int(steps * interval * 1000)))


class AdbTarget(Target):
    def __init__(self, serial: str, adb_path: str = "adb"):
        self.device = AdbDevice(serial, adb_path)
        self._mk: _AdbKeyMouse | None = None

    @property
    def serial(self) -> str:
        return self.device.serial

    # ---- 帧源协议(ops.FrameSource): 四件齐, 可直接 Ops(AdbTarget(...)) ----
    def screen(self) -> np.ndarray:
        """Ops 帧源 seam 的取帧名(与 screenshot 同物)。"""
        return self.screenshot()

    @property
    def keymouse(self) -> _AdbKeyMouse:
        """拟人键鼠(actions 层)—— Ops.find_tap/swipe_find 走这里。"""
        if self._mk is None:
            self._mk = _AdbKeyMouse(self.device)
        return self._mk

    def GetBindWindow(self) -> int:
        """帧源协议对应物: adb 无窗口句柄概念, 恒 0。"""
        return 0

    def GetClientSize(self, _hwnd: int = 0) -> tuple[int, int]:
        """设备分辨率 (w, h)(帧源协议对应物; adb 无客户区, 从当前帧取)。"""
        img = self.screenshot()
        return img.shape[1], img.shape[0]

    # ---- Target 基本面 ----
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
