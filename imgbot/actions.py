"""拟人化操作(安卓侧): 点击随机偏移 + 随机延迟.

PC 窗口侧的拟人(贝塞尔轨迹)在 imgbot.win32.input; 两边量级不同是刻意的 ——
adb input tap 无光标概念, 偏移+延迟即可; PostMessage 有光标语义, 需要轨迹.
"""
from __future__ import annotations

import random
import time

from .adb import AdbDevice


def tap(device: AdbDevice, x: int, y: int, jitter_px: int = 3,
        delay_ms: tuple[int, int] = (300, 800)):
    """随机偏移点击, 点击前随机停顿."""
    dx = random.randint(-jitter_px, jitter_px)
    dy = random.randint(-jitter_px, jitter_px)
    time.sleep(random.uniform(*delay_ms) / 1000)
    device.tap(x + dx, y + dy)


def swipe(device: AdbDevice, x1: int, y1: int, x2: int, y2: int, ms: int = 350,
          jitter_px: int = 3, delay_ms: tuple[int, int] = (300, 800)):
    """随机偏移滑动(两端各自偏移), 滑动前随机停顿."""
    j = lambda: random.randint(-jitter_px, jitter_px)
    time.sleep(random.uniform(*delay_ms) / 1000)
    device.swipe(x1 + j(), y1 + j(), x2 + j(), y2 + j(), ms)


def pause(seconds: tuple[float, float] = (1.0, 3.0), stop_event=None):
    """任务间随机停顿; stop_event 可提前打断."""
    end = time.monotonic() + random.uniform(*seconds)
    while not (stop_event is not None and stop_event.is_set()) and time.monotonic() < end:
        time.sleep(0.1)
