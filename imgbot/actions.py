"""拟人化操作(安卓侧): 点击随机偏移 + 随机延迟.

PC 窗口侧的拟人(贝塞尔轨迹)在 imgbot.win32.input; 两边量级不同是刻意的 ——
adb input tap 无光标概念, 偏移+延迟即可; PostMessage 有光标语义, 需要轨迹.
"""
from __future__ import annotations

import random
import time
from typing import NamedTuple

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


class TapTarget(NamedTuple):
    """表驱动的一项: 等它出现 → 点它.

    name: 模板文件名(vision.load 缓存).
    point: 点击坐标; None=点模板命中中心. 模板认界面、点击走固定坐标时用它
           (广告页X认模板但点在固定处, 点命中处会误触「立即前往」).
    region: 搜索矩形 (x1,y1,x2,y2); None=全屏. 位置固定的元素必须限矩形.
    threshold: 该项的匹配阈值, None=用默认.
    """
    name: str
    point: tuple[int, int] | None = None
    region: tuple[int, int, int, int] | None = None
    threshold: float | None = None


def wait_tap_any(device: AdbDevice, targets: list[TapTarget], timeout: float = 30,
                 interval: float = 0.4, until: set[str] | None = None,
                 stop_event=None, log=None) -> set[str]:
    """表驱动轮询: 一组模板里**谁出现点谁**(不讲顺序), 循环到超时或 until 集合集齐.

    targets: [TapTarget, ...] —— 每项一个模板 + 点击方式, 见 TapTarget.
    until: 这些模板名都出现过即提前结束(达成即止); None=只按 timeout 收口.
    返回: 本轮点过的模板名集合(点过哪些).

    与 wait_tap(单模板)的区别: 这个是多目标表驱动, 谁先出现先点谁.
    与"有序序列"的区别: 不保证顺序 —— 同一帧多个都在时按 targets 顺序点第一个,
    下一轮再点剩下的(避免一帧多点把画面点乱).

    一帧多查: 一轮截图里把 targets 全部找完(省截图), 命中项点完继续下一轮.
    stop_event 置位立即停止(与 vision.wait_find 同语义: 停止优先, 不再点).
    模板文件缺失的项跳过(素材可渐进添加, 不让整表崩).
    """
    deadline = time.monotonic() + timeout
    tpls = []
    for t in targets:
        try:
            tpls.append((t, vision.load(t.name)))
        except FileNotFoundError:
            continue
    done: set[str] = set()
    while True:
        if stop_event is not None and stop_event.is_set():
            return done
        screen = vision.decode_png(device.screenshot())
        for t, tpl in tpls:
            if t.name in done:
                continue
            thr = t.threshold if t.threshold is not None else vision.DEFAULT_THRESHOLD
            hit = vision.find(screen, tpl, thr, t.region)
            if not hit:
                continue
            x, y = t.point if t.point is not None else (hit[0], hit[1])
            tap(device, x, y)
            done.add(t.name)
            if log is not None:
                log(f"wait_tap_any 命中并点击 「{t.name}」({x},{y}) score={hit[2]:.2f}")
            break                       # 一帧只点一个: 下一轮重新截图再点剩下的
        if until is not None and until <= done:
            return done
        if time.monotonic() >= deadline:
            return done
        time.sleep(interval)
