"""拟人化操作(安卓侧): 点击随机偏移 + 随机延迟.

PC 窗口侧的拟人(贝塞尔轨迹)在 imgbot.win32.input; 两边量级不同是刻意的 ——
adb input tap 无光标概念, 偏移+延迟即可; PostMessage 有光标语义, 需要轨迹.
"""
from __future__ import annotations

import random
import time

import numpy as np

from . import vision
from .adb import AdbDevice


def _jitter(x: int, y: int, jitter_px: int) -> tuple[int, int]:
    """随机偏移(±jitter_px): adb 无光标概念, 偏移即可."""
    return (x + random.randint(-jitter_px, jitter_px),
            y + random.randint(-jitter_px, jitter_px))


def tap(device: AdbDevice, x: int, y: int, jitter_px: int = 3,
        delay_ms: tuple[int, int] = (300, 800)):
    """随机偏移点击, 点击前随机停顿."""
    px, py = _jitter(x, y, jitter_px)
    time.sleep(random.uniform(*delay_ms) / 1000)
    device.tap(px, py)


def swipe(device: AdbDevice, x1: int, y1: int, x2: int, y2: int, ms: int = 350,
          jitter_px: int = 3, delay_ms: tuple[int, int] = (300, 800)):
    """随机偏移滑动(两端各自偏移), 滑动前随机停顿."""
    time.sleep(random.uniform(*delay_ms) / 1000)
    x1, y1 = _jitter(x1, y1, jitter_px)
    x2, y2 = _jitter(x2, y2, jitter_px)
    device.swipe(x1, y1, x2, y2, ms)


def pause(seconds: tuple[float, float] = (1.0, 3.0), stop_event=None):
    """任务间随机停顿; stop_event 可提前打断."""
    end = time.monotonic() + random.uniform(*seconds)
    while not (stop_event is not None and stop_event.is_set()) and time.monotonic() < end:
        time.sleep(0.1)


def wait_tap(device: AdbDevice, name: str | np.ndarray, timeout: float = 30,
             interval: float = 0.4, threshold: float = vision.DEFAULT_THRESHOLD,
             stop_event=None, log=None) -> tuple[int, int, float] | None:
    """轮询等模板出现, 出现即拟人点击("循环等待+出现点击"通用原语, 魔神仓提炼下沉).

    命中: tap 后返回 (中心x, 中心y, 分数); 超时/停止返回 None —— 不抛错、不另存失败
    截图, 失败语义由调用方交代(要"强等待"自行 raise), 区分超时与停止用 stop_event.is_set().
    name 为模板文件名(vision.load 缓存)或已加载模板图; log 可选回调, 命中点击时调用.
    轮询语义同 vision.wait_find(一轮一帧, 库不带帧缓存); stop_event 置位即停, 不再点击.
    """
    hit = vision.wait_find(lambda: vision.decode_png(device.screenshot()), name,
                           timeout, interval, threshold, stop_event)
    if hit is None:
        return None
    tap(device, hit[0], hit[1])
    if log is not None:
        log(f"wait_tap 命中并点击 ({hit[0]},{hit[1]}) score={hit[2]:.2f}")
    return hit
