# -*- coding: utf-8 -*-
"""后台截图(LZG_PMH 移植, PIL→ndarray BGR 归一) — PrintWindow(PW_CLIENTONLY | PW_RENDERFULLCONTENT).

实机验证: 目标窗口被遮挡时与前台画面差异仅 0.29%, 可用。
"""
import ctypes
import ctypes.wintypes as wt
import time

import numpy as np

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

PW_CLIENTONLY = 1
PW_RENDERFULLCONTENT = 2
SW_RESTORE = 9
SRCCOPY = 0x00CC0020
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


class CaptureError(RuntimeError):
    """截图失败: 窗口无效、客户区为 0 或系统调用失败。"""


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class RGBQUAD(ctypes.Structure):
    _fields_ = [("rgbBlue", ctypes.c_uint8), ("rgbGreen", ctypes.c_uint8),
                ("rgbRed", ctypes.c_uint8), ("rgbReserved", ctypes.c_uint8)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]


def capture_client(window) -> np.ndarray:
    """截取窗口客户区, 返回 BGR ndarray(与 adb 后端同序, 图色层零适配).

    不要求窗口在前台, 被遮挡亦可; 真最小化时自动 SW_RESTORE 还原后重试
    (最小化下目标停笔, 任何截图都拿不到新像素)。坐标仍是客户区相对。
    """
    try:
        _, _, w, h = window.client_rect()
    except OSError as e:
        raise CaptureError("窗口无效: %r" % (window.hwnd,)) from e
    if w <= 0 or h <= 0:
        if not _restore_if_minimized(window):
            raise CaptureError("客户区为 0, 窗口无效: %r" % (window.hwnd,))
        _, _, w, h = window.client_rect()
        restored = True
    else:
        restored = False
    # 还原后 DWM 重新合成需要时间, 首帧可能仍是旧的黑帧: 排空重绘消息后多试几次
    attempts, last = (3 if restored else 1), None
    for _ in range(attempts):
        try:
            return _capture_once(window, w, h)
        except CaptureError as e:
            last = e
            _drain_paints()
            time.sleep(0.25)
    raise last


def virtual_screen_rect():
    """(x, y, w, h) 全部显示器拼成的虚拟屏幕; 多屏时原点可为负(主屏左上恒为 0,0)."""
    x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    if w <= 0 or h <= 0:
        raise CaptureError("虚拟屏幕尺寸非法: %r" % ((x, y, w, h),))
    return x, y, w, h


def capture_screen(rect=None) -> np.ndarray:
    """截取屏幕前台画面(微信截图的底层), 返回 BGR ndarray.

    rect=(x,y,w,h) 屏幕坐标, 可为负原点(多屏), None=整个虚拟屏幕。
    遮挡即所得: 被盖住的部分拍到的是遮挡者 —— 与 capture_client(后台, 不受遮挡)互补。
    """
    from .window import ensure_dpi_aware
    ensure_dpi_aware()
    if rect is None:
        rect = virtual_screen_rect()
    x, y, w, h = (int(v) for v in rect)
    if w <= 0 or h <= 0:
        raise CaptureError("截屏矩形非法: %r" % (rect,))
    hdc_scr = user32.GetDC(None)
    if not hdc_scr:
        raise CaptureError("GetDC(None) 失败: %d" % ctypes.get_last_error())
    hdc_mem = gdi32.CreateCompatibleDC(hdc_scr)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_scr, w, h)
    if not hdc_mem or not hbmp:
        if hbmp:
            gdi32.DeleteObject(hbmp)
        if hdc_mem:
            gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_scr)
        raise CaptureError("CreateCompatibleDC/Bitmap 失败: %d" % ctypes.get_last_error())
    old_bmp = gdi32.SelectObject(hdc_mem, hbmp)   # 选入时的原位图要选回, 否则 Delete 静默失败泄漏
    try:
        if not gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_scr, x, y, SRCCOPY):
            raise CaptureError("BitBlt 失败: %d" % ctypes.get_last_error())
        img = _dib_to_bgr(hdc_mem, hbmp, w, h)
    finally:
        gdi32.SelectObject(hdc_mem, old_bmp)
        gdi32.DeleteObject(hbmp)
        user32.ReleaseDC(None, hdc_scr)
        gdi32.DeleteDC(hdc_mem)
    return img


def _dib_to_bgr(hdc_mem, hbmp, w, h):
    """内存位图 -> BGR ndarray(32bpp DIB 内存序是 BGRA, 前 3 通道直接切)."""
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h          # 负高 = 自上而下
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    if gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0) == 0:
        raise CaptureError("GetDIBits 取数失败: %d" % ctypes.get_last_error())
    arr = np.frombuffer(buf.raw, dtype=np.uint8).reshape(h, w, 4)
    return np.ascontiguousarray(arr[:, :, :3])


def _drain_paints():
    """派发本线程队列里的 WM_PAINT 等消息(还原后的静态窗口需要重绘才能非黑)。"""
    msg = wt.MSG()
    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def _capture_once(window, w, h):
    hdc_win = user32.GetWindowDC(window.hwnd)
    if not hdc_win:
        raise CaptureError("GetWindowDC 失败: %d" % ctypes.get_last_error())
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    gdi32.SelectObject(hdc_mem, hbmp)
    try:
        ok = user32.PrintWindow(window.hwnd, hdc_mem, PW_CLIENTONLY | PW_RENDERFULLCONTENT)
        img = _dib_to_bgr(hdc_mem, hbmp, w, h)
    finally:
        gdi32.DeleteObject(hbmp)
        user32.ReleaseDC(window.hwnd, hdc_win)
        gdi32.DeleteDC(hdc_mem)

    if not ok and _all_black(img):
        raise CaptureError("PrintWindow 失败且画面全黑: %r" % (window.hwnd,))
    return img


def _restore_if_minimized(window):
    """窗口处于真最小化时 SW_RESTORE 还原, 返回是否成功脱离最小化态。

    最小化时目标收到 WM_SIZE(SIZE_MINIMIZED) 自行停笔, 截图拿不到新像素;
    还原后窗口回到最小化前的位置: 伪最小化(挪出屏幕)时仍在屏外, 否则在屏上原位。
    """
    if not user32.IsIconic(window.hwnd):
        return False
    user32.ShowWindow(window.hwnd, SW_RESTORE)
    user32.UpdateWindow(window.hwnd)         # 同步请求重绘, 刷新 DWM 重定向表面
    time.sleep(0.3)                          # 等窗口重排
    return not user32.IsIconic(window.hwnd)


def _all_black(img):
    return bool(img.max() == 0)
