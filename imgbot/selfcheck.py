"""库级自检(无真机/无目标窗口): uv run python -m imgbot.selfcheck

覆盖: adb 解析 / vision 全套(模板/键控/多点找色/区域找色族/卡死判定) /
ocr 纯逻辑 / readout 逐字读数 / win32 后台键鼠+截图(离线窗口).
"""
import ctypes
import ctypes.wintypes as wt
import random
import re
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from . import actions, adb as adb_mod
from . import ime, vision
from .ocr import best_match
from .readout import enlarge, read_digits
from .win32.capture import capture_client, capture_screen, virtual_screen_rect
from .win32.input import (CF_UNICODETEXT, WM_CHAR, WM_LBUTTONDOWN, WM_LBUTTONUP,
                          WM_MOUSEMOVE, WM_PASTE, MouseKeyboard, human_path,
                          send_paste)
from .win32.window import GameWindow, list_windows, xor_frame

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)
_user32.DefWindowProcW.restype = ctypes.c_ssize_t
_user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
WM_QUIT = 0x0012
HWND_MESSAGE = ctypes.c_void_p(-3)
# GetModuleHandleW 默认 restype 是 32 位 int, ASLR 下句柄超 32 位会被截断,
# 带着假 hInstance 建窗口会在消息分发期 access violation(实测) —— 必须声明指针原型
_kernel32.GetModuleHandleW.restype = wt.HMODULE
_kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
# 原型声明跟着 WinDLL 实例走: input.py 声明在 ctypes.windll 单例上, 这里的私有实例要自己声明
_user32.GetClipboardData.restype = ctypes.c_void_p
_user32.GetClipboardData.argtypes = [ctypes.c_uint]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HANDLE), ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HANDLE), ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HANDLE),
    ]


def _register_class(cls: str, wndproc, brush=None):
    """注册窗口类并返回 (hwnd 相关准备好的 hinst, wc).

    wc 必须被调用方持有: 回收后窗口类指向已释放蹦床, Dispatch 即崩.
    """
    hinst = _kernel32.GetModuleHandleW(None)
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = wndproc
    wc.hInstance = hinst
    wc.hbrBackground = brush if brush is not None else 0
    wc.lpszClassName = cls
    if not _user32.RegisterClassExW(ctypes.byref(wc)):
        raise OSError("RegisterClassExW 失败: %d" % ctypes.get_last_error())
    return hinst, wc


def create_message_window():
    """message-only 窗口, WndProc 记录到达的消息(LZG_PMH tests/_winhelper 思路).
    返回 (hwnd, records, pump) — pump() 处理队列后返回."""
    records = []
    seq = create_message_window._n = getattr(create_message_window, "_n", 0) + 1

    @WNDPROC
    def wndproc(hwnd, msg, wp, lp):
        records.append((int(msg), int(wp), int(lp)))
        return _user32.DefWindowProcW(hwnd, msg, wp, lp)

    hinst, wc = _register_class("ImgbotMsg%d" % seq, wndproc)
    hwnd = _user32.CreateWindowExW(0, wc.lpszClassName, "", 0, 0, 0, 0, 0, HWND_MESSAGE, None, ctypes.c_void_p(hinst), None)
    if not hwnd:
        raise OSError("CreateWindowExW 失败: %d" % ctypes.get_last_error())

    def pump():
        _user32.PostThreadMessageW(_kernel32.GetCurrentThreadId(), WM_QUIT, 0, 0)
        msg = wt.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

    pump._keep = (wndproc, wc)  # 防 GC: 回调/类结构被回收后窗口类指向已释放蹦床, Dispatch 即崩
    return hwnd, records, pump


def create_visible_window(width, height, bg_rgb):
    """显示出来的纯色窗口(截屏链路验证用), 返回 (hwnd, cleanup)."""
    r, g, b = bg_rgb
    brush = _gdi32.CreateSolidBrush(r | (g << 8) | (b << 16))
    seq = create_visible_window._n = getattr(create_visible_window, "_n", 0) + 1

    @WNDPROC
    def wndproc(hwnd, msg, wp, lp):
        return _user32.DefWindowProcW(hwnd, msg, wp, lp)

    create_visible_window._keep = wndproc     # 防 WNDPROC 回调被 GC 后窗口崩溃
    hinst, wc = _register_class("ImgbotVis%d" % seq, wndproc, brush)
    hwnd = _user32.CreateWindowExW(
        0, wc.lpszClassName, "imgbot-selfcheck", 0x10CF0000,  # WS_OVERLAPPEDWINDOW
        100, 100, width + 16, height + 39, None, None, ctypes.c_void_p(hinst), None)
    _user32.ShowWindow(hwnd, 1)
    _user32.UpdateWindow(hwnd)

    def cleanup():
        _user32.DestroyWindow(hwnd)
        _user32.UnregisterClassW(wc.lpszClassName, ctypes.c_void_p(hinst))
        _gdi32.DeleteObject(brush)

    return hwnd, cleanup


def fake_png(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big")


def main():
    # ---- adb 解析 ----
    assert adb_mod.png_size(fake_png(960, 540)) == (960, 540)
    assert adb_mod.parse_devices(
        "List of devices attached\n"
        "127.0.0.1:5555\tdevice\n"
        "127.0.0.1:5557\toffline\n"
        "emulator-5554\tunauthorized\n"
    ) == ["127.0.0.1:5555"]
    # 模拟器双通道去重: emulator-5554 与 127.0.0.1:5555 是同一台(控制台+TCP), 只留 TCP
    assert adb_mod.parse_devices(
        "List of devices attached\n"
        "127.0.0.1:5555\tdevice\n"
        "emulator-5554\tdevice\n"
    ) == ["127.0.0.1:5555"]
    assert adb_mod.parse_devices(  # 只有控制台通道(无 TCP)时如实保留
        "List of devices attached\n"
        "emulator-5554\tdevice\n"
    ) == ["emulator-5554"]

    # ---- 模板匹配 ----
    rng = np.random.default_rng(7)
    screen = rng.integers(0, 255, (540, 960, 3), dtype=np.uint8)
    patch = screen[100:140, 300:420].copy()
    tpl = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    hit = vision.find(screen, tpl)
    assert hit and abs(hit[0] - 360) <= 1 and abs(hit[1] - 120) <= 1 and hit[2] > 0.99, hit
    assert vision.find(np.zeros((540, 960, 3), np.uint8), tpl, 0.99) is None  # NaN/不相似 → 未命中
    # 零方差防虚高(LZG 移植): 纯色模板在纯色屏上相关系数满分也必须判未命中
    flat = np.full((20, 20), 128, np.uint8)
    assert vision.find(np.full((540, 960, 3), 128, np.uint8), flat, 0.5) is None
    assert vision.find_all(np.full((540, 960, 3), 128, np.uint8), flat, 0.5) == []

    # find_all 多目标+去重: 一排三个同款图标
    icon = rng.integers(0, 255, (16, 16, 3), dtype=np.uint8)
    row = np.zeros((200, 400, 3), np.uint8)
    for x in (50, 150, 250):
        row[80:96, x:x + 16] = icon
    hits = vision.find_all(row, icon, 0.99)
    assert [h[0] for h in hits] == [58, 158, 258], hits

    # find_any: 多模板任一命中取最高分; 缺失素材跳过(渐进配图不炸)
    fake_tpls = {"a.png": tpl, "b.png": None}

    def _fake_load(name):
        if fake_tpls.get(name) is None:
            raise FileNotFoundError(name)
        return fake_tpls[name]

    with mock.patch.object(vision, "load", _fake_load):
        hit = vision.find_any(screen, ["a.png", "b.png"])
        assert hit and abs(hit[0] - 360) <= 1 and abs(hit[1] - 120) <= 1 and hit[2] > 0.99
        assert vision.find_any(screen, ["b.png", "c.png"]) is None  # 全缺失 → None
    same = rng.integers(0, 255, (540, 960, 3), dtype=np.uint8)
    assert not vision.changed(same, same.copy())
    assert vision.changed(same, rng.integers(0, 255, (540, 960, 3), dtype=np.uint8))

    # ---- wait_tap: 轮询出现即点(假帧序列注入; 命中/超时/停止三分支) ----
    class _FakeDev:  # AdbDevice 替身: screenshot() 吐 PNG bytes, 帧耗尽后停最后一帧
        def __init__(self, frames: list[np.ndarray]):
            self.frames, self.i = frames, 0

        def screenshot(self) -> bytes:
            img = self.frames[min(self.i, len(self.frames) - 1)]
            self.i += 1
            return cv2.imencode(".png", img)[1].tobytes()

    miss_frame = rng.integers(0, 255, (540, 960, 3), dtype=np.uint8)
    taps, logs = [], []
    with mock.patch.object(actions, "tap", lambda dev, x, y, **_: taps.append((x, y))), \
         mock.patch.object(vision, "load", lambda n: tpl):  # 劫持 tap/load, 不落盘
        hit3 = actions.wait_tap(_FakeDev([miss_frame, miss_frame, screen]), tpl,
                                timeout=5, interval=0.02, log=logs.append)
        assert hit3 and abs(hit3[0] - 360) <= 1 and abs(hit3[1] - 120) <= 1, hit3
        assert taps == [hit3[:2]] and logs and "score=" in logs[0], (taps, logs)
        assert actions.wait_tap(_FakeDev([miss_frame]), tpl,
                                timeout=0.25, interval=0.05) is None  # 不出现 → None
        assert len(taps) == 1 and len(logs) == 1                        # 超时不点击不多话
        ev = threading.Event()
        ev.set()
        t_stop = time.monotonic()
        assert actions.wait_tap(_FakeDev([miss_frame]), tpl, timeout=5,
                                interval=0.05, stop_event=ev) is None
        assert time.monotonic() - t_stop < 2, "stop_event 置位应立即返回, 不等满 timeout"
        # 停止优先: 已叫停且模板就在屏上 —— 不得命中更不得点击(停止≠命中)
        assert actions.wait_tap(_FakeDev([screen]), tpl, timeout=5,
                                interval=0.05, stop_event=ev) is None
        assert len(taps) == 1, f"停止后不应再点击, 实际点了 {len(taps)} 次"
        assert actions.wait_tap(_FakeDev([screen]), "btn.png",  # 模板文件名入参
                                timeout=2, interval=0.02)

    # 停止优先是 wait_find 的语义(不止 wait_tap): 已叫停时即使模板在屏上也不该返回命中
    ev2 = threading.Event()
    ev2.set()
    assert vision.wait_find(lambda: screen.copy(), tpl, timeout=5, interval=0.05,
                            stop_event=ev2) is None
    assert vision.wait_find(lambda: screen.copy(), tpl, timeout=5,
                            interval=0.05) is not None  # 无 stop_event 时正常命中

    # ---- wait_tap_any: 表驱动"谁出现点谁"(命中即点/固定点/until集齐/超时/stop) ----
    # 两个模板用**不同形状的确定图案**(纯随机纹理会互相关误命中: 同分布噪声块之间
    # 相关系数也能很高), 贴到干净底上; 底色远离图案色, 保证互不误命中
    pat_a = np.zeros((40, 120, 3), np.uint8)
    pat_a[:, ::2] = (250, 250, 250)          # 竖条纹
    pat_b = np.zeros((40, 120, 3), np.uint8)
    pat_b[::2, :] = (250, 250, 250)          # 横条纹
    tpl_a = cv2.cvtColor(pat_a, cv2.COLOR_BGR2GRAY)
    tpl_b = cv2.cvtColor(pat_b, cv2.COLOR_BGR2GRAY)
    clean = np.full((540, 960, 3), 40, np.uint8)
    frame_a = clean.copy()
    frame_a[100:140, 300:420] = pat_a
    frame_b = clean.copy()
    frame_b[300:340, 600:720] = pat_b
    taps2, logs2 = [], []
    tbl = {"a.png": tpl_a, "b.png": tpl_b}

    def _fake_load(n):
        if n not in tbl:
            raise FileNotFoundError(n)   # 与 vision.load 真实行为一致
        return tbl[n]

    with mock.patch.object(actions, "tap", lambda dev, x, y, **_: taps2.append((x, y))), \
         mock.patch.object(vision, "load", _fake_load):
        tgts = [actions.TapTarget("a.png"), actions.TapTarget("b.png", point=(111, 222))]
        # 谁出现点谁: 帧里只有 a → 点 a 的命中中心
        got = actions.wait_tap_any(_FakeDev([frame_a]), tgts, timeout=1, interval=0.02,
                                   log=logs2.append)
        assert got == {"a.png"}, got
        assert taps2 == [(360, 120)], taps2
        # b 的固定点击点: 命中处不是点击点
        taps2.clear()
        got = actions.wait_tap_any(_FakeDev([clean, frame_b]), tgts, timeout=2,
                                   interval=0.02, log=logs2.append)
        assert got == {"b.png"}, got
        assert taps2 == [(111, 222)], taps2
        # until 集齐即止: 两个都点到才收口, 不等满 timeout
        taps2.clear()
        t0 = time.monotonic()
        got = actions.wait_tap_any(_FakeDev([frame_a, frame_b]), tgts, timeout=30,
                                   interval=0.02, until={"a.png", "b.png"})
        assert got == {"a.png", "b.png"}, got
        assert time.monotonic() - t0 < 5, "until 集齐应提前收口, 不等满 timeout"
        # 超时: 一个都没出现 → 空集合
        taps2.clear()
        got = actions.wait_tap_any(_FakeDev([clean]), tgts, timeout=0.25, interval=0.05)
        assert got == set() and taps2 == [], (got, taps2)
        # stop_event: 置位立即停, 不再点
        taps2.clear()
        ev3 = threading.Event()
        ev3.set()
        got = actions.wait_tap_any(_FakeDev([frame_a]), tgts, timeout=30, interval=0.05,
                                   stop_event=ev3)
        assert got == set() and taps2 == [], (got, taps2)
        # 缺模板的项跳过, 不让整表崩(素材渐进添加)
        got = actions.wait_tap_any(_FakeDev([frame_a]),
                                   [actions.TapTarget("missing.png"), actions.TapTarget("a.png")],
                                   timeout=1, interval=0.02)
        assert got == {"a.png"}, got

    # region 限定: 只在矩形内搜, 返回全屏坐标
    hit_r = vision.find(screen, tpl, region=(300, 100, 420, 140))
    assert hit_r and abs(hit_r[0] - 360) <= 1 and abs(hit_r[1] - 120) <= 1, hit_r
    assert vision.find(screen, tpl, region=(700, 400, 900, 500)) is None  # 矩形外 → 未命中

    # ---- 多点找色(Pt 规范): 矩形范围 / 全点命中 / 变体色 / 反色 / 方向 / 逐通道容差 ----
    canvas = rng.integers(40, 90, (540, 960, 3), dtype=np.uint8)  # 噪底远离特征色
    c1, c2, c3 = (220, 30, 180), (30, 200, 40), (250, 220, 30)
    canvas[200, 300] = c1
    canvas[204, 300] = c2  # 锚点+4行
    canvas[200, 304] = c3  # 锚点+4列
    pts = [vision.Pt(0, 0, c1, 6), vision.Pt(0, 4, c2, 6), vision.Pt(4, 0, c3, 6)]
    assert vision.find_by_points(canvas, (250, 150, 400, 300), pts) == (300, 200)
    assert vision.find_by_points(canvas, (400, 150, 600, 300), pts) is None  # 范围外
    assert vision.find_by_points(canvas, (250, 150, 400, 300), []) is None   # 空点阵
    canvas[204, 300] = (60, 60, 60)  # 打掉伴色点(锚点+4行) → 全点必须命中, 差一个都不行
    assert vision.find_by_points(canvas, (250, 150, 400, 300), pts) is None
    canvas[204, 300] = c3  # 变体色: 基准色 c2 不在, alts 里的动画相位 c3 命中也算
    assert vision.find_by_points(canvas, (250, 150, 400, 300),
                                 [pts[0], vision.Pt(0, 4, c2, 6, alts=(c3,))]) == (300, 200)
    canvas[204, 300] = (30, 210, 60)  # 逐通道容差: 相对 c2 是 G+10 B+20
    assert vision.find_by_points(canvas, (250, 150, 400, 300),
                                 [pts[0], vision.Pt(0, 4, c2, (15, 15, 25))]) == (300, 200)
    assert vision.find_by_points(canvas, (250, 150, 400, 300),
                                 [pts[0], vision.Pt(0, 4, c2, (15, 15, 15))]) is None
    canvas[204, 300] = c2
    # 反色: 伴点"不是特效色"才算命中 — 特效相位落进排除色即失配
    canvas[200, 304] = (80, 80, 80)
    anti_pts = [pts[0], vision.Pt(4, 0, (250, 220, 30), 20, anti=True)]
    assert vision.find_by_points(canvas, (250, 150, 400, 300), anti_pts) == (300, 200)
    canvas[200, 304] = (240, 230, 40)  # 落进 (250,220,30)±20
    assert vision.find_by_points(canvas, (250, 150, 400, 300), anti_pts) is None
    try:
        vision.find_by_points(canvas, (250, 150, 400, 300), pts, direction=9)
        assert False, "direction=9 应 ValueError"
    except ValueError:
        pass
    # 方向: 三个达标候选, 4 个方向各取其所(返回值是 (x,y), 锚点刻意 x≠y 以验证)
    tri = np.full((540, 960, 3), 60, np.uint8)
    for ty, tx in ((100, 120), (100, 500), (300, 500)):
        tri[ty, tx] = c1
        tri[ty + 4, tx] = c2
        tri[ty, tx + 4] = c3
    tri_pts = [vision.Pt(0, 0, c1, 6), vision.Pt(0, 4, c2, 6), vision.Pt(4, 0, c3, 6)]
    fbp = lambda d: vision.find_by_points(tri, (0, 0, 960, 540), tri_pts, direction=d)
    assert fbp(0) == (120, 100) and fbp(2) == (500, 100)
    assert fbp(1) == (500, 300) and fbp(3) == (500, 300)

    # ---- 区域找色族(LZG 移植 + 大漠对应物) ----
    reg = np.full((100, 200, 3), 30, np.uint8)
    reg[10:20, 50:60] = (200, 10, 10)
    reg[30:33, 120:126] = (10, 200, 10)
    found = vision.find_color(reg, (200, 10, 10), tol=5)
    assert found and 50 <= found[0][0] < 60 and 10 <= found[0][1] < 20, found
    multi = vision.find_color(reg, [(200, 10, 10), (10, 200, 10)], tol=5)
    assert len(multi) == 118, len(multi)  # 10x10 + 3x6
    assert vision.count_color(reg, (10, 200, 10), tol=5, region=(100, 0, 200, 100)) == 18
    assert vision.color_at(reg, 55, 15) == (200, 10, 10)  # BGR
    assert vision.cmp_color(reg, 55, 15, (200, 10, 10), tol=5)
    assert not vision.cmp_color(reg, 55, 15, (200, 10, 9), tol=0)
    assert vision.cmp_color(reg, 55, 15, [(0, 0, 0), (200, 10, 10)])  # 列表任一命中
    b, g, r = vision.ave_color(reg, (50, 10, 60, 20))
    assert abs(b - 200) < 2 and abs(g - 10) < 2 and abs(r - 10) < 2, (b, g, r)

    # ---- 卡死判定 ----
    assert vision.is_display_dead(lambda: screen.copy(), timeout=0.7, interval=0.2) is True
    flip = {"n": 0}

    def _alternating():
        flip["n"] += 1
        return screen if flip["n"] % 2 else np.roll(screen, 3, axis=1)

    assert vision.is_display_dead(_alternating, timeout=2.0, interval=0.2) is False

    # ---- 模板目录注入 + 色键(临时目录, 不污染消费项目) ----
    with tempfile.TemporaryDirectory() as td:
        _orig_dir = vision.TEMPLATES_DIR
        vision.set_templates_dir(Path(td))
        try:
            probe = Path(td) / "_key_test.png"
            img = np.full((20, 20, 3), (255, 0, 255), np.uint8)
            img[8:12, 8:12] = rng.integers(0, 255, (4, 4, 3), dtype=np.uint8)  # 非均匀笔画
            probe.write_bytes(cv2.imencode(".png", img)[1].tobytes())
            keep = vision.key_out_background("_key_test.png")
            assert 0 < keep < 0.2, keep
            rgba = vision.load("_key_test.png")
            assert rgba.shape[2] == 4
            screen2 = np.full((540, 960, 3), 60, np.uint8)
            screen2[100:120, 300:320] = img
            hit = vision.find(screen2, rgba, 0.8)
            assert hit and abs(hit[0] - 310) <= 1 and abs(hit[1] - 110) <= 1, hit
            probe.unlink()

            # 「四角即底色」自动键控: 3通道模板≥3/4角同色 → 载入即RGBA
            probe2 = Path(td) / "_autokey_test.png"
            img2 = np.full((20, 60, 3), (255, 128, 128), np.uint8)  # #8080ff 实心底
            img2[6:14, 20:24] = (30, 30, 30)                        # 笔画要有灰度层次
            img2[6:14, 30:34] = (30, 30, 30)
            img2[9:11, 24:30] = (90, 90, 90)
            img2[6:8, 24:30] = (140, 140, 140)
            img2[0, 0] = (200, 200, 200)                            # 污染一个角(多数票应容忍)
            probe2.write_bytes(cv2.imencode(".png", img2)[1].tobytes())
            tpl2 = vision.load("_autokey_test.png")
            assert tpl2.shape[2] == 4, "四角同色模板载入时应自动键控成RGBA"
            screen3 = np.full((540, 960, 3), 90, np.uint8)
            screen3[100:120, 300:360] = img2
            hit2 = vision.find(screen3, tpl2)
            assert hit2 and abs(hit2[0] - 330) <= 1 and abs(hit2[1] - 110) <= 1, hit2
            probe2.unlink()
        finally:
            vision.set_templates_dir(_orig_dir)
            vision._tpl_cache.clear()   # 临时目录的模板别留在缓存里

    # ---- OCR 纯逻辑 ----
    assert best_match([(100, 200, "军团任务发布员"), (300, 100, "军团服务商店")], "军团任务发布员")[:2] == (100, 200)
    assert best_match([(100, 200, "军团任务发布岗")], "军团任务发布员")[2] >= 0.7  # 错1字仍命中
    assert best_match([(100, 200, "军团服务商店")], "军团任务发布员") is None
    assert best_match([(100, 200, "发布员")], "军团任务发布员") is None  # 短词比例不够

    # ---- readout 逐字读数: 放大 + 分数优先 NMS + 按 x 排序 ----
    # (画布与模板各自放大后拼合 —— 整体放大和单独放大边缘插值不同, 会把分数压到阈值线)
    glyph1 = (150 - rng.integers(0, 90, (14, 10))).astype(np.uint8)   # 带纹理的字形
    glyph2 = (150 - rng.integers(0, 90, (14, 12))).astype(np.uint8)
    t1 = enlarge(cv2.cvtColor(glyph1, cv2.COLOR_GRAY2BGR))
    t2 = enlarge(cv2.cvtColor(glyph2, cv2.COLOR_GRAY2BGR))
    big = np.full((160, 480, 3), 240, np.uint8)
    big[40:96, 80:80 + t1.shape[1], :] = t1
    big[40:96, 240:240 + t2.shape[1], :] = t2
    assert read_digits(big, {"1": t1, "2": t2}) == "12"

    # ---- ime 资产在位 ----
    assert ime.ASSET_APK.exists(), "ADBKeyboard.apk 应随库打包在 imgbot/assets/"

    # ---- win32: 拟人轨迹(纯计算) ----
    path = human_path(0, 0, 100, 200, random.Random(1))
    assert path[-1][:2] == (100, 200) and len(path) >= 6
    assert all(dt >= 0 for _x, _y, dt in path)

    # ---- win32: 后台键鼠消息序列(message-only 录制窗口, 不碰任何真窗口) ----
    hwnd, records, pump = create_message_window()
    try:
        gw = GameWindow(hwnd)
        mk = MouseKeyboard(gw, inter_action_delay=0, rng=random.Random(2))
        mk.click(30, 40)
        pump()
        kinds = [m for m, _w, _l in records]
        assert WM_MOUSEMOVE in kinds and WM_LBUTTONDOWN in kinds and WM_LBUTTONUP in kinds
        for m, _w, lp in records:
            if m == WM_LBUTTONDOWN:
                assert lp == (40 << 16) | 30, hex(lp)  # lParam: 高16位y 低16位x
        records.clear()
        mk.type_text("魔A")
        pump()
        chars = [chr(w) for m, w, _l in records if m == WM_CHAR]
        assert chars == list("魔A"), chars
    finally:
        _user32.DestroyWindow(hwnd)

    # ---- win32: 剪贴板粘贴(send_paste: 剪贴板往返 + WM_PASTE 投递) ----
    hwnd2, records2, pump2 = create_message_window()
    try:
        send_paste(GameWindow(hwnd2), "粘贴123")
        pump2()
        assert any(m == WM_PASTE for m, _w, _l in records2)
        assert _user32.OpenClipboard(None)
        try:
            h = _user32.GetClipboardData(CF_UNICODETEXT)
            p = _kernel32.GlobalLock(h)
            got = ctypes.wstring_at(p).split("\x00")[0]
            _kernel32.GlobalUnlock(h)
        finally:
            _user32.CloseClipboard()
        assert got == "粘贴123", got
    finally:
        _user32.DestroyWindow(hwnd2)

    # ---- win32: PrintWindow 截屏链路(纯色窗口 + BGR 序验证) ----
    vh, cleanup = create_visible_window(200, 100, (30, 200, 90))  # RGB(30,200,90)
    try:
        time.sleep(0.3)
        img = None
        for _ in range(3):  # DWM 合成首帧可能黑: 排空重绘后重试
            img = capture_client(GameWindow(vh))
            if int(img.max()) > 0:
                break
            time.sleep(0.25)
        assert img is not None and img.shape[:2] == (100, 200), img.shape if img is not None else None
        assert int(img.max()) > 0, "截图全黑: DWM 未完成合成"
        b, g, r = img[50, 100]
        assert abs(int(b) - 90) <= 6 and abs(int(g) - 200) <= 6 and abs(int(r) - 30) <= 6, (b, g, r)
    finally:
        cleanup()

    # ---- win32: 屏幕截取(前台 BitBlt, 与 PrintWindow 后台互补; 用纯色窗口验色) ----
    # 依赖"该窗口当前可见": 桌面随便开个窗口盖住它就会拍到别的东西 —— 先提 TOPMOST 再拍
    _user32.SetWindowPos.restype = wt.BOOL
    _user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wt.UINT]
    _TOPMOST, _NOTOPMOST, _SWP = -1, -2, 0x0003  # SWP_NOSIZE | SWP_NOMOVE
    vh3, cleanup3 = create_visible_window(120, 60, (30, 200, 90))
    try:
        time.sleep(0.3)
        sx, sy, sw, sh = GameWindow(vh3).client_rect()
        _user32.SetWindowPos(vh3, _TOPMOST, 0, 0, 0, 0, _SWP)
        shot = None
        for _ in range(3):  # 前台合成有竞态, 全黑重试
            shot = capture_screen((sx, sy, sw, sh))
            if int(shot.max()) > 0:
                break
            time.sleep(0.25)
        assert shot.shape[:2] == (sh, sw), shot.shape
        assert int(shot.max()) > 0, "屏幕截取全黑: 前台合成未就绪"
        b, g, r = shot[sh // 2, sw // 2]
        assert abs(int(b) - 90) <= 6 and abs(int(g) - 200) <= 6 and abs(int(r) - 30) <= 6, (b, g, r)
        vx, vy, vw, vht = virtual_screen_rect()
        assert capture_screen().shape[:2] == (vht, vw), "全虚拟屏幕截取尺寸与 virtual_screen_rect 不符"
        assert any(t == "imgbot-selfcheck" for _h, _c, t in list_windows()), \
            "刚创建的可见窗口应出现在 list_windows 里"
    finally:
        _user32.SetWindowPos(vh3, _NOTOPMOST, 0, 0, 0, 0, _SWP)
        cleanup3()

    # ---- win32: 拾取高亮(xor_frame XOR 自逆: 画+擦往返不残留)与 window_rect ----
    vh4, cleanup4 = create_visible_window(120, 60, (30, 200, 90))
    try:
        l, t, r, b = GameWindow(vh4).window_rect()
        assert r > l and b > t, ((l, t, r, b),)
        xor_frame((l, t, r, b))
        xor_frame((l, t, r, b))  # 第二次应还原屏幕(自逆), 不炸即通过
        assert GameWindow(vh4).pid() > 0, "窗口应归属一个活着的进程"
        assert GameWindow(vh4).process_path().endswith(".exe"), "应取到本进程可执行路径"
        hbtn = _user32.CreateWindowExW(0, "BUTTON", "子按钮", 0x50000000,  # WS_CHILD|WS_VISIBLE
                                       10, 10, 50, 20, vh4, None, None, None)
        assert hbtn, "创建子按钮失败"
        sub = GameWindow.find(class_name="BUTTON", parent=vh4)
        assert sub is not None and sub.hwnd == hbtn, "find(parent=) 应在子孙里按类名找到子窗口"
    finally:
        cleanup4()

    # ---- win32: 伪最小化停靠往返(状态位必须跟着 park 走, 曾恒为 False) ----
    vh2, cleanup2 = create_visible_window(200, 100, (30, 200, 90))
    try:
        gw = GameWindow(vh2)
        assert gw.is_pseudo_minimized is False
        gw.park_offscreen(True)
        assert gw.is_pseudo_minimized is True, "停靠后应报告伪最小化"
        gw.park_offscreen(False)
        assert gw.is_pseudo_minimized is False
    finally:
        cleanup2()

    _check_readme_api()
    _check_no_swallowed_code()

    print("imgbot selfcheck ok")


def _check_no_swallowed_code():
    """防回归: 注释行里不能藏着被挤到同一行的代码(曾两次把赋值写进注释尾, 运行时才炸)."""
    for p in Path(__file__).resolve().parent.rglob("*.py"):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if not s.startswith("#"):
                continue
            body = s.lstrip("#").strip()
            # 注释正文里以赋值/调用开头 = 代码被吞进注释
            if re.match(r"^[A-Za-z_][\w.]*\s*(=|\(|\[)", body):
                raise AssertionError(f"{p.name}:{i} 代码被挤进注释: {s}")


def _check_readme_api():
    """README 能力清单的 API 必须真实存在(防文档漂移: 清单是给别的 AI 查用的, 写了没有的
    API 比不写更糟)。README 在仓库根, 源码在 imgbot/。"""
    import ctypes as _ct
    import inspect
    readme = Path(__file__).resolve().parent.parent / "README.md"
    if not readme.exists():          # 装成 wheel 后 README 不在包里, 跳过
        return
    text = readme.read_text(encoding="utf-8")
    from . import actions, adb, ime, ocr, readout, target, vision
    from .win32 import capture
    from .win32 import input as win_input
    from .win32 import window
    modules = [vision, ocr, readout, actions, adb, ime, target,
               window, capture, win_input]
    # `模块.名` 形式(可带不带括号): 名必须真实可取(含 win32 子模块的写法)
    # 模块名按 README 里的简称匹配: vision/ocr/readout/actions/adb/ime/target + win32 三件
    by_name = {m.__name__.rsplit(".", 1)[-1]: m for m in modules}
    submodules = {"capture": capture, "input": win_input, "window": window}
    # 类名也算合法前缀(如 `Target.screenshot` 指的是 target 模块的类)
    class_names = {n for mod in modules for n, o in vars(mod).items() if inspect.isclass(o)}
    for m, f in set(re.findall(r"`([A-Za-z_][A-Za-z_0-9]*)\.([A-Za-z_][A-Za-z_0-9]*)`", text)):
        if f.endswith(".py"):
            continue
        mod = submodules.get(m) or by_name.get(m)
        if mod is None:
            if m == "imgbot" or m in class_names:
                continue      # 包路径 / 类名引用
            raise AssertionError(f"README 引用了未知模块: {m}")
        if not hasattr(mod, f):
            raise AssertionError(f"README 引用了不存在的 API: {m}.{f}")
    # 裸函数名写法 `name(` 也必须真实存在(清单里大量是裸名)
    # 只认**模块级**函数/类 —— 类的方法名(如 screenshot/tap)也是合法裸名, 不在此列
    known = set()
    for mod in modules:
        known |= {n for n, o in vars(mod).items()
                  if inspect.isfunction(o) or inspect.isclass(o)}
    for mod in modules:
        for cls in [o for o in vars(mod).values() if inspect.isclass(o)]:
            known |= set(vars(cls).keys())     # 类的方法/属性名也算已知
    for f in set(re.findall(r"`([A-Za-z_][A-Za-z_0-9]*)\(", text)):
        if f not in known:
            raise AssertionError(f"README 引用了不存在的函数: {f}")
    # 各模块**自己定义**的公开名(函数/类)都应出现在 README 的能力清单段内
    # (只查清单段: 其它章节提到名字不算数, 否则"漏列"检不出来)
    start = text.find("## 能力清单")
    end = text.find("## 色键模板约定")
    assert start >= 0 and end > start, "README 缺能力清单段"
    catalog = text[start:end]
    for mod in modules:
        for name, obj in vars(mod).items():
            if name.startswith("_") or not (inspect.isfunction(obj) or inspect.isclass(obj)):
                continue
            if getattr(obj, "__module__", "") != mod.__name__:
                continue          # 别的模块导入进来的, 不算本模块的 API
            if inspect.isclass(obj) and issubclass(obj, _ct.Structure):
                continue          # ctypes 结构体是内部实现细节, 不进能力清单
            if name not in catalog:
                raise AssertionError(f"README 能力清单漏了: {mod.__name__}.{name}")


if __name__ == "__main__":
    main()
