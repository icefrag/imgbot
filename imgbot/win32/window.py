# -*- coding: utf-8 -*-
"""窗口绑定(LZG_PMH 移植, 游戏常量参数化) — 窗口句柄与客户区矩形.

约束(实机结论):
- 所有坐标一律相对客户区原点, 每次操作前实时刷新矩形, 窗口移动免疫;
- 目标进程以管理员运行时(UIPI), 本进程必须同级, 否则 PostMessage 被拒;
- 进程需 DPI 感知, 非 100% 缩放下坐标与物理像素一致。
"""
import ctypes
import ctypes.wintypes as wt
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

R2_NOT = 6        # 画上去即反色, 同矩形再画一次即还原(XOR 自逆)
NULL_BRUSH = 5    # 只描边不填充
PS_SOLID = 0

gdi32.GetStockObject.restype = ctypes.c_void_p
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.SetROP2.restype = ctypes.c_int
gdi32.SetROP2.argtypes = [ctypes.c_void_p, ctypes.c_int]
gdi32.CreatePen.restype = ctypes.c_void_p
gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wt.COLORREF]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.Rectangle.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int, ctypes.c_int]
user32.GetDC.restype = ctypes.c_void_p
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, ctypes.c_void_p]


class UipiError(PermissionError):
    """PostMessage 被系统拒绝(UIPI): 目标窗口权限高于本进程, 需以管理员运行。"""


class WindowLostError(RuntimeError):
    """绑定的窗口已失效(被关闭/句柄过期), 需重新绑定。"""


user32.PostMessageW.restype = wt.BOOL
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
kernel32.GetCurrentProcessId.restype = wt.DWORD
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.IsIconic.argtypes = [wt.HWND]
user32.IsIconic.restype = wt.BOOL
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.ShowWindow.restype = wt.BOOL
user32.IsHungAppWindow.argtypes = [wt.HWND]
user32.IsHungAppWindow.restype = wt.BOOL
user32.MonitorFromWindow.restype = wt.HMONITOR
user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
user32.MonitorFromRect.restype = wt.HMONITOR
user32.MonitorFromRect.argtypes = [ctypes.POINTER(wt.RECT), wt.DWORD]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD)]


user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFO)]

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_NOZORDER = 0x0004
SW_RESTORE = 9


def list_windows():
    """所有可见且有标题的顶层窗口 [(hwnd, 类名, 标题)] — 供工具类消费方做人工选绑列表."""
    ensure_dpi_aware()
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def on_window(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        if not user32.GetWindowTextLengthW(hwnd):
            return True              # 无标题的悬浮/工具窗口, 人工选绑无意义
        title = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
        user32.GetWindowTextW(hwnd, title, len(title))
        cname = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cname, 256)
        out.append((hwnd, cname.value, title.value))
        return True

    user32.EnumWindows(on_window, 0)
    return out


def xor_frame(rect):
    """屏幕坐标矩形(l,t,r,b)上 XOR 反色描 2px 框(Spy++/大漠式拾取高亮).

    XOR 自逆: 同一矩形再调一次即擦除 —— 调用方自己记住上次画的矩形."""
    hdc = user32.GetDC(None)
    if not hdc:
        return
    pen = gdi32.CreatePen(PS_SOLID, 2, 0)
    brush = gdi32.GetStockObject(NULL_BRUSH)
    old_pen = gdi32.SelectObject(hdc, pen)
    old_brush = gdi32.SelectObject(hdc, brush)
    old_rop2 = gdi32.SetROP2(hdc, R2_NOT)
    try:
        gdi32.Rectangle(hdc, *rect)
    finally:
        gdi32.SetROP2(hdc, old_rop2)
        gdi32.SelectObject(hdc, old_brush)
        gdi32.SelectObject(hdc, old_pen)
        gdi32.DeleteObject(pen)
        user32.ReleaseDC(None, hdc)


def is_elevated():
    """本进程是否以管理员运行(UIPI 同级要求)。"""
    return bool(ctypes.WinDLL("shell32").IsUserAnAdmin())


def ensure_dpi_aware():
    """DPI 感知, 幂等。按优先级尝试 Per-Monitor V2 -> Per-Monitor -> System。"""
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except AttributeError:
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2):
            return
    except (AttributeError, OSError):
        pass
    user32.SetProcessDPIAware()


class GameWindow:
    """已绑定的目标窗口。所有输入/截图坐标以客户区为基准(大漠 BindWindow 的绑定物)."""

    def __init__(self, hwnd):
        self.hwnd = hwnd
        self._parked_rect = None          # 伪最小化停靠前的窗口矩形存档, None = 不在屏外

    @classmethod
    def find(cls, class_name=None, title_keyword=None):
        """按窗口类名(优先)或标题关键字定位窗口; 找不到返回 None. 至少给一个条件."""
        if not class_name and not title_keyword:
            raise ValueError("class_name / title_keyword 至少给一个")
        ensure_dpi_aware()
        by_class, by_title = None, None

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def on_window(hwnd, _):
            nonlocal by_class, by_title
            if not user32.IsWindowVisible(hwnd):
                return True
            cname = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cname, 256)
            if class_name and cname.value == class_name:
                if by_class is None:
                    by_class = GameWindow(hwnd)
                return True
            if title_keyword:
                n = user32.GetWindowTextLengthW(hwnd)
                if n:
                    title = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, title, n + 1)
                    if title_keyword in title.value and by_title is None:
                        by_title = GameWindow(hwnd)
            return True

        user32.EnumWindows(on_window, 0)
        return by_class or by_title

    @classmethod
    def from_cursor(cls):
        """取鼠标光标所在窗口(向上取到顶层窗口); 无窗口/本进程窗口返回 None。"""
        ensure_dpi_aware()
        pt = wt.POINT()
        if not user32.GetCursorPos(ctypes.byref(pt)):
            return None
        hwnd = user32.WindowFromPoint(pt)
        if not hwnd:
            return None
        hwnd = user32.GetAncestor(hwnd, 2)          # GA_ROOT: 子控件 -> 顶层窗口
        if not hwnd or not user32.IsWindow(hwnd):
            return None
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == kernel32.GetCurrentProcessId():
            return None                              # 鼠标在工具自己的窗口上
        return GameWindow(hwnd)

    def client_rect(self):
        """(屏幕x, 屏幕y, 宽, 高) — 每次调用实时获取, 窗口移动免疫。"""
        rc = wt.RECT()
        if not user32.GetClientRect(self.hwnd, ctypes.byref(rc)):
            raise OSError("GetClientRect 失败: %d" % ctypes.get_last_error())
        pt = wt.POINT(0, 0)
        if not user32.ClientToScreen(self.hwnd, ctypes.byref(pt)):
            raise OSError("ClientToScreen 失败: %d" % ctypes.get_last_error())
        return pt.x, pt.y, rc.right - rc.left, rc.bottom - rc.top

    def is_alive(self):
        return bool(user32.IsWindow(self.hwnd))

    def is_hung(self):
        """窗口是否假死(主循环卡死, 系统判定约 5s 未处理消息, 即任务栏"未响应")。"""
        return bool(user32.IsHungAppWindow(self.hwnd))

    def info(self):
        """(类名, 标题, 客户区(x,y,w,h)) — 绑定日志与身份校验用。"""
        cname = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(self.hwnd, cname, 256)
        n = user32.GetWindowTextLengthW(self.hwnd)
        title = ctypes.create_unicode_buffer(n + 1)
        if n:
            user32.GetWindowTextW(self.hwnd, title, n + 1)
        return cname.value.strip(), title.value.strip(), self.client_rect()

    def window_rect(self):
        """(左, 上, 右, 下) 屏幕坐标 — 含边框/标题栏的完整窗口矩形(拾取高亮用)."""
        rc = self._window_rect()
        return rc.left, rc.top, rc.right, rc.bottom

    def _window_rect(self):
        """窗口屏幕矩形(wt.RECT: left/top/right/bottom); 取失败抛 OSError."""
        rc = wt.RECT()
        if not user32.GetWindowRect(self.hwnd, ctypes.byref(rc)):
            raise OSError("GetWindowRect 失败: %d" % ctypes.get_last_error())
        return rc

    def resize_client_to(self, target):
        """把窗口调整为客户区 target=(宽,高), 保持位置。等待重排后复核, 返回是否成功."""
        try:
            rect = self._window_rect()
        except OSError:
            return False
        _, _, cw, ch = self.client_rect()
        dw = (rect.right - rect.left) - cw      # 外框 - 客户区 = 边框+标题栏加成
        dh = (rect.bottom - rect.top) - ch
        if not user32.SetWindowPos(self.hwnd, None, 0, 0,
                                   target[0] + dw, target[1] + dh, SWP_NOMOVE):
            return False
        time.sleep(0.3)
        return self.client_rect()[2:] == tuple(target)

    @property
    def is_pseudo_minimized(self):
        """是否处于伪最小化(已挪出屏幕)状态 — GUI 按钮同步文案用。"""
        return self._parked_rect is not None

    def park_offscreen(self, park):
        """伪后台停靠: 窗口挪到所在显示器右下角之外, 仅留 2×30px 细缝在屏内
        (通常被任务栏盖住, 视觉上等于消失)。

        完全离屏会触发 DXGI 遮挡节流, 截图变暗冻结(LZG ADR-0002); 缝在屏内即可
        让系统认为窗口可见、游戏持续真实渲染。停靠前自愈: 若窗口已在完全
        离屏位置(上次被杀没还原/游戏记住了), 先挪回屏内默认位再存档。
        """
        if park:
            if self._parked_rect is not None or not self.is_alive():
                return
            if user32.IsIconic(self.hwnd):
                user32.ShowWindow(self.hwnd, SW_RESTORE)
            rc = self._window_rect()
            if not self._rect_on_monitor(rc):
                self._move_to_default()                    # 自愈: 完全离屏先回屏内
                rc = self._window_rect()
            mon = MONITORINFO()
            mon.cbSize = ctypes.sizeof(MONITORINFO)
            user32.GetMonitorInfoW(user32.MonitorFromWindow(self.hwnd, 1),
                                   ctypes.byref(mon))
            # 右边缘留 2px 宽、底部 30px 高的缝(任务栏一般 ≥30px, 正好盖住)
            x = mon.rcMonitor.right - 2
            y = mon.rcMonitor.bottom - 30
            if not user32.SetWindowPos(self.hwnd, None, x, y, 0, 0,
                                       SWP_NOSIZE | SWP_NOZORDER):
                raise OSError("SetWindowPos 失败: %d" % ctypes.get_last_error())
            self._parked_rect = (rc.left, rc.top, rc.right, rc.bottom)
        else:
            rect = self._parked_rect
            if rect is None:
                return
            self._parked_rect = None
            if self.is_alive():
                if user32.IsIconic(self.hwnd):
                    user32.ShowWindow(self.hwnd, SW_RESTORE)
                l, t, r, b = rect
                user32.SetWindowPos(self.hwnd, None, l, t, r - l, b - t, SWP_NOZORDER)

    def _rect_on_monitor(self, rc):
        """窗口矩形是否与任一显示器相交(有像素在屏内)。"""
        return bool(user32.MonitorFromRect(ctypes.byref(rc), 1))

    def _move_to_default(self):
        user32.SetWindowPos(self.hwnd, None, 100, 100, 0, 0,
                            SWP_NOSIZE | SWP_NOZORDER)

    def post_message(self, msg, wparam, lparam):
        """向窗口投递消息。失败时区分 UIPI 拒绝(需管理员)与其他错误。"""
        if user32.PostMessageW(self.hwnd, msg, wparam, lparam):
            return
        err = ctypes.get_last_error()
        if err == 5:
            raise UipiError(
                "消息被系统拒绝(权限不足): 目标以管理员运行, 本程序也必须以管理员运行"
            )
        raise OSError("PostMessage 失败: %d" % err)
