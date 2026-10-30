# -*- coding: utf-8 -*-
"""dx 注入通道宿主侧: 建共享内存 -> 注入 dmcore -> 心跳/命令 -> 取帧.

协议(与 dx/native/dmcore.c 同步改):
- 共享内存 "Local\\imgbotdx_{pid}", 32MB; 头 30 个 DWORD(120B), 帧区偏移 4096。
- 纯轮询: 宿主写 cmd+cmd_seq, DLL 执行后写 ack_result+ack_seq; 心跳=heartbeat
  计数持续增长; 帧=frame_seq 递增(BGRA/RGB8, 行序自底向上, 取出时翻正)。
命令: PING=1 BYE=2 HOOK_GL=3 BIND=4 KEY=5 MOUSE=6 RAWINFO=7 FAKE_ACTIVE=8
HIDE=9 HOOK_D3D9=10 KEYSTATE=11。版本号(第2 DWORD)改协议必须两端同步+1。
"""
from __future__ import annotations

import ctypes
import struct
import subprocess
import time
from pathlib import Path

import numpy as np

from ..win32.window import WindowLostError

k32 = ctypes.WinDLL("kernel32", use_last_error=True)

SHM_SIZE = 32 * 1024 * 1024
FRAME_OFF = 4096
MAGIC = 0x49425844
PAGE_READWRITE = 0x04
FILE_MAP_ALL_ACCESS = 0xF001F

CMD_PING, CMD_BYE, CMD_HOOK_GL = 1, 2, 3
CMD_BIND, CMD_KEY, CMD_MOUSE = 4, 5, 6
CMD_RAWINFO, CMD_FAKE_ACTIVE, CMD_HIDE, CMD_HOOK_D3D9 = 7, 8, 9, 10
CMD_KEYSTATE = 11
KEY_DOWN, KEY_UP, KEY_CHAR = 0, 1, 2
M_MOVE, M_LDOWN, M_LUP = 0, 1, 2
M_RDOWN, M_RUP, M_MDOWN, M_MUP = 3, 4, 5, 6
M_DBLCLK = 7
M_WHEEL_UP, M_WHEEL_DOWN = 9, 10
HOOK_ERRORS = {
    20: "目标未加载 opengl32", 21: "GL 函数解析失败",
    22: "SwapBuffers 导出解析失败", 31: "函数前导无热补丁哨兵(x64 未实现)",
    32: "蹦床分配失败", 33: "函数页保护失败",
    60: "目标未加载 d3d9", 61: "Direct3DCreate9 解析失败",
    62: "d3d9 函数前导无哨兵", 63: "蹦床分配失败", 64: "函数页保护失败",
    70: "user32 未加载", 71: "GetKeyState 解析失败",
    72: "GetKeyState 无哨兵(x64)", 73: "蹦床分配失败", 74: "页保护失败",
    75: "GetAsyncKeyState 无哨兵(x64)", 76: "蹦床分配失败", 77: "页保护失败",
}

k32.CreateFileMappingW.restype = ctypes.c_void_p
k32.CreateFileMappingW.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_wchar_p]
k32.OpenFileMappingW.restype = ctypes.c_void_p
k32.OpenFileMappingW.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_wchar_p]
k32.MapViewOfFile.restype = ctypes.c_void_p
k32.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                              ctypes.c_ulong, ctypes.c_size_t]
k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
k32.CloseHandle.argtypes = [ctypes.c_void_p]


def target_bitness(pid: int) -> str:
    """'32'/'64'; 探不到抛 OSError。"""
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        raise OSError(f"OpenProcess({pid}) 失败: {ctypes.get_last_error()}")
    try:
        wow = ctypes.c_int()
        if not k32.IsWow64Process(h, ctypes.byref(wow)):
            raise OSError("IsWow64Process 失败")
        return "32" if wow.value else "64"
    finally:
        k32.CloseHandle(h)


class DxHost:
    """一个目标进程一个实例: bind() 起全程, unbind() 收口。"""

    def __init__(self, pid: int):
        self.pid = pid
        self._map = None
        self._view = None
        bin_dir = Path(__file__).parent / "bin"
        self._dll = (bin_dir / f"dmcore{target_bitness(pid)}.dll").resolve()
        self._injector = bin_dir / f"injector{target_bitness(pid)}.exe"
        if not self._dll.exists() or not self._injector.exists():
            raise FileNotFoundError(
                f"注入件未构建: {bin_dir} (先跑 uv run dx/native/build.py)")

    # -- 生命周期 ------------------------------------------------------
    def bind(self, timeout: float = 5.0) -> None:
        """绑定目标进程(大漠 BindWindow 的进程侧): 旧实例在则 BYE 退场, 再注入新实例。"""
        self._bye_stale_worker()
        name = f"Local\\imgbotdx_{self.pid}"
        self._map = k32.CreateFileMappingW(-1, None, PAGE_READWRITE,
                                           0, SHM_SIZE, name)
        if not self._map:
            raise OSError(f"CreateFileMapping 失败: {ctypes.get_last_error()}")
        self._view = k32.MapViewOfFile(self._map, FILE_MAP_ALL_ACCESS, 0, 0, SHM_SIZE)
        if not self._view:
            raise OSError(f"MapViewOfFile 失败: {ctypes.get_last_error()}")
        struct.pack_into("<30I", (ctypes.c_char * SHM_SIZE).from_address(self._view),
                         0, MAGIC, 6, *([0] * 28))
        try:
            r = subprocess.run([str(self._injector), str(self.pid), str(self._dll)],
                               capture_output=True, text=True, timeout=30)
            out = r.stdout.strip()
            if not out.startswith("OK"):
                raise RuntimeError(f"注入失败: {out} {r.stderr.strip()}")
            rc = int(out.split("rc=")[1])
            if rc != 0:
                raise RuntimeError(
                    f"DxInit 返回 {rc}(5=线程/互斥体失败 6=旧实例未退干净, 重启目标进程可解)")
            self._wait(lambda: self.hdr[3] > 0, timeout,
                       "心跳未出现(注入后 DLL 未挂上共享内存)")
        except Exception:
            k32.UnmapViewOfFile(self._view)
            k32.CloseHandle(self._map)
            self._view = None
            self._map = None
            raise

    def _bye_stale_worker(self):
        """若旧驻留实例还在(上次会话没收口), 命令它解钩退出, 让位给新注入。"""
        map_h = k32.OpenFileMappingW(FILE_MAP_ALL_ACCESS, False,
                                     f"Local\\imgbotdx_{self.pid}")
        if not map_h:
            return
        view = k32.MapViewOfFile(map_h, FILE_MAP_ALL_ACCESS, 0, 0, SHM_SIZE)
        if not view:
            k32.CloseHandle(map_h)
            return
        try:
            magic = struct.unpack_from("<I", (ctypes.c_char * 4).from_address(view))[0]
            if magic != MAGIC:
                return
            # 旧 worker 活着才会应答; 死实例的残留 mapping 由引用计数自然消亡
            base = (ctypes.c_char * 120).from_address(view)
            cur = struct.unpack("<30I", bytes(base))
            struct.pack_into("<4I", base, 16, CMD_BYE, cur[5] + 1, cur[6], cur[7])
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                now = struct.unpack("<30I", bytes(base))
                if now[6] == cur[5] + 1:
                    time.sleep(0.15)          # 等它退线程/放互斥体
                    return
                time.sleep(0.02)
        finally:
            k32.UnmapViewOfFile(view)
            k32.CloseHandle(map_h)

    def unbind(self):
        """解绑(大漠 UnBindWindow 对应物): 命令 DLL 解钩退场后释放本侧映射。"""
        if self._view is None:
            return
        try:
            self._cmd(CMD_BYE, timeout=3.0)
            time.sleep(0.2)                    # 等 DLL 收口(state->0)
        finally:
            if self._view:
                k32.UnmapViewOfFile(self._view)
                self._view = None
            if self._map:
                k32.CloseHandle(self._map)
                self._map = None

    # -- 观测与命令 ----------------------------------------------------
    @property
    def hdr(self):
        """头 19 个 DWORD 活视图: [magic,ver,state,heart, cmd,seq,ack,res,
        fseq,w,h,fmt, swap_calls,gl_err,vp_w,vp_h, arg0,arg1, msg_filtered]"""
        if self._view is None:
            raise RuntimeError("未 attach")
        buf = (ctypes.c_char * 120).from_address(self._view)
        return struct.unpack("<30I", bytes(buf))

    def _cmd(self, cmd: int, arg0: int = 0, arg1: int = 0,
             timeout: float = 3.0) -> int:
        if self._view is None:
            raise RuntimeError("未 attach")
        base = (ctypes.c_char * 120).from_address(self._view)
        cur = struct.unpack("<30I", bytes(base))
        seq = cur[5]
        struct.pack_into("<2I", base, 64, arg0 & 0xFFFFFFFF, arg1 & 0xFFFFFFFF)
        struct.pack_into("<4I", base, 16, cmd, seq + 1, cur[6], cur[7])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            now = self.hdr
            if now[6] == seq + 1:
                return now[7]
            time.sleep(0.01)
        raise TimeoutError(f"命令 {cmd} 无应答(DLL 卡死或已卸载)")

    def ping(self) -> int:
        return self._cmd(CMD_PING)

    def hook_gl(self) -> int:
        rc = self._cmd(CMD_HOOK_GL)
        if rc != 0:
            raise RuntimeError(f"GL 钩子失败 rc={rc}: {HOOK_ERRORS.get(rc, '未知')}")
        return rc

    def hook_d3d9(self) -> int:
        """d3d9 图色钩子(时机: 目标此后才会调 Direct3DCreate9, 即先注入后开游戏)。"""
        rc = self._cmd(CMD_HOOK_D3D9)
        if rc != 0:
            raise RuntimeError(f"d3d9 钩子失败 rc={rc}: {HOOK_ERRORS.get(rc, '未知')}")
        return rc

    def hook_keystate(self) -> int:
        """键态钩子: user32!GetKeyState/GetAsyncKeyState 改读虚拟状态(大漠键盘死案
        解法 —— 轮询型输入游戏收不到合成消息, 钩子喂虚拟按住态)。仅 x86。"""
        rc = self._cmd(CMD_KEYSTATE)
        if rc != 0:
            raise RuntimeError(f"键态钩子失败 rc={rc}: {HOOK_ERRORS.get(rc, '未知')}")
        return rc

    def hook_auto(self) -> str:
        """按目标已加载的图形栈自动选钩子, 返回 'gl' / 'd3d9'; 失败抛 RuntimeError。"""
        rc = self._cmd(CMD_HOOK_GL)
        if rc == 0:
            return "gl"
        if rc != 20:                                  # 20=没有 opengl32 才值得试 d3d9
            raise RuntimeError(f"GL 钩子失败 rc={rc}: {HOOK_ERRORS.get(rc, '未知')}")
        rc9 = self._cmd(CMD_HOOK_D3D9)
        if rc9 != 0:
            raise RuntimeError(f"d3d9 钩子失败 rc={rc9}: {HOOK_ERRORS.get(rc9, '未知')}")
        return "d3d9"

    def bind_input(self, hwnd: int) -> None:
        """键鼠命令的目标窗口(CMD_BIND); 失败(窗口无效)抛 RuntimeError。"""
        rc = self._cmd(CMD_BIND, hwnd)
        if rc != 1:
            raise RuntimeError(f"BIND 失败: hwnd=0x{hwnd:X} 不是有效窗口")

    def key(self, vk: int, mode: int) -> None:
        """mode: 0=按下 1=抬起 2=WM_CHAR(vk 位传 UTF-16 码元)。"""
        self._check(self._cmd(CMD_KEY, vk, mode), CMD_KEY)

    def mouse(self, x: int, y: int, op: int) -> None:
        """op: 0=移动 1/2=左键按下/抬起 3/4=右键 5/6=中键 9/10=滚轮上/下。"""
        self._check(self._cmd(CMD_MOUSE, (y & 0xFFFF) << 16 | (x & 0xFFFF), op),
                    CMD_MOUSE)

    def fake_active(self, enable: bool = True) -> int:
        """假激活: 子类化绑定窗口, 吞失焦/失活族消息并合成一次激活(大漠 EnableFakeActive)。"""
        rc = self._cmd(CMD_FAKE_ACTIVE, 1 if enable else 0)
        if enable and rc not in (0, 1):    # 1=本次新开, 0=已开启(幂等)
            raise RuntimeError(f"假激活失败 rc={rc}(40=未BIND/窗口失效, 其他=SetWindowLongPtr 错误码)")
        return rc

    def hide_module(self) -> int:
        """模块隐藏: 本 DLL 从目标进程 PEB 模块链摘除(大漠 dx.public.hide.dll 简版)。"""
        rc = self._cmd(CMD_HIDE)
        if rc != 1:
            raise RuntimeError(f"模块隐藏失败 rc={rc}(50-53=PEB/链异常, 54=已摘链但句柄仍可解析)")
        return rc

    @property
    def msg_filtered(self) -> int:
        """假激活已吞掉的失焦/失活消息数。"""
        return self.hdr[18]

    @property
    def swap_calls(self) -> int:
        """换帧钩子被调用次数(GL/d3d9 共用; 0=钩子没被走到)。"""
        return self.hdr[12]

    @property
    def last_err(self) -> int:
        """钩子最后一次记录的错误码(面包屑)。"""
        return self.hdr[13]

    def rawinfo(self) -> dict:
        """目标进程 raw input 注册情况(诊断: 判断消息免疫根因)。"""
        self._cmd(CMD_RAWINFO)
        h = self.hdr
        return {"target_hwnd": h[16], "devices": h[17] >> 16,
                "with_target": h[17] & 0xFFFF}

    @staticmethod
    def _check(rc: int, cmd: int):
        if rc == 40:
            # 键鼠命令的"窗口无效/未BIND"归一化: 消费方按 WindowLostError 重绑
            raise WindowLostError(f"命令 {cmd} 失败 rc=40(未BIND或目标窗口已失效), 需重新绑定")
        if rc != 0:
            raise RuntimeError(f"命令 {cmd} 失败 rc={rc}")

    def frame_info(self) -> tuple[int, int, int, int]:
        """(frame_seq, w, h, fmt); frame_seq 不再增长=渲染停了。"""
        h = self.hdr
        return h[8], h[9], h[10], h[11]

    def capture(self, fresh_wait: float = 1.0) -> np.ndarray:
        """取最新帧为 BGR ndarray; fresh_wait 内无新帧则取现存帧(可能旧)。"""
        if self._view is None:
            raise RuntimeError("未 attach")
        deadline = time.monotonic() + fresh_wait
        seq0 = self.frame_info()[0]
        while time.monotonic() < deadline:
            seq, w, h, fmt = self.frame_info()
            if seq != seq0 and w:
                break
            time.sleep(0.02)
        else:
            seq, w, h, fmt = self.frame_info()
        if not w or not h:
            raise RuntimeError("尚无帧(渲染没跑或钩子未挂)")
        n = 4 if fmt == 1 else 3
        for _ in range(3):                          # 拷后复核 seq, 防单槽撕裂
            buf = (ctypes.c_char * (w * h * n)).from_address(self._view + FRAME_OFF)
            img = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, n)
            img = img[::-1]                                   # GL 行序自底向上, 翻正
            img = img[:, :, :3]
            if fmt == 2:                                      # GL_RGB: 通道序反成 BGR
                img = img[:, :, ::-1]
            img = np.ascontiguousarray(img)
            if self.frame_info()[0] == seq:                   # 拷贝期间无新帧落地
                return img
            seq, w, h, fmt = self.frame_info()                # 有新帧, 按新头重拷
            n = 4 if fmt == 1 else 3
        return img                                             # 高刷下三次未稳, 取最后拷贝

    # -- 内部 ----------------------------------------------------------
    def _wait(self, cond, timeout: float, what: str):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cond():
                return
            time.sleep(0.02)
        raise TimeoutError(what)
