"""库级自检(无真机/无目标窗口): uv run python -m imgbot.selfcheck

覆盖: adb 解析 / vision 全套(模板/键控/多点找色/区域找色族/卡死判定) /
ocr 纯逻辑 / readout 逐字读数 / win32 后台键鼠+截图(离线窗口).
"""
import ctypes
import ctypes.wintypes as wt
import random
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

from . import adb as adb_mod
from . import ime, vision
from .ocr import best_match
from .readout import enlarge, read_digits
from .win32.capture import capture_client
from .win32.input import (CF_UNICODETEXT, WM_CHAR, WM_LBUTTONDOWN, WM_LBUTTONUP,
                          WM_MOUSEMOVE, WM_PASTE, human_path, send_paste)
from .win32.input import MouseKeyboard
from .win32.window import GameWindow

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


def create_message_window():
    """message-only 窗口, WndProc 记录到达的消息(LZG_PMH tests/_winhelper 思路).
    返回 (hwnd, records, pump) — pump() 处理队列后返回."""
    records = []
    seq = create_message_window._n = getattr(create_message_window, "_n", 0) + 1

    @WNDPROC
    def wndproc(hwnd, msg, wp, lp):
        records.append((int(msg), int(wp), int(lp)))
        return _user32.DefWindowProcW(hwnd, msg, wp, lp)

    hinst = _kernel32.GetModuleHandleW(None)
    cls = "ImgbotMsg%d" % seq
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = wndproc
    wc.hInstance = hinst
    wc.lpszClassName = cls
    if not _user32.RegisterClassExW(ctypes.byref(wc)):
        raise OSError("RegisterClassExW 失败: %d" % ctypes.get_last_error())
    hwnd = _user32.CreateWindowExW(0, cls, "", 0, 0, 0, 0, 0, HWND_MESSAGE, None, ctypes.c_void_p(hinst), None)
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
    hinst = _kernel32.GetModuleHandleW(None)
    cls = "ImgbotVis%d" % seq
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = wndproc
    wc.hInstance = hinst
    wc.hbrBackground = brush
    wc.lpszClassName = cls
    if not _user32.RegisterClassExW(ctypes.byref(wc)):
        raise OSError("RegisterClassExW 失败: %d" % ctypes.get_last_error())
    hwnd = _user32.CreateWindowExW(
        0, cls, "imgbot-selfcheck", 0x10CF0000,  # WS_OVERLAPPEDWINDOW
        100, 100, width + 16, height + 39, None, None, ctypes.c_void_p(hinst), None)
    _user32.ShowWindow(hwnd, 1)
    _user32.UpdateWindow(hwnd)

    def cleanup():
        _user32.DestroyWindow(hwnd)
        _user32.UnregisterClassW(cls, ctypes.c_void_p(hinst))
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

    _orig_load = vision.load
    vision.load = _fake_load
    try:
        hit = vision.find_any(screen, ["a.png", "b.png"])
        assert hit and abs(hit[0] - 360) <= 1 and abs(hit[1] - 120) <= 1 and hit[2] > 0.99
        assert vision.find_any(screen, ["b.png", "c.png"]) is None  # 全缺失 → None
    finally:
        vision.load = _orig_load
    same = rng.integers(0, 255, (540, 960, 3), dtype=np.uint8)
    assert not vision.changed(same, same.copy())
    assert vision.changed(same, rng.integers(0, 255, (540, 960, 3), dtype=np.uint8))

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

    print("imgbot selfcheck ok")


if __name__ == "__main__":
    main()
