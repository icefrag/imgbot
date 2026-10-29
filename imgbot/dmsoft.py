# -*- coding: utf-8 -*-
"""DmSoft — 大漠插件接口壳(方法名/参数序/返回语义 1:1, Python 化出参为返回元组)。

用法与大漠脚本逐行对应:
    dm = DmSoft()
    dm.BindWindow(hwnd, "dx", "windows", "windows", 0)
    dm.MoveTo(500, 300); dm.LeftClick()
    x, y = dm.FindPic(0, 0, 2000, 2000, "btn.bmp", "000000", 0.9, 0)
    dm.Capture(0, 0, 1000, 1000, "screen.bmp")
    dm.UnBindWindow()

通道词汇 = 大漠模式字符串(全表 docs/ref/damo-BindWindow*.txt):
- display: "normal"(前台) / "gdi"/"gdi2"(PrintWindow 族) / "dx"*("dx","dx2",
  "dx.graphic.2d/3d/opengl" → 注入件; GL 已通, d3d9 在调试 → 明确报错)
- mouse/keypad: "windows"* → PostMessage; "dx"* → 注入件; "normal" → SendInput
  真事件(前台, 动真光标, 单开专用; 图色坐标仍统一客户区)
颜色格式同大漠: "RRGGBB" 或 "RRGGBB-DRDGDB"(偏差), FindPic 的 delta_color 同。
未实现的方法 raise NotImplementedError(带说明) —— 决策4: 明确错误优先。
"""
from __future__ import annotations

import time

import cv2
import numpy as np

from . import vision
from .win32.capture import capture_client, capture_screen
from .win32.input import MouseKeyboard
from .win32.window import GameWindow, ensure_dpi_aware


def _bgr(damo_color: str) -> tuple[tuple[int, int, int], int]:
    """"RRGGBB" 或 "RRGGBB-DRDGDB" → (BGR 元组, 容差)(带偏差的取首色, 偏差并入 tol)。"""
    head = damo_color.split("-")[0]
    r, g, b = int(head[0:2], 16), int(head[2:4], 16), int(head[4:6], 16)
    tol = 0
    if "-" in damo_color:
        d = damo_color.split("-")[1]
        tol = max(int(d[0:2], 16), int(d[2:4], 16), int(d[4:6], 16))
    return (b, g, r), tol


class DmSoft:
    """一个大漠对象 = 一个绑定。重复 BindWindow 前先 UnBindWindow(大漠同义)。"""

    def __init__(self):
        self._win: GameWindow | None = None
        self._mk: MouseKeyboard | None = None      # windows 通道(postmessage)
        self._dx = None                            # dx 通道注入件宿主
        self._dxmk = None                          # dx 通道键鼠
        self._display = None
        self._last_error = 0

    # ================= 后台设置: 绑定族(8) =================
    def BindWindow(self, hwnd: int, display: str, mouse: str, keypad: str,
                   mode: int = 0) -> int:
        """绑定窗口并选通道。返回 1 成功 / 0 失败(大漠语义)。"""
        try:
            self._win = GameWindow(hwnd)
            ensure_dpi_aware()
            self._display = display
            if display in ("gdi", "gdi2"):
                pass                                   # PrintWindow 按需现截
            elif display == "normal":
                pass                                   # 前台全屏, 按需现截
            elif display.startswith("dx"):
                from .dx.host import DxHost
                self._dx = DxHost(self._win.pid())
                self._dx.bind()
                self._dx.hook_auto()                   # gl 已通; d3d9 未通会抛
            else:
                raise NotImplementedError(f"display 模式未实现: {display}")
            if mouse.startswith("dx") or keypad.startswith("dx"):
                if self._dx is None:
                    from .dx.host import DxHost
                    self._dx = DxHost(self._win.pid())
                    self._dx.bind()
                from .dx.input import DxMouseKeyboard
                self._dx.bind_input(hwnd)
                self._dxmk = DxMouseKeyboard(self._dx, hwnd)
            if mouse.startswith("windows"):
                if self._dxmk is None and self._mk is None:
                    self._mk = MouseKeyboard(self._win)
            elif mouse.startswith("dx"):
                pass
            elif mouse == "normal":
                if self._dxmk is None and self._mk is None:
                    from .win32.input import SendInputMouseKeyboard
                    self._mk = SendInputMouseKeyboard(self._win)
            else:
                raise NotImplementedError(f"mouse 模式未实现: {mouse}")
            if keypad.startswith("windows"):
                if self._mk is None and self._dxmk is None:
                    self._mk = MouseKeyboard(self._win)
            elif keypad.startswith("dx"):
                pass
            elif keypad == "normal":
                if self._mk is None and self._dxmk is None:
                    from .win32.input import SendInputMouseKeyboard
                    self._mk = SendInputMouseKeyboard(self._win)
            else:
                raise NotImplementedError(f"keypad 模式未实现: {keypad}")
            return 1
        except NotImplementedError:
            raise
        except Exception as e:
            self._last_error = 1
            raise RuntimeError(f"BindWindow 失败: {e}") from e

    def UnBindWindow(self) -> int:
        """解绑并释放资源(大漠: 脚本退出时调用)。"""
        if self._dx is not None:
            self._dx.unbind()
        self._win = self._mk = self._dx = self._dxmk = None
        self._display = None
        return 1

    def ForceUnBindWindow(self, hwnd: int) -> int:
        """强制解绑指定窗口(宿主侧: 直接解绑本对象 dx; 他进程绑定不在范围)。"""
        return self.UnBindWindow() if self._win and self._win.hwnd == hwnd else 0

    def GetBindWindow(self) -> int:
        """当前绑定的窗口句柄, 未绑定返回 0。"""
        return self._win.hwnd if self._win else 0

    def IsBind(self, hwnd: int) -> int:
        return 1 if self._win and self._win.hwnd == hwnd else 0

    def SwitchBindWindow(self, hwnd: int) -> int:
        """不解绑切换同进程窗口。"""
        if not self._win:
            return 0
        if self._win.pid() != GameWindow(hwnd).pid():
            raise RuntimeError("SwitchBindWindow 要求同进程窗口(大漠同款限制)")
        self._win = GameWindow(hwnd)
        if self._dxmk is not None:
            self._dx.bind_input(hwnd)
            self._dxmk = type(self._dxmk)(self._dx, hwnd)
        return 1

    def EnableBind(self, enable: int) -> int:
        raise NotImplementedError("EnableBind(暂停/恢复后台)未实现, 用到再加")

    # ================= 后台设置: 开关族(核心) =================
    def EnableFakeActive(self, enable: int) -> int:
        """后台假激活(dx 通道)。"""
        self._need_dx()
        self._dx.fake_active(bool(enable))
        return 1

    def LockInput(self, lock: int) -> int:
        raise NotImplementedError("LockInput(禁止外部输入)未实现")

    def LockDisplay(self, lock: int) -> int:
        raise NotImplementedError("LockDisplay(锁定图色不刷新)未实现")

    def GetFps(self) -> int:
        """绑定窗口帧率(dx 图色: 一秒帧序号增量)。"""
        self._need_dx()
        s0 = self._dx.frame_info()[0]
        time.sleep(1.0)
        return self._dx.frame_info()[0] - s0

    def HackSpeed(self, rate: float) -> int:
        raise NotImplementedError("变速齿轮(阶段3b): IAT hook 时间函数, 未开工")

    def SetAero(self, enable: int) -> int:
        raise NotImplementedError("SetAero 未实现")

    def DownCpu(self, type_: int, rate: int) -> int:
        raise NotImplementedError("DownCpu 未实现")

    def SetInputDm(self, dm_id, rx, ry) -> int:
        raise NotImplementedError("SetInputDm 未实现")

    def EnableRealMouse(self, enable: int, mousedelay: int, mousestep: int) -> int:
        """拟人轨迹已在键鼠层默认开启(贝塞尔+ease-out), 此开关为兼容而设。"""
        return 1

    def EnableRealKeypad(self, enable: int) -> int:
        return 1                                  # 同上, 拟人默认开

    def EnableMouseSync(self, enable: int, time_out: int) -> int:
        return 1                                  # dx 通道本就同步(SendMessageTimeout)

    def EnableKeypadSync(self, enable: int, time_out: int) -> int:
        return 1

    def EnableSpeedDx(self, enable: int) -> int:
        raise NotImplementedError("EnableSpeedDx(高速 dx 键鼠)未实现")

    def EnableIme(self, enable: int) -> int:
        raise NotImplementedError("EnableIme 未实现")

    def EnableKeypadMsg(self, enable: int) -> int:
        raise NotImplementedError("EnableKeypadMsg 未实现")

    def EnableMouseMsg(self, enable: int) -> int:
        raise NotImplementedError("EnableMouseMsg 未实现")

    def EnableKeypadPatch(self, enable: int) -> int:
        raise NotImplementedError("EnableKeypadPatch(键盘补丁)未实现")

    def SetDisplayDelay(self, ms: int) -> int:
        raise NotImplementedError("SetDisplayDelay 未实现")

    def SetDisplayRefreshDelay(self, ms: int) -> int:
        raise NotImplementedError("SetDisplayRefreshDelay 未实现")

    def LockMouseRect(self, x1: int, y1: int, x2: int, y2: int) -> int:
        raise NotImplementedError("LockMouseRect(前台)未实现")

    # ================= 图色 =================
    def _screen(self) -> np.ndarray:
        if self._display == "normal":
            # 前台=截全屏后裁到绑定窗口客户区: 下游坐标系统一为客户区(大漠同款,
            # 不裁的话 FindPic 返回屏幕坐标、点击却按客户区坐标走, 必错位)
            img = capture_screen()
            rx, ry, w_, h_ = self._win.client_rect()
            return img[ry:ry + h_, rx:rx + w_]
        if self._display is None:
            raise RuntimeError("未 BindWindow")
        if self._display.startswith("dx"):
            return self._dx.capture()
        return capture_client(self._win)

    def Capture(self, x1, y1, x2, y2, file, *_):
        """截取区域存 BMP; 存档格式按扩展名(PNG/JPG 同 CapturePng/CaptureJpg)。"""
        img = self._screen()[y1:y2, x1:x2]
        return 1 if cv2.imwrite(file, img) else 0

    def CapturePng(self, x1, y1, x2, y2, file):
        return 1 if cv2.imwrite(file, self._screen()[y1:y2, x1:x2]) else 0

    def CaptureJpg(self, x1, y1, x2, y2, file, quality=90):
        return 1 if cv2.imwrite(file, self._screen()[y1:y2, x1:x2],
                                [cv2.IMWRITE_JPEG_QUALITY, quality]) else 0

    def GetColor(self, x, y, _bpp=0) -> str:
        """取色, 返回 "RRGGBB"(大漠格式)。"""
        b, g, r = vision.color_at(self._screen(), x, y)
        return f"{r:02X}{g:02X}{b:02X}"

    def GetColorBGR(self, x, y) -> str:
        b, g, r = vision.color_at(self._screen(), x, y)
        return f"{b:02X}{g:02X}{r:02X}"

    def CmpColor(self, x, y, color, sim=1.0) -> int:
        """比较坐标颜色。返回 1/0(大漠语义)。"""
        (b, g, r), tol = _bgr(color)
        return 1 if vision.cmp_color(self._screen(), x, y, (b, g, r),
                                     tol=tol + round((1 - sim) * 32)) else 0

    def FindColor(self, x1, y1, x2, y2, color, sim=1.0, dir=0, *_):
        """找色, 返回 (x, y); 未找到 (-1, -1)。dir=0 从左到右从上到下。"""
        hits = self._find_colors(x1, y1, x2, y2, color, sim)
        if not hits:
            return -1, -1
        return hits[0] if dir == 0 else hits[-1]

    def FindColorE(self, x1, y1, x2, y2, color, sim=1.0, dir=0) -> str:
        x, y = self.FindColor(x1, y1, x2, y2, color, sim, dir)
        return f"{x}|{y}"

    def FindColorEx(self, x1, y1, x2, y2, color, sim=1.0, dir=0) -> str:
        hits = self._find_colors(x1, y1, x2, y2, color, sim)
        return "|".join(f"{x},{y}" for x, y in hits)

    def _find_colors(self, x1, y1, x2, y2, color, sim):
        cols, tol = _bgr(color)
        return vision.find_color(self._screen(), [cols], tol=tol + round((1 - sim) * 32),
                                 region=(x1, y1, x2, y2))

    def FindPic(self, x1, y1, x2, y2, pic_name, delta_color="000000", sim=0.9,
                dir=0, *_):
        """找图(pic_name 支持 "a.bmp|b.bmp" 多模板)。返回 (找到的序号, x, y);
        未找到 (-1, -1, -1) —— x,y 为模板左上角(大漠语义; vision.find 返回中心,
        此处换算回去, 移植老脚本点击才不错位)。"""
        names = pic_name.split("|")
        screen = self._screen()
        for idx, name in enumerate(names):
            tpl = vision.load(name)
            hit = vision.find(screen, tpl, threshold=sim,
                              region=(x1, y1, x2, y2))
            if hit:  # 中心 → 左上角(大漠 FindPic 语义)
                return idx, hit[0] - tpl.shape[1] // 2, hit[1] - tpl.shape[0] // 2
        return -1, -1, -1

    def FindPicE(self, x1, y1, x2, y2, pic_name, delta_color="000000", sim=0.9,
                 dir=0) -> str:
        idx, x, y = self.FindPic(x1, y1, x2, y2, pic_name, delta_color, sim, dir)
        return f"{idx}|{x}|{y}"

    def FindMultiColor(self, x1, y1, x2, y2, first_color, offset_color, sim=1.0,
                       dir=0, *_):
        """多点找色: first_color="RRGGBB", offset_color="x|y|RRGGBB,...". 返回 (x, y)/(-1,-1)。"""
        from .vision import Pt
        (fb, fg, fr), ftol = _bgr(first_color)
        pts = [Pt(0, 0, (fb, fg, fr), ftol + round((1 - sim) * 32))]
        for item in offset_color.split(","):
            dx_, dy_, col = item.split("|")
            (b, g, r), t = _bgr(col)
            pts.append(Pt(int(dx_), int(dy_), (b, g, r), t + round((1 - sim) * 32)))
        hit = vision.find_by_points(self._screen(), (x1, y1, x2, y2), pts)
        return (hit[0], hit[1]) if hit else (-1, -1)

    def GetColorNum(self, x1, y1, x2, y2, color, sim=1.0) -> int:
        cols, tol = _bgr(color)
        return vision.count_color(self._screen(), [cols],
                                  tol=tol + round((1 - sim) * 32),
                                  region=(x1, y1, x2, y2))

    def IsDisplayDead(self, x1, y1, x2, y2, t=2) -> int:
        """区域 t 秒画面不变返回 1(卡屏检测; 区域裁剪后比对, 全屏死=区域死)。"""
        def shot():
            return self._screen()[y1:y2, x1:x2]
        return 1 if vision.is_display_dead(shot, timeout=t) else 0

    # ================= 键鼠 =================
    def _mk_(self):
        if self._dxmk is not None:
            return self._dxmk
        if self._mk is not None:
            return self._mk
        raise RuntimeError("键鼠通道未绑定(BindWindow 的 mouse/keypad 传了未实现的模式)")

    def _need_dx(self):
        if self._dx is None:
            raise RuntimeError("该接口需要 dx 通道(BindWindow mouse/keypad/display 含 'dx')")

    def MoveTo(self, x, y):
        """移动虚拟光标(客户区坐标); 越出客户区矩形拒绝并返回 0(大漠反编译
        MoveTo 辅助: GetClientRect 边界检查, 出界不发消息)。"""
        if self._win is not None:
            _, _, w_, h_ = self._win.client_rect()
            if not (0 <= x < w_ and 0 <= y < h_):
                return 0
        self._mk_().move(x, y)
        return 1

    def MoveR(self, rx, ry):
        x, y = self._mk_()._pos
        return self.MoveTo(x + rx, y + ry)

    def LeftClick(self):
        mk = self._mk_()
        mk.click(*mk._pos)
        return 1

    def LeftDoubleClick(self):
        mk = self._mk_()
        mk.double_click(*mk._pos)
        return 1

    def LeftDown(self):
        x, y = self._mk_()._pos
        self._post(0x0201, 1, x, y)
        return 1

    def LeftUp(self):
        x, y = self._mk_()._pos
        self._post(0x0202, 0, x, y)
        return 1

    def RightClick(self):
        mk = self._mk_()
        if hasattr(mk, "right_click"):
            mk.right_click(*mk._pos)
        else:
            x, y = mk._pos
            self._post(0x0204, 2, x, y)
            self._post(0x0205, 0, x, y)
        return 1

    def RightDown(self):
        x, y = self._mk_()._pos
        self._post(0x0204, 2, x, y)
        return 1

    def RightUp(self):
        x, y = self._mk_()._pos
        self._post(0x0205, 0, x, y)
        return 1

    def MiddleClick(self):
        mk = self._mk_()
        if hasattr(mk, "middle_click"):
            mk.middle_click()
        else:
            x, y = mk._pos
            self._post(0x0207, 0x10, x, y)
            self._post(0x0208, 0, x, y)
        return 1

    def MiddleDown(self):
        x, y = self._mk_()._pos
        self._post(0x0207, 0x10, x, y)
        return 1

    def MiddleUp(self):
        x, y = self._mk_()._pos
        self._post(0x0208, 0, x, y)
        return 1

    def WheelDown(self):
        self._wheel(-120)
        return 1

    def WheelUp(self):
        self._wheel(120)
        return 1

    def _wheel(self, delta):
        mk = self._mk_()
        if hasattr(mk, "wheel"):
            mk.wheel(up=delta > 0)
        else:
            x, y = mk._pos
            self._win.post_message(0x020A, (delta & 0xFFFF) << 16, (y & 0xFFFF) << 16 | (x & 0xFFFF))

    def _post(self, msg, wp, x, y):
        """裸按钮事件: dx 通道走 host 命令, 前台通道走 SendInput, 其余 PostMessage。"""
        if self._dxmk is not None:
            from .dx.host import M_LDOWN, M_LUP, M_MDOWN, M_MUP, M_RDOWN, M_RUP
            op = {0x0201: M_LDOWN, 0x0202: M_LUP, 0x0204: M_RDOWN,
                  0x0205: M_RUP, 0x0207: M_MDOWN, 0x0208: M_MUP}[msg]
            self._dx.mouse(x, y, op)
        elif self._mk is not None and hasattr(self._mk, "left_down"):
            getattr(self._mk, {0x0201: "left_down", 0x0202: "left_up",
                               0x0204: "right_down", 0x0205: "right_up",
                               0x0207: "middle_down", 0x0208: "middle_up"}[msg])()
        else:
            self._win.post_message(msg, wp, (y & 0xFFFF) << 16 | (x & 0xFFFF))

    def KeyPress(self, vk):
        if self._dxmk is not None:
            self._dxmk.key_tap(vk)
        elif self._mk is not None and hasattr(self._mk, "key_tap"):
            self._mk.key_tap(vk)
        else:
            raise NotImplementedError("windows 通道键盘历史死案, 用 keypad='dx'/'normal'")
        return 1

    def _need_dxmk(self):
        if self._dxmk is None:
            raise RuntimeError(
                "该接口需要 dx 键鼠通道(BindWindow mouse/keypad 含 'dx')")

    def KeyDown(self, vk):
        self._need_dxmk()
        self._dxmk.key_down(vk)
        return 1

    def KeyUp(self, vk):
        self._need_dxmk()
        self._dxmk.key_up(vk)
        return 1

    def KeyPressChar(self, key_str: str):
        vk, shift = _vk_of_char(key_str)
        if shift:
            self.KeyDown(0x10)
            self.KeyPress(vk)
            self.KeyUp(0x10)
        else:
            self.KeyPress(vk)
        return 1

    def KeyDownChar(self, key_str: str):
        vk, _shift = _vk_of_char(key_str)
        return self.KeyDown(vk)

    def KeyUpChar(self, key_str: str):
        vk, _shift = _vk_of_char(key_str)
        return self.KeyUp(vk)

    def KeyPressStr(self, key_str: str, delay=50):
        for ch in key_str:
            self.KeyPressChar(ch)
            time.sleep(delay / 1000)
        return 1

    def SendString(self, hwnd: int, str_: str):
        """向指定窗口打字(与绑定通道无关, 大漠语义)。三条键鼠通道都实现
        type_text; 未绑定(全 None)显式报错而非裸崩。"""
        if self._dxmk is not None:
            self._dxmk.type_text(str_)
        elif self._mk is not None:
            self._mk.type_text(str_)             # 前台: SendInput UNICODE 注字符
        else:
            raise RuntimeError("未 BindWindow, 无键鼠通道可发字符串")
        return 1

    def SendPaste(self, hwnd: int):
        """向绑定窗口模拟 Ctrl+V(大漠语义: 粘贴剪贴板现状, 不改写内容)。"""
        self._win.post_message(0x0302, 0, 0)               # WM_PASTE
        return 1

    def GetCursorPos(self):
        """虚拟光标位置(客户区坐标)。大漠语义: 绑定了键鼠通道返回注入跟踪位
        (反编译 GetCursorPos 辅助: dx 通道查组件虚拟位/宿主 ctx+0x50), 而非
        系统光标 —— 后台自动化时真人动鼠标不影响读数。"""
        if self._win is None:
            import ctypes
            import ctypes.wintypes as wt
            pt = wt.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            return pt.x, pt.y
        # 走到这说明 _mk_() 会因键鼠通道未绑直接 raise, 没有 None 回退路径
        return self._mk_()._pos

    # ================= 窗口(核心) =================
    def FindWindow(self, class_name, title="") -> int:
        w = GameWindow.find(class_name=class_name or None,
                            title_keyword=title or None)
        return w.hwnd if w else 0

    def FindWindowEx(self, parent, class_name, title="") -> int:
        w = GameWindow.find(class_name=class_name or None,
                            title_keyword=title or None, parent=parent or None)
        return w.hwnd if w else 0

    def GetWindowClass(self, hwnd) -> str:
        buf = __import__("ctypes").create_unicode_buffer(256)
        __import__("ctypes").windll.user32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def GetWindowTitle(self, hwnd) -> str:
        u = __import__("ctypes").windll.user32
        n = u.GetWindowTextLengthW(hwnd)
        buf = __import__("ctypes").create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def GetWindowRect(self, hwnd):
        w = GameWindow(hwnd)
        return w.window_rect()

    def GetClientRect(self, hwnd):
        x, y, w_, h_ = GameWindow(hwnd).client_rect()
        return x, y, x + w_, y + h_

    def GetClientSize(self, hwnd):
        _, _, w_, h_ = GameWindow(hwnd).client_rect()
        return w_, h_

    def ClientToScreen(self, hwnd, x, y):
        rx, ry, _, _ = GameWindow(hwnd).client_rect()
        return rx + x, ry + y

    def ScreenToClient(self, hwnd, x, y):
        rx, ry, _, _ = GameWindow(hwnd).client_rect()
        return x - rx, y - ry

    def MoveWindow(self, hwnd, x, y) -> int:
        import ctypes
        ctypes.windll.user32.SetWindowPos(hwnd, None, x, y, 0, 0, 0x0001 | 0x0004)
        return 1

    def GetWindowProcessId(self, hwnd) -> int:
        return GameWindow(hwnd).pid()

    def GetWindowProcessPath(self, hwnd) -> str:
        return GameWindow(hwnd).process_path()

    def GetForegroundWindow(self) -> int:
        import ctypes
        return ctypes.windll.user32.GetForegroundWindow() or 0

    # ================= 基本设置 =================
    def SetPath(self, path) -> int:
        vision.set_templates_dir(path)
        return 1

    def Ver(self) -> str:
        return "imgbot-dmsoft 0.1(大漠 7.2319 接口复刻, 阶段1+2)"

    def GetLastError(self) -> int:
        return self._last_error


def _vk_of_char(ch: str) -> tuple[int, bool]:
    """单字符 → (虚拟键码, 是否需 Shift), US 布局; 无映射抛错而非发 0xFF。"""
    import ctypes
    r = int(ctypes.windll.user32.VkKeyScanW(ord(ch)))
    if r == -1:
        raise ValueError(f"字符 {ch!r} 在当前键盘布局无虚拟键映射")
    return r & 0xFF, bool(r & 0x0100)   # 高字节 bit0 = 需 Shift, 不能被 & 0xFF 丢弃
