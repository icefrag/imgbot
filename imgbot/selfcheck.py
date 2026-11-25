"""库级自检(无真机/无目标窗口): uv run python -m imgbot.selfcheck [层名[,层名...]]

按层选择回归(AGENTS 口径"改哪层跑哪层"; 不带参数仍全量):
  adb           adb.py(设备解析/resolve_adb) + ime 资产
  vision        vision.py(模板/键控/多点找色/区域找色族/卡死判定/raw 帧)
  actions       actions.py(等待点击族 wait_tap/wait_tap_any)
  ocr           ocr.py+readout.py(纯逻辑/并发闸/逐字读数)
  ops           ops.py(帧源 seam 全策略)
  nav           nav.py(界面导航图)
  win32_input   win32/input.py(拟人轨迹/消息序列/剪贴板/LockInput)
  win32_capture win32/capture.py(PrintWindow/屏幕截取)
  win32_window  win32/window.py(窗口族/停靠/枚举/DACL)
  docs          文档 lint(api.md 引用真实存在 + 注释无吞码)
"win32" = win32_input,win32_window,win32_capture 合选。
dmcore/host 协议改动例外: selfcheck 全量 + dx 双烟测(两端强耦合)。
"""
import ctypes
import ctypes.wintypes as wt
import random
import re
import struct
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from . import actions, adb as adb_mod, ocr as ocr_mod
from . import ime, nav as nav_mod, ops as ops_mod, vision
from .dmsoft import DmSoft
from .ocr import best_match
from .ops import FrameSource, Ops
from .readout import enlarge, read_digits
from .target import AdbTarget
from .win32.capture import capture_client, capture_client_gdi2, capture_screen, virtual_screen_rect
from .win32.input import (CF_UNICODETEXT, WM_CHAR, WM_KEYDOWN, WM_KEYUP,
                          WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE, WM_PASTE,
                          MouseKeyboard, human_path, send_ime_string, send_paste)
from .win32.window import (GameWindow, enum_windows_dm, list_windows,
                           pids_of_process, set_window_state,
                           unlock_process_dacl, xor_frame)

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
        if msg == 0x0318:  # WM_PRINTCLIENT(gdi2 截图路靶): 自绘客户区 —— GDI 应用标准行为
            rc = wt.RECT()
            _user32.GetClientRect(hwnd, ctypes.byref(rc))
            _user32.FillRect(wt.HDC(wp), ctypes.byref(rc), wc.hbrBackground)
            return 1
        return _user32.DefWindowProcW(hwnd, msg, wp, lp)

    create_visible_window._keep = wndproc     # 防 WNDPROC 回调被 GC 后窗口崩溃
    hinst, wc = _register_class("ImgbotVis%d" % seq, wndproc, brush)
    # 外框尺寸用 AdjustWindowRectEx 按真实非客户区反推(固定 +16/+39 只在 100% DPI
    # 成立; dpi 感知进程在 200% 屏上 NC 区按 DPI 缩放, 客户区会偏离断言值)
    _rc = wt.RECT(0, 0, width, height)
    _user32.AdjustWindowRectEx(ctypes.byref(_rc), 0x10CF0000, False, 0)  # WS_OVERLAPPEDWINDOW
    hwnd = _user32.CreateWindowExW(
        0, wc.lpszClassName, "imgbot-selfcheck", 0x10CF0000,
        100, 100, _rc.right - _rc.left, _rc.bottom - _rc.top,
        None, None, ctypes.c_void_p(hinst), None)
    _user32.ShowWindow(hwnd, 1)
    _user32.UpdateWindow(hwnd)

    def cleanup():
        _user32.DestroyWindow(hwnd)
        _user32.UnregisterClassW(wc.lpszClassName, ctypes.c_void_p(hinst))
        _gdi32.DeleteObject(brush)

    return hwnd, cleanup


def fake_png(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big")


def _vision_fixtures():
    """确定性噪声帧 + 从中裁出的模板(各层独立重建, 单层可重跑)。"""
    rng = np.random.default_rng(7)
    screen = rng.integers(0, 255, (540, 960, 3), dtype=np.uint8)
    patch = screen[100:140, 300:420].copy()
    return rng, screen, cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)


def check_adb():
    """adb 层: png_size/parse_devices/resolve_adb + ime 资产 + 帧源 adapter
    (AdbTarget 四件齐, Ops 直喂)。"""
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
    # resolve_adb: 裸名先找宿主 exe 旁(产品把 adb.exe 摆 exe 目录, 用户机免装
    # platform-tools), 找不到退 PATH 裸名; 显式路径直通(2026-11-03 用户侧
    # FileNotFoundError 案防回归)
    _ad = Path(tempfile.mkdtemp())
    _explicit = str(_ad / "adb.exe")
    assert adb_mod.resolve_adb(_explicit) == _explicit
    with mock.patch.object(adb_mod.sys, "executable", str(_ad / "host.exe")):
        assert adb_mod.resolve_adb("adb") == "adb"
        (_ad / "adb.exe").write_bytes(b"")
        assert adb_mod.resolve_adb("adb") == str(_ad / "adb.exe")

    # ---- ime 资产在位 ----
    assert ime.ASSET_APK.exists(), "ADBKeyboard.apk 应随库打包在 imgbot/assets/"

    # ---- 帧源 adapter: AdbTarget 四件齐, 直接 Ops(AdbTarget(...)) ----
    class _FakeAdcDev:
        """AdbDevice 替身: 固定帧 PNG + 点击/滑动记账(免真机)。"""
        def __init__(self, frame):
            self._frame = frame
            self.taps, self.swipes = [], []

        def screenshot(self) -> bytes:
            return cv2.imencode(".png", self._frame)[1].tobytes()

        def tap(self, x, y):
            self.taps.append((x, y))

        def swipe(self, x1, y1, x2, y2, ms=300):
            self.swipes.append((x1, y1, x2, y2, ms))

    _rng, _ascr, _atpl = _vision_fixtures()
    t = AdbTarget("emulator-5554")           # 构造不连设备(adb.py 只存 serial)
    t.device = _FakeAdcDev(_ascr)
    assert isinstance(t, FrameSource), "AdbTarget 应结构化满足帧源协议"
    assert Ops(t).find_tap(_atpl, timeout=2, interval=0.05) is True
    assert len(t.device.taps) == 1, t.device.taps
    assert abs(t.device.taps[0][0] - 360) <= 3 and abs(t.device.taps[0][1] - 120) <= 3, \
        t.device.taps                        # 拟人 ±3px 抖动落在模板中心附近
    assert t.GetClientSize(0) == (960, 540), "GetClientSize 应从帧取设备分辨率"
    t.keymouse.drag(100, 200, 300, 400, steps=12, interval=0.03)
    assert len(t.device.swipes) == 1, t.device.swipes
    s = t.device.swipes[0]
    # 拟人 ±3px: 两端各自抖动; 时长=steps*interval 拍(ms=360), 下限 150ms 防瞬移
    assert all(abs(a - b) <= 3 for a, b in zip(s[:4], (100, 200, 300, 400))), s
    assert s[4] >= 150, s


def check_adb_session():
    """常驻会话层(ADR-0002): 协议应答清点/回落状态机/生命周期 —— 假会话桩全离线。"""
    from . import adb_session as sess_mod
    from .adb_session import (AdbSession, AdbSessionError, SessionDevice,
                              set_log as sess_set_log)

    # ---- 协议应答清点: 异步积压按发送序由同步指令顺带清掉 ----
    class _FakeChan:
        def __init__(self, lines):
            self.lines, self.sent, self.closed = list(lines), [], False

        @property
        def alive(self):
            return not self.closed

        def send_line(self, line):
            self.sent.append(line)

        def read_line(self, timeout):
            if not self.lines:
                raise AdbSessionError("应答超时(假桩耗尽)")
            return self.lines.pop(0)

        def close(self):
            self.closed = True

    dev = adb_mod.AdbDevice("emulator-5554")     # 构造不连设备
    s = AdbSession(dev)
    s._chan = _FakeChan(["OK tap", "OK tap", "OK ping sdk28"])
    assert s.cmd("tap 1 2", sync=False) == ""    # 异步: 发后不等, pending 记账
    assert s.cmd("tap 3 4", sync=False) == ""
    assert s.cmd("ping") == "OK ping sdk28"      # 同步: 先清 2 条积压再取本指令应答
    assert s._chan.sent == ["tap 1 2", "tap 3 4", "ping"], s._chan.sent
    # 积压里的 ERR 只记日志不占错误位; 本指令 ERR 才抛
    s._chan = _FakeChan(["ERR tap", "OK ping sdk28"])
    s.cmd("tap 1 2", sync=False)
    assert s.cmd("ping") == "OK ping sdk28"
    s._chan = _FakeChan(["ERR ping"])
    try:
        s.cmd("ping")
        raise AssertionError("同步 ERR 应抛 AdbSessionError")
    except AdbSessionError:
        pass

    # ---- 回落状态机: 假会话工厂(建立失败→永久回落; 中断→重建一次重发原指令) ----
    warns = []
    sess_set_log(lambda msg, level="warn": warns.append(msg))

    recorded = []

    class _FakeSess:
        inst = []

        def __init__(self, device):
            self.started, self.sent, self.alive_flag = 0, [], True
            self.fail_start = getattr(type(self), "fail_start", False)
            type(self).inst.append(self)

        @property
        def alive(self):
            return self.alive_flag

        def start(self):
            self.started += 1
            if self.fail_start:
                raise RuntimeError("驻留件起不来")

        def cmd(self, line, timeout=5.0, sync=True):
            self.sent.append((line, sync))
            if line == "tap 9 9" and len(self.sent) == 1 and getattr(type(self), "die_once", False):
                type(self).die_once = False
                self.alive_flag = False
                raise RuntimeError("EOF")
            return ""

        def close(self):
            self.alive_flag = False

    dev2 = SessionDevice("emulator-5554")
    assert isinstance(dev2, adb_mod.AdbDevice), "SessionDevice 应是 AdbDevice 子类(截图/shell 原样继承)"
    dev2._run = lambda *a, **k: recorded.append(a) or b""   # 拦截 shell 路基类调用(回落记账)
    _FakeSess.inst = []
    _FakeSess.fail_start = False
    _FakeSess.die_once = True
    dev2._factory = _FakeSess

    assert dev2.tap(1, 2) is None and not recorded, "正常路径应走会话, 不碰 shell 路"
    fs = _FakeSess.inst[-1]
    assert fs.sent == [("tap 1 2", False)], fs.sent          # tap 发后不等(sync=False)
    assert dev2._send("ping") == ""                          # 同步指令走会话
    assert _FakeSess.inst[-1].sent[-1] == ("ping", True)

    # 中断→重建一次, 重发原指令( die_once 在首个会话首条 tap 上引爆 )
    dev3 = SessionDevice("emulator-5554")
    dev3._run = lambda *a, **k: recorded.append(a) or b""
    _FakeSess.inst = []
    _FakeSess.fail_start = False
    _FakeSess.die_once = True
    dev3._factory = _FakeSess
    dev3.tap(9, 9)
    assert not dev3._locked_out and len(_FakeSess.inst) == 2, \
        (dev3._locked_out, len(_FakeSess.inst))
    assert _FakeSess.inst[0].sent == [("tap 9 9", False)]
    assert _FakeSess.inst[1].sent == [("tap 9 9", False)], "重建会话应重发原指令"
    assert any("重建" in w for w in warns), warns

    # 建立失败→永久回落 shell 路(之后不再碰会话)
    dev4 = SessionDevice("emulator-5554")
    dev4._run = lambda *a, **k: recorded.append(a) or b""
    _FakeSess.inst = []
    _FakeSess.fail_start = True
    _FakeSess.die_once = False
    dev4._factory = _FakeSess
    dev4.tap(5, 5)                                    # 建立失败 → 回落, 基类 tap 走 shell 路
    assert dev4._locked_out is True
    assert any(a[:2] == ("shell", "input") for a in recorded), recorded[-2:]
    n_inst = len(_FakeSess.inst)
    dev4.tap(6, 6)
    assert len(_FakeSess.inst) == n_inst, "回落后不应再碰会话"
    assert any("回落" in w for w in warns), warns

    # ---- AdbTarget 通道面: session 构造/非法通道/close 收口 ----
    t5 = AdbTarget("emulator-5554", input="session")   # 构造不连设备(会话懒建立)
    assert isinstance(t5.device, SessionDevice)
    t5.close()                                         # 未建立过会话的 close 不炸
    try:
        AdbTarget("emulator-5554", input="bogus")
        raise AssertionError("非法通道应 NotImplementedError")
    except NotImplementedError:
        pass


def check_vision():
    """图色层: 模板/region/多点找色/区域找色族/卡死判定/raw 帧/模板目录注入+色键。"""
    # ---- 模板匹配 ----
    rng, screen, tpl = _vision_fixtures()
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

    # ---- decode_framebuffer: screencap 原始帧(2026-11-07 CPU案) ----
    fb = rng.integers(0, 255, (54, 96, 4), dtype=np.uint8)  # h=54, w=96
    _hdr = struct.pack("<iii", 96, 54, 1)                   # RGBA_8888, 12字节头(Android 9-)
    _got = vision.decode_framebuffer(_hdr + fb.tobytes())
    assert _got.shape == (54, 96, 3) and (_got == cv2.cvtColor(fb, cv2.COLOR_RGBA2BGR)).all()
    _got = vision.decode_framebuffer(_hdr + fb.tobytes() + b"\x00" * 4)  # 16字节头(11+ colorspace)
    assert _got.shape == (54, 96, 3)
    for _bad in (struct.pack("<iii", 96, 54, 7) + fb.tobytes(),    # fmt 非4字节/像素
                 struct.pack("<iii", 96, 54, 1) + fb.tobytes()[:-1],  # 总长与 w*h*4 不符
                 b"", b"garbage"):
        try:
            vision.decode_framebuffer(_bad)
            raise AssertionError(f"坏帧头不该解码成功: {_bad[:16]!r}")
        except ValueError:
            pass

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

    # ---- exclude 排除区(大漠 SetExcludeRegion 对应物, 工单 damo-bg-capture/06) ----
    assert vision.find(screen, tpl, exclude=(350, 110, 380, 140)) is None   # 命中点落区内
    assert vision.find(screen, tpl, exclude=(0, 0, 100, 100)) is not None   # 区外无碍
    assert [h[0] for h in vision.find_all(row, icon, 0.99,
                                          exclude=(100, 0, 200, 200))] == [58, 258]
    assert vision.find_color(reg, (10, 200, 10), tol=5,
                             exclude=(115, 25, 131, 38)) == []              # 3x6 块全区内
    n_all = vision.count_color(reg, (10, 200, 10), tol=5)
    n_left = vision.count_color(reg, (10, 200, 10), tol=5, exclude=(118, 29, 124, 33))
    assert 0 < n_left < n_all, (n_all, n_left)
    assert vision.find_by_points(tri, (0, 0, 300, 300), tri_pts,
                                 exclude=(115, 102, 140, 120)) is None      # 伴点落区=无效(唯一候选被否决)
    assert vision.find_by_points(tri, (0, 0, 960, 540), tri_pts,
                                 exclude=(100, 90, 140, 110)) == (500, 100)  # 锚落区丢弃, 递补下一候选
    assert vision.find_by_points(tri, (0, 0, 960, 540), tri_pts,
                                 exclude=(400, 400, 500, 500)) == (120, 100)  # 区外无碍

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

    # ---- wait_find 停止优先(vision 级语义): 已叫停时即使模板在屏上也不该返回命中 ----
    ev2 = threading.Event()
    ev2.set()
    assert vision.wait_find(lambda: screen.copy(), tpl, timeout=5, interval=0.05,
                            stop_event=ev2) is None
    assert vision.wait_find(lambda: screen.copy(), tpl, timeout=5,
                            interval=0.05) is not None  # 无 stop_event 时正常命中


def check_actions():
    """拟人点击等待族: wait_tap/wait_tap_any(假帧序列注入, 停止优先)。"""
    class _FakeDev:  # AdbDevice 替身: screenshot() 吐 PNG bytes, 帧耗尽后停最后一帧
        def __init__(self, frames: list[np.ndarray]):
            self.frames, self.i = frames, 0

        def screenshot(self) -> bytes:
            img = self.frames[min(self.i, len(self.frames) - 1)]
            self.i += 1
            return cv2.imencode(".png", img)[1].tobytes()

    rng, screen, tpl = _vision_fixtures()

    # ---- wait_tap: 轮询出现即点(假帧序列注入; 命中/超时/停止三分支) ----
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


def check_ocr():
    """OCR/readout 层: 纯逻辑 + 慢引擎并发闸 + 逐字读数。"""
    # ---- OCR 纯逻辑 ----
    assert best_match([(100, 200, "军团任务发布员"), (300, 100, "军团服务商店")], "军团任务发布员")[:2] == (100, 200)
    assert best_match([(100, 200, "军团任务发布岗")], "军团任务发布员")[2] >= 0.7  # 错1字仍命中
    assert best_match([(100, 200, "军团服务商店")], "军团任务发布员") is None
    assert best_match([(100, 200, "发布员")], "军团任务发布员") is None  # 短词比例不够

    # ---- 慢引擎并发闸(2026-11-07 客户机5开CPU案): engine() 返回代理, 进程级最多
    # OCR_MAX_CONCURRENT 发同推; 预播种假引擎免加载真模型 ----
    _lk, _cur, _max, _done = threading.Lock(), [0], [0], []

    def _fake_raw(_img):
        with _lk:
            _cur[0] += 1
            _max[0] = max(_max[0], _cur[0])
        time.sleep(0.15)
        with _lk:
            _cur[0] -= 1
        return "r"

    # 桩只播**裸**假引擎: 闸只有 _SelfHeal 一层(2026-11-25 修, 旧桩再包一层闸 → 同线程
    # 对同一信号量取两次, 闸=1 必死锁 —— 实测 join 超时 _done=[], 默认闸宽下只是碰巧
    # 没撞上) —— 所以两种闸宽各跑一遍: 默认宽验"不超闸", 闸=1 验"不双重取闸"
    _seed, _sem_seed = ocr_mod._engines, ocr_mod._sem_slow
    try:
        for _cap in (ocr_mod.OCR_MAX_CONCURRENT, 1):
            _cur[0] = _max[0] = 0
            _done.clear()
            ocr_mod._engines = {False: _fake_raw}
            ocr_mod._sem_slow = threading.Semaphore(_cap)
            _ths = [threading.Thread(daemon=True,
                                     target=lambda: _done.append(ocr_mod.engine(False)(None)))
                    for _ in range(6)]
            for t in _ths:
                t.start()
            for t in _ths:
                t.join(5)
            assert _done == ["r"] * 6, (_cap, _done)       # 6发全跑完
            assert 1 <= _max[0] <= _cap, (_cap, _max)       # 同时在推不超闸
    finally:
        ocr_mod._engines, ocr_mod._sem_slow = _seed, _sem_seed

    # ---- readout 逐字读数: 放大 + 分数优先 NMS + 按 x 排序 ----
    # (画布与模板各自放大后拼合 —— 整体放大和单独放大边缘插值不同, 会把分数压到阈值线)
    rng = np.random.default_rng(7)
    glyph1 = (150 - rng.integers(0, 90, (14, 10))).astype(np.uint8)   # 带纹理的字形
    glyph2 = (150 - rng.integers(0, 90, (14, 12))).astype(np.uint8)
    t1 = enlarge(cv2.cvtColor(glyph1, cv2.COLOR_GRAY2BGR))
    t2 = enlarge(cv2.cvtColor(glyph2, cv2.COLOR_GRAY2BGR))
    big = np.full((160, 480, 3), 240, np.uint8)
    big[40:96, 80:80 + t1.shape[1], :] = t1
    big[40:96, 240:240 + t2.shape[1], :] = t2
    assert read_digits(big, {"1": t1, "2": t2}) == "12"


def check_ops():
    """Ops 门面: 帧源 seam 假 adapter 离线覆盖等待/冻结/停止/找点全策略。"""
    rng, screen, tpl = _vision_fixtures()

    class _FakeDm:
        """FrameSource 假 adapter: 逐帧吐(耗尽停末张), 点击/拖拽只记账。"""
        def __init__(self, frames):
            self.frames, self.i = list(frames), 0
            self.screen_calls = 0
            self.clicks, self.drags = [], []

        def screen(self):
            self.screen_calls += 1
            img = self.frames[min(self.i, len(self.frames) - 1)]
            self.i += 1
            return img

        @property
        def keymouse(self):
            return self                       # click/drag 即本对象

        def click(self, x, y, settle=None):
            self.clicks.append((x, y))

        def drag(self, x1, y1, x2, y2, **_):
            self.drags.append((x1, y1, x2, y2))

        def GetBindWindow(self):
            return 4321

        def GetClientSize(self, hwnd):
            return 960, 540

    assert isinstance(DmSoft(), FrameSource), "DmSoft 应结构化满足帧源协议"
    blank = np.zeros((540, 960, 3), np.uint8)
    assert isinstance(_FakeDm([screen]), FrameSource)

    hit = Ops(_FakeDm([screen])).wait_pic(tpl, timeout=2, interval=0.02)
    assert hit and abs(hit[0] - 360) <= 1 and abs(hit[1] - 120) <= 1, hit
    with tempfile.TemporaryDirectory() as td:   # 超时安静返回 + 留痕(shot 落临时目录)
        _orig_shot_dir = ops_mod.SHOT_DIR
        ops_mod.SHOT_DIR = Path(td)
        try:
            assert Ops(_FakeDm([blank])).wait_pic(tpl, timeout=0.15,
                                                  interval=0.05) is None
            assert len(list(Path(td).glob("*.png"))) == 1, "超时应自动留痕一张"
        finally:
            ops_mod.SHOT_DIR = _orig_shot_dir
    ev4 = threading.Event()
    ev4.set()
    fo_stop = _FakeDm([screen])
    assert Ops(fo_stop).wait_pic(tpl, timeout=5, interval=0.05, stop=ev4) is None
    assert fo_stop.screen_calls == 1, \
        "停止优先: 轮询一轮都不跑, 只剩超时留痕的那一帧截图"

    # same_frame: 窗内 N 查共用一帧, 出窗各自现截; 等待/verify 进窗即拒
    fo_fr = _FakeDm([screen, screen])
    ops_fr = Ops(fo_fr)
    with ops_fr.same_frame():
        assert ops_fr.exists(tpl) and ops_fr.find_all(tpl)
        assert fo_fr.screen_calls == 1, "窗内多查应只截一帧"
        try:
            ops_fr.wait_pic(tpl, timeout=0.1)
            raise AssertionError("冻结窗里等待应拒绝")
        except RuntimeError:
            pass
        try:
            ops_fr.find_tap(tpl, verify=tpl)
            raise AssertionError("冻结窗里 verify 应拒绝")
        except RuntimeError:
            pass
    ops_fr.exists(tpl)
    assert fo_fr.screen_calls == 2, "出窗后查询应各自现截"

    # find_tap: 点击记账 + verify 二次确认; 停止贯穿验证阶段
    pat_v = np.zeros((40, 120, 3), np.uint8)
    pat_v[:, ::3] = 250
    tpl_v = cv2.cvtColor(pat_v, cv2.COLOR_BGR2GRAY)
    frame_v = blank.copy()
    frame_v[200:240, 500:620] = pat_v
    fo_tap = _FakeDm([screen, frame_v])
    assert Ops(fo_tap).find_tap(tpl, timeout=1, interval=0.02, verify=tpl_v) is True
    assert fo_tap.clicks == [(360, 120)], fo_tap.clicks
    fo_tap2 = _FakeDm([screen, frame_v])
    assert Ops(fo_tap2).find_tap(tpl, timeout=1, interval=0.02, verify=tpl_v,
                                 stop=lambda: len(fo_tap2.clicks) >= 1) is False, \
        "点击后叫停: verify 阶段应立即放弃"

    # tap_until_gone: 点一下图消失即收手
    fo_gone = _FakeDm([screen, screen, blank])
    assert Ops(fo_gone).tap_until_gone(tpl, timeout=3, interval=0.02) == 1
    assert fo_gone.clicks == [(360, 120)], fo_gone.clicks

    # swipe_find: 首屏命中不拖; 拖后画面不变 = 到底
    fo_sw = _FakeDm([screen])
    assert Ops(fo_sw).swipe_find(tpl, "up", timeout=1) == (360, 120)
    assert fo_sw.drags == [], "首屏命中不应拖"
    fo_sw2 = _FakeDm([blank])
    assert Ops(fo_sw2).swipe_find(tpl, "up", timeout=2) is None
    assert len(fo_sw2.drags) == 1, fo_sw2.drags

    # wait_color / wait_stable / wait_change
    frame_c = blank.copy()
    frame_c[300:310, 400:410] = (0, 200, 0)                    # BGR 绿(#00C800)
    assert Ops(_FakeDm([blank, frame_c])).wait_color(
        "00C800", timeout=1, interval=0.02) == (400, 300)
    fa = np.full((540, 960, 3), 10, np.uint8)
    fb = np.full((540, 960, 3), 20, np.uint8)
    assert Ops(_FakeDm([fa, fb, fb, fb])).wait_stable(
        None, timeout=2, interval=0.02, frames=3) is True
    assert Ops(_FakeDm([fa, fb] * 20)).wait_stable(
        None, timeout=0.2, interval=0.05, frames=3) is False
    assert Ops(_FakeDm([fa, fa, fb])).wait_change(
        None, timeout=1, interval=0.02) is True


def check_nav():
    """nav 层: 界面导航图(定位两层/BFS/自愈/迷路/打断, 离线 FakeOps)。"""
    class FakeOps:
        """锚点名当"画面": state=当前屏, popups=屏上挂着的弹窗。"""
        def __init__(self):
            self.state, self.popups, self.tapped = "a_city", [], []
        @contextmanager
        def same_frame(self):
            yield self
        def exists(self, pic, region=None, threshold=None):
            return pic == self.state or pic in self.popups
        def wait_pic(self, pic, region=None, timeout=30, interval=0.4,
                     threshold=None, stop=None):
            return (0, 0, 1.0) if self.exists(pic) else None
        def probe(self, *pics, region=None, threshold=None):
            return next((p for p in pics if p in self.popups), None)
        def find_tap(self, pic, region=None, timeout=0, interval=0.4,
                     threshold=None, verify=None, stop=None):
            if pic in self.popups:
                self.popups.remove(pic)
                self.tapped.append(pic)
                return True
            return False
        def shot(self, tag=""):
            return Path("logs/shots") / f"{tag}.png"

    def to(fake, target):                      # 造边动作: 落到 target 屏
        def act():
            fake.state = target
            return True
        return act

    fo = FakeOps()
    nav = nav_mod.Nav(fo)
    nav.screen("city", "a_city")
    nav.screen("menu", "a_menu")
    nav.screen("dg", "a_dg")
    nav.edge("city", "menu", to(fo, "a_menu"))
    nav.edge("menu", "dg", to(fo, "a_dg"))
    nav.edge("dg", "city", to(fo, "a_city"))
    nav.on_interrupt(["a_popup"])
    fo.popups.append("a_popup")
    assert nav.go("dg") is True and nav.cur == "dg", "基础 BFS 带路+打断轰弹窗"
    assert fo.tapped == ["a_popup"], "走边前应把弹窗点掉"
    assert nav.at("dg") and nav.where() == "dg"
    assert nav.go("dg") is True, "已在目标应零成本直返"
    fo.state = "a_city"
    assert nav.go("city") is True, "回头路(dg->city)"

    # 自愈: 边动作点歪落在意外屏(非目标), 重试尽后重定位换路仍能到达
    fo2 = FakeOps()
    nav2 = nav_mod.Nav(fo2)
    for n, a in (("city", "a_city"), ("menu", "a_menu"), ("dg", "a_dg")):
        nav2.screen(n, a)
    nav2.edge("city", "menu", to(fo2, "a_dg"))       # 点歪: 想去 menu 落在 dg
    nav2.edge("dg", "menu", to(fo2, "a_menu"))
    assert nav2.go("menu") is True and nav2.cur == "menu", "点歪后应重定位自愈"

    # 迷路: 全锚点扫不到(黑屏/掉登录) → LostError
    fo3 = FakeOps()
    fo3.state = "a_black_screen"
    nav3 = nav_mod.Nav(fo3)
    nav3.screen("city", "a_city")
    try:
        nav3.go("city")
        raise AssertionError("迷路应抛 LostError")
    except nav_mod.LostError:
        pass

    # 建图错误大声: 未注册界面/无可达路径/边引用未注册界面
    try:
        nav3.go("nowhere")
        raise AssertionError("未注册目标应 ValueError")
    except ValueError:
        pass
    try:
        nav3.edge("city", "nowhere", to(fo3, "a_city"))
        raise AssertionError("边引用未注册界面应 ValueError")
    except ValueError:
        pass
    nav3.screen("island", "a_island")
    fo3.state = "a_city"
    try:
        nav3.go("island")
        raise AssertionError("无可达路径应 ValueError")
    except ValueError:
        pass


def check_win32_input():
    """win32 键鼠层: 拟人轨迹/后台消息序列/剪贴板/首击播种/LockInput。"""
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
        records.clear()
        mk.key_tap(0x0D)                       # VK_RETURN: 主键盘回车, 非扩展键
        pump()
        keys = [(m, w, l) for m, w, l in records if m in (WM_KEYDOWN, WM_KEYUP)]
        assert [m for m, _w, _l in keys] == [WM_KEYDOWN, WM_KEYUP], keys
        scan = _user32.MapVirtualKeyW(0x0D, 0)  # 扫描码按真机布局查, 断言不写死
        assert keys[0][1] == 0x0D and keys[0][2] == 1 | (scan << 16), (hex(keys[0][2]),)
        assert keys[1][2] == 1 | (scan << 16) | (1 << 30) | (1 << 31), (hex(keys[1][2]),)
        records.clear()
        send_ime_string(gw, "魔A")            # 大漠 SendString 真身配方
        pump()
        imes = [w for m, w, _l in records if m == 0x286]
        gbk = "魔A".encode("gbk")
        assert imes == [(gbk[0] << 8) | gbk[1], gbk[2]], \
            (imes, "WM_IME_CHAR 应 GBK 双字节大端打包/ASCII 单字节直传")
        # 补齐后的 windows 通道全表面(与 dx/SendInput 同形): 右键/滚轮/裸按钮/拖拽
        records.clear()
        mk.right_click(30, 40)
        pump()
        kinds = [m for m, _w, _l in records]
        assert 0x204 in kinds and 0x205 in kinds, kinds
        assert [w for m, w, _l in records if m == 0x204] == [2], "右键按下 wParam=MK_RBUTTON"
        records.clear()
        mk.left_down()
        mk.left_up()
        mk.wheel(up=True)
        pump()
        kinds = [m for m, _w, _l in records]
        assert kinds[:2] == [WM_LBUTTONDOWN, WM_LBUTTONUP], kinds
        assert [w for m, w, _l in records if m == 0x20A] == [120 << 16], \
            "WM_MOUSEWHEEL wParam 高16位=delta"
        records.clear()
        mk.drag(10, 10, 50, 50, steps=2, interval=0)
        pump()
        kinds = [m for m, _w, _l in records]
        # 拖拽序列: 移到起点 → LDOWN → 2 步插值移动 → LUP; 光标记到终点
        assert kinds[0] == WM_MOUSEMOVE and kinds[1] == WM_LBUTTONDOWN, kinds
        assert kinds[-1] == WM_LBUTTONUP and kinds.count(WM_MOUSEMOVE) == 3, kinds
        assert mk.pos == (50, 50), mk.pos
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

    # ---- win32: 拟人首击起点播种: 真实光标→客户区(窗外钳边), 不再恒 (0,0) 会话指纹 ----
    vh6, cleanup6 = create_visible_window(200, 100, (30, 200, 90))
    try:
        sx, sy, cw, ch = GameWindow(vh6).client_rect()
        _user32.SetCursorPos(sx + cw // 2, sy + ch // 2)
        assert MouseKeyboard(GameWindow(vh6)).pos == (cw // 2, ch // 2), "窗内: 种子=光标落点"
        _user32.SetCursorPos(sx - 50, sy - 50)          # 窗外左上 → 钳边
        assert MouseKeyboard(GameWindow(vh6)).pos == (0, 0), "窗外: 应钳到贴边"
    finally:
        cleanup6()

    # ---- win32: LockInput(BlockInput; lock=1 需管理员, 不断言锁定结果只走通解锁;
    # 记账挂实例 —— 锁/解同一对象才有"真锁过"语义) ----
    dmli = DmSoft()
    try:
        dmli.LockInput(1)
    finally:
        assert dmli.LockInput(0) == 1


def check_win32_capture():
    """win32 截图层: PrintWindow 后台链路 + 前台 BitBlt(用纯色窗口验色)。"""
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

    # ---- win32: gdi2 第二截图路(WM_PRINT, 工单 damo-bg-capture/04; 同窗对照) ----
    vh2, cleanup2 = create_visible_window(200, 100, (30, 200, 90))
    try:
        time.sleep(0.3)
        img2 = None
        for _ in range(3):
            img2 = capture_client_gdi2(GameWindow(vh2))
            if int(img2.max()) > 0:
                break
            time.sleep(0.25)
        assert img2 is not None and img2.shape[:2] == (100, 200), img2.shape if img2 is not None else None
        assert int(img2.max()) > 0, "gdi2 WM_PRINT 路全黑: 窗口过程未自绘"
        b2, g2, r2 = img2[50, 100]
        assert abs(int(b2) - 90) <= 6 and abs(int(g2) - 200) <= 6 and abs(int(r2) - 30) <= 6, (b2, g2, r2)
    finally:
        cleanup2()

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


def check_win32_window():
    """win32 窗口层: 拾取高亮/伪最小化/大漠窗口族/DACL 解封。"""
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

    # ---- dx2/dx3 无注入路(工单 damo-bg-capture/02): 绑定即停靠, 图色 PrintWindow,
    # unbind 还原原位; dx 键鼠通道在此词汇下必须拒绝 ----
    vh6, cleanup6 = create_visible_window(200, 100, (30, 200, 90))
    try:
        time.sleep(0.3)
        prev_rect = GameWindow(vh6).window_rect()
        dm2 = DmSoft()
        assert dm2.BindWindow(vh6, "dx2", "windows", "windows", 0) == 1
        parked_rect = GameWindow(vh6).window_rect()
        assert parked_rect[0] > prev_rect[0], "dx2 绑定应把窗口停靠到屏外细缝"
        w6, h6 = GameWindow(vh6).client_rect()[2:]   # 客户区实际尺寸(200% DPI 下非 200x100)
        img6 = None
        for _ in range(3):
            img6 = dm2.screen()
            if int(img6.max()) > 0:
                break
            time.sleep(0.25)
        assert img6 is not None and img6.shape[:2] == (h6, w6), \
            f"dx2 图色应取到 PrintWindow 帧: img={None if img6 is None else img6.shape} client={(w6, h6)}"
        assert int(img6.max()) > 0, "dx2 图色全黑"
        b6, g6, r6 = img6[h6 // 2, w6 // 2]
        assert abs(int(b6) - 90) <= 6 and abs(int(g6) - 200) <= 6 and abs(int(r6) - 30) <= 6, (b6, g6, r6)
        dm3 = DmSoft()
        try:
            dm3.BindWindow(vh6, "dx3", "dx", "windows", 0)
            raise AssertionError("dx3 + dx 键鼠应拒绝(无注入路)")
        except NotImplementedError:
            pass
        assert dm2.UnBindWindow() == 1
        assert GameWindow(vh6).window_rect()[:2] == prev_rect[:2], "解绑应还原窗口原位"
        # 绑定中途失败(停靠之后的 mouse 分派抛错)同样要还原: 不还原则窗口悬在屏外细缝,
        # 同对象重绑还会把细缝位当原位存档, 原位永久丢失(2026-11-25 修)
        dm4 = DmSoft()
        try:
            dm4.BindWindow(vh6, "dx2", "foo", "windows", 0)
            raise AssertionError("无效 mouse 模式应拒绝")
        except NotImplementedError:
            pass
        assert GameWindow(vh6).window_rect()[:2] == prev_rect[:2], "绑定失败应还原窗口原位"
        assert dm4.BindWindow(vh6, "dx2", "windows", "windows", 0) == 1    # 同对象改对后重绑
        assert dm4.UnBindWindow() == 1
        assert GameWindow(vh6).window_rect()[:2] == prev_rect[:2], "失败后重绑再解绑, 原位不应丢"
    finally:
        cleanup6()

    # ---- win32: 大漠窗口族(SetWindowState 0-14 口径 + EnumWindow 过滤位) ----
    dmw = DmSoft()
    vh5, cleanup5 = create_visible_window(120, 60, (30, 200, 90))
    try:
        assert set_window_state(vh5, 6) and not _user32.IsWindowVisible(vh5)
        assert set_window_state(vh5, 7) and _user32.IsWindowVisible(vh5)
        assert set_window_state(vh5, 8)
        assert _user32.GetWindowLongW(vh5, -20) & 0x8, "置顶后应带 WS_EX_TOPMOST"
        assert set_window_state(vh5, 9)
        assert not _user32.GetWindowLongW(vh5, -20) & 0x8
        assert set_window_state(vh5, 10) and not _user32.IsWindowEnabled(vh5)
        assert set_window_state(vh5, 11) and _user32.IsWindowEnabled(vh5)
        assert set_window_state(vh5, 2) and _user32.IsIconic(vh5)
        assert set_window_state(vh5, 12) and not _user32.IsIconic(vh5)
        assert vh5 in enum_windows_dm(0, "imgbot-selfcheck", "", 1 | 8), \
            "标题+可见过滤应枚举到自己"
        assert vh5 not in enum_windows_dm(0, "绝不存在的标题xyz", "", 1 | 8)
        # filter 16/32/64: 最小化/最大化/弹出式状态位
        assert set_window_state(vh5, 2) and _user32.IsIconic(vh5)
        assert vh5 in enum_windows_dm(0, "", "", 16), "最小化窗应被 filter 16 命中"
        assert set_window_state(vh5, 12)
        assert set_window_state(vh5, 4) and _user32.IsZoomed(vh5)
        assert vh5 in enum_windows_dm(0, "", "", 32), "最大化窗应被 filter 32 命中"
        assert set_window_state(vh5, 12)
        hpop = _user32.CreateWindowExW(0, "BUTTON", "imgbot-selfcheck",
                                       0x80000000 | 0x10000000,  # WS_POPUP|WS_VISIBLE
                                       5, 5, 60, 20, None, None, None, None)
        try:
            assert hpop and hpop in enum_windows_dm(0, "", "", 64), "弹出式窗应被 filter 64 命中"
            assert vh5 not in enum_windows_dm(0, "", "", 64), "重叠窗不该进 filter 64"
        finally:
            _user32.DestroyWindow(hpop)
        # 子窗句柄置顶属调用方责任(库哑透传, 见 set_window_state 口径), 不设断言
        # DmSoft 层包装(大漠壳转发必须与 win32 底层同效)
        assert dmw.SetWindowState(vh5, 6) == 1 and not _user32.IsWindowVisible(vh5)
        assert dmw.SetWindowState(vh5, 7) == 1
        assert str(vh5) in dmw.EnumWindow(0, "imgbot-selfcheck", "", 1 | 8)
        # 窗口族单风格(全走 GameWindow, 不再有壳内联 ctypes)
        assert dmw.GetWindowClass(vh5).lower().startswith("imgbotvis"), dmw.GetWindowClass(vh5)
        assert dmw.GetWindowTitle(vh5) == "imgbot-selfcheck", dmw.GetWindowTitle(vh5)
        dmw.MoveWindow(vh5, 100, 100)
        assert GameWindow(vh5).window_rect()[:2] == (100, 100), "MoveWindow 应真移动"
        assert isinstance(dmw.GetForegroundWindow(), int)
        assert pids_of_process("python") or pids_of_process("uv"), \
            "本进程名应能取到 pid 集"
        try:
            set_window_state(vh5, 99)
            assert False, "越界 flag 应抛 ValueError"
        except ValueError:
            pass
    finally:
        cleanup5()

    # ---- win32: 进程 DACL 解封(护甲锁; 本进程无锁, 验证幂等不炸+返回 bool) ----
    assert isinstance(unlock_process_dacl(_kernel32.GetCurrentProcessId()), bool)


def check_docs():
    """文档 lint: api.md/README 引用的 API 真实存在 + 注释无吞码。"""
    _check_readme_api()
    _check_no_swallowed_code()


_LAYERS = {
    "adb": check_adb,
    "adb_session": check_adb_session,
    "vision": check_vision,
    "actions": check_actions,
    "ocr": check_ocr,
    "ops": check_ops,
    "nav": check_nav,
    "win32_input": check_win32_input,
    "win32_capture": check_win32_capture,
    "win32_window": check_win32_window,
    "docs": check_docs,
}
_ALIASES = {"win32": ["win32_input", "win32_capture", "win32_window"]}


def main(argv=None):
    """按层跑自检; 不带参数全量。用法: python -m imgbot.selfcheck [层名[,层名...]]"""
    args = sys.argv[1:] if argv is None else argv
    if args:
        want = []
        for name in ",".join(args).split(","):
            want.extend(_ALIASES.get(name, [name]))
        unknown = [n for n in want if n not in _LAYERS]
        if unknown:
            raise SystemExit(f"未知层: {', '.join(unknown)} —— "
                             f"可选: {', '.join(_LAYERS)}(win32=三段合选)")
    else:
        want = list(_LAYERS)
    for name in want:
        _LAYERS[name]()
        print(f"selfcheck[{name}] ok")
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
    """API 引用必须真实存在(防文档漂移: 清单是给别的 AI 查用的, 写了没有的 API
    比不写更糟)。2026-11-01 README 重组后: README 只留简介/打包, 能力清单在
    docs/api.md —— 正向引用两文件都查, 反向"漏列"查 docs/api.md 全文。"""
    import ctypes as _ct
    import inspect
    root = Path(__file__).resolve().parent.parent
    readme = root / "README.md"
    api_doc = root / "docs" / "api.md"
    if not readme.exists():          # 装成 wheel 后文档不在包里, 跳过
        return
    text = readme.read_text(encoding="utf-8")
    assert api_doc.exists(), "docs/api.md 应与 README 同在(能力清单迁此)"
    api_text = api_doc.read_text(encoding="utf-8")
    both = text + "\n" + api_text
    from . import actions, adb, adb_session, ime, nav, ocr, ops, readout, target, vision
    from .win32 import capture
    from .win32 import input as win_input
    from .win32 import window
    modules = [vision, ocr, readout, actions, adb, adb_session, ime, target, ops, nav,
               window, capture, win_input]
    # `模块.名` 形式(可带不带括号): 名必须真实可取(含 win32 子模块的写法)
    # 模块名按文档里的简称匹配: vision/ocr/readout/actions/adb/ime/target + win32 三件
    by_name = {m.__name__.rsplit(".", 1)[-1]: m for m in modules}
    submodules = {"capture": capture, "input": win_input, "window": window}
    # 类名也算合法前缀(如 `Target.screenshot` 指的是 target 模块的类)
    class_names = {n for mod in modules for n, o in vars(mod).items() if inspect.isclass(o)}
    for m, f in set(re.findall(r"`([A-Za-z_][A-Za-z_0-9]*)\.([A-Za-z_][A-Za-z_0-9]*)`", both)):
        if f in ("py", "md", "toml", "exe", "dll", "whl", "apk", "png", "bmp"):
            continue                 # 文件名引用(如 `GLOSSARY.md`): 点是分隔符, f=扩展名
        mod = submodules.get(m) or by_name.get(m)
        if mod is None:
            if m == "imgbot" or m in class_names:
                continue      # 包路径 / 类名引用
            raise AssertionError(f"文档引用了未知模块: {m}")
        if not hasattr(mod, f):
            raise AssertionError(f"文档引用了不存在的 API: {m}.{f}")
    # 裸函数名写法 `name(` 也必须真实存在(清单里大量是裸名)
    # 只认**模块级**函数/类 —— 类的方法名(如 screenshot/tap)也是合法裸名, 不在此列
    known = set()
    for mod in modules:
        known |= {n for n, o in vars(mod).items()
                  if inspect.isfunction(o) or inspect.isclass(o)}
    for mod in modules:
        for cls in [o for o in vars(mod).values() if inspect.isclass(o)]:
            known |= set(vars(cls).keys())     # 类的方法/属性名也算已知
    for f in set(re.findall(r"`([A-Za-z_][A-Za-z_0-9]*)\(", both)):
        if f not in known:
            raise AssertionError(f"文档引用了不存在的函数: {f}")
    # 各模块**自己定义**的公开名(函数/类)都应出现在 docs/api.md 能力清单内
    # (全文都算清单段 —— README 只留简介, 漏列检不出才是问题)
    catalog = api_text
    for mod in modules:
        for name, obj in vars(mod).items():
            if name.startswith("_") or not (inspect.isfunction(obj) or inspect.isclass(obj)):
                continue
            if getattr(obj, "__module__", "") != mod.__name__:
                continue          # 别的模块导入进来的, 不算本模块的 API
            if inspect.isclass(obj) and issubclass(obj, _ct.Structure):
                continue          # ctypes 结构体是内部实现细节, 不进能力清单
            if name not in catalog:
                raise AssertionError(f"api.md 能力清单漏了: {mod.__name__}.{name}")


if __name__ == "__main__":
    main()
