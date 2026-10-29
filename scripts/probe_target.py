# -*- coding: utf-8 -*-
"""[一次性脚手架] 阶段1 侦察: 找模拟器/游戏窗口, 报 PID/位宽/子窗口(内层渲染窗)/已加载图形模块.

用法: uv run scripts/probe_target.py [进程名或标题关键字]
"""
import ctypes
import ctypes.wintypes as wt
import sys

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32 = ctypes.WinDLL("user32", use_last_error=True)

TH32CS_SNAPPROCESS, TH32CS_SNAPMODULE, TH32CS_SNAPMODULE32 = 0x2, 0x8, 0x10
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)), ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD), ("szExeFile", wt.WCHAR * 260)]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("th32ModuleID", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("GlblcntUsage", wt.DWORD), ("ProccntUsage", wt.DWORD), ("modBaseAddr", ctypes.c_void_p),
                ("modBaseSize", wt.DWORD), ("hModule", ctypes.c_void_p),
                ("szModule", wt.WCHAR * 256), ("szExePath", wt.WCHAR * 260)]


def processes():
    """[(pid, exe名)] 全进程表。"""
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    e = PROCESSENTRY32W()
    e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    out = []
    if k32.Process32FirstW(snap, ctypes.byref(e)):
        while True:
            out.append((e.th32ProcessID, e.szExeFile))
            if not k32.Process32NextW(snap, ctypes.byref(e)):
                break
    k32.CloseHandle(snap)
    return out


def modules(pid):
    """目标进程已加载模块名列表(小写); 拿不到(权限)返回 None。"""
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snap in (0, -1):
        return None
    e = MODULEENTRY32W()
    e.dwSize = ctypes.sizeof(MODULEENTRY32W)
    out = []
    if k32.Module32FirstW(snap, ctypes.byref(e)):
        while True:
            out.append(e.szModule.lower())
            if not k32.Module32NextW(snap, ctypes.byref(e)):
                break
    k32.CloseHandle(snap)
    return out


def bitness(pid):
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return "?"
    try:
        wow = wt.BOOL()
        if not k32.IsWow64Process(h, ctypes.byref(wow)):
            return "?"
        return "32位(WOW64)" if wow.value else "64位"
    finally:
        k32.CloseHandle(h)


EMU_KEYS = ("dnplayer", "ldplayer", "nox", "mumu", "nemu", "memu",
            "bluestacks", "hd-player", "雷电")
SKIP_CLASS = ("Windows.UI.Core.CoreWindow",)   # 同进程的通知类杂窗


def main():
    from imgbot.win32.window import list_windows
    keyword = (sys.argv[1].lower() if len(sys.argv) > 1 else "")
    procs = {pid: name for pid, name in processes()}
    print("== 候选窗口(可见+有标题) ==")
    cands = []
    for hwnd, cname, title in list_windows():
        if cname in SKIP_CLASS:
            continue
        pid = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pname = procs.get(pid.value, "?").lower()
        hit = keyword and (keyword in pname or keyword in title.lower())
        emu = any(k in pname or k in title.lower() for k in EMU_KEYS)
        if hit or emu:
            cands.append((hwnd, pname, pid.value, cname, title))
            print(f"hwnd=0x{hwnd:X} pid={pid.value}({pname}) 位宽={bitness(pid.value)}\n"
                  f"    class={cname!r} title={title!r}")
    if not cands:
        print("(没找到模拟器窗口 — 开了没? 或用参数指定关键字)")
        return
    hwnd, pname, pid, cname, title = max(
        cands, key=lambda c: ("MainFrame" in c[3]) + (len(c[4]) > 0))
    print(f"\n== 深查首选: {title!r} pid={pid} ==")
    print(f"位宽: {bitness(pid)}")
    kids = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def on_child(h, _):
        buf = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(h, buf, 256)
        kids.append((h, buf.value))
        return True

    u32.EnumChildWindows(hwnd, on_child, 0)
    for h, c in kids:
        print(f"  子窗口 0x{h:X} class={c!r}")
    mods = modules(pid)
    if mods is None:
        print("  模块表拿不到(权限/位宽)")
        return
    watch = ("d3d9.dll", "d3d11.dll", "dxgi.dll", "opengl32.dll", "vulkan-1.dll",
             "dinput8.dll", "dinput.dll", "ddraw.dll", "angle", "egl")
    loaded = [m for m in mods if any(w in m for w in watch)]
    print("  图形/输入相关模块:", ", ".join(loaded) or "(无)")


if __name__ == "__main__":
    main()
