# -*- coding: utf-8 -*-
"""d3d9 帧二崩溃诊断: 读 shm 诊断区, 定位哪个间接调用跳进了帧数据区。

诊断区(shm+2048, DWORD 序号): [0]=Eip [1..15]=栈转储  [32]=蹦床
[48]=vt17 [49]=g_realPresent[0]现值 [50]=影子原值 [51]=vt [52]=dev [53]=lastCallTarget
解读: Eip==[53] → 该 call 位点跳飞; [49]!=[50] → g_realPresent 被踩;
[48] 是否落在 DLL 基址范围内 → vt17 还指不指 our_present。
"""
import ctypes
import struct
import subprocess
import sys
import time
from pathlib import Path

from imgbot.dx import DxHost
from imgbot.win32.window import GameWindow

BIN = Path(__file__).parent.parent / "imgbot" / "dx" / "bin"


def list_modules(pid):
    """64 位宿主枚举 32 位目标进程模块(64 位 MODULEENTRY32W 布局)。"""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class ME32(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_uint32), ("th32ModuleID", ctypes.c_uint32),
                    ("th32ProcessID", ctypes.c_uint32), ("GlblcntUsage", ctypes.c_uint32),
                    ("ProccntUsage", ctypes.c_uint32), ("modBaseAddress", ctypes.c_void_p),
                    ("modBaseSize", ctypes.c_uint32), ("hModule", ctypes.c_void_p),
                    ("szModule", ctypes.c_wchar * 256),
                    ("szExePath", ctypes.c_wchar * 260)]

    me = ME32()
    me.dwSize = ctypes.sizeof(ME32)
    snap = k32.CreateToolhelp32Snapshot(0x8 | 0x10, pid)   # MODULE|MODULE32
    if snap in (-1, 0xFFFFFFFFFFFFFFFF, 0):
        return []
    mods = []
    k32.Module32FirstW(snap, ctypes.byref(me))
    while True:
        mods.append((me.szModule, me.modBaseAddress or 0, me.modBaseSize))
        if not k32.Module32NextW(snap, ctypes.byref(me)):
            break
    k32.CloseHandle(snap)
    return mods


def diag_words(host, n=160):
    buf = (ctypes.c_char * (4 * n)).from_address(host._view + 2048)
    return struct.unpack(f"<{n}I", bytes(buf))


def hx(v):
    return f"0x{v:08X}"


def main():
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    mask = int(pos[0], 0) if pos else 0                    # 0=全开(默认行为)
    nohook = "--nohook" in flags
    app = subprocess.Popen([str(BIN / "d3d9test32.exe")])
    try:
        win = None
        for _ in range(30):
            win = GameWindow.find(class_name="imgbot_d3d9test")
            if win:
                break
            time.sleep(0.2)
        if not win:
            print("测试靶窗口没出现")
            return 1
        host = DxHost(app.pid)
        host.bind()
        try:
            struct.pack_into("<I", (ctypes.c_char * 4).from_address(
                host._view + 2048 + 400), 0, mask)            # DIAG(100)=掩码
            if "--novt17" in sys.argv:
                struct.pack_into("<I", (ctypes.c_char * 4).from_address(
                    host._view + 2048 + 404), 0, 1)         # DIAG(101)=跳过vt17补丁
            if nohook:
                print("nohook 模式: 只注入不挂钩, 纯环境对照, 等 12 秒")
                time.sleep(12)
                print(f"nohook -> {'存活' if app.poll() is None else '死了'}")
                return 0
            host.hook_d3d9()
            mods = list_modules(app.pid)               # 崩前拿模块表(基址对快照)
            print(f"hook_d3d9 OK mask={mask:#x}, 等帧流/崩溃... ({len(mods)} 模块)")
            deadline = time.time() + 15
            while time.time() < deadline:
                h = host._hdr()
                if h["crash_addr"]:
                    break
                if h["swap_calls"] > 4:           # Present 活过 4 帧 = 该掩码下没崩
                    break
                time.sleep(0.2)
            time.sleep(0.5)                   # 让 VEH 落笔
            h = host._hdr()
            d = diag_words(host)
            verdict = (f"CRASH @{hx(d[0])}" if h["crash_addr"]
                       else f"OK(swap_calls={h['swap_calls']})")
            print(f"mask={mask:#x} -> {verdict} frame_seq={h['frame_seq']} err={hx(h['gl_err'])}")
            eip = d[0]
            if not h["crash_addr"]:
                return 0
            print(f"crash: code={hx(h['crash_code'])} addr={hx(h['crash_addr'])} self_base={hx(h['self_base'])}")
            print(f"ring: {[(hx(h[f'ring{i*2}']), hx(h[f'ring{i*2+1}'])) for i in range(4)]}")
            print(f"Eip={hx(eip)} 崩溃线程={d[16]} present线程={d[55]} Esp={hx(d[17])} "
                  f"enterCount={d[23]}(diag59={d[59]})")
            n = d[62]
            if n:
                print(f"真实栈界: base={hx(d[60])} limit={hx(d[61])} "
                      f"({(d[60]-d[61])//1024}KB) 栈顶回溯 {n} 条:")
                for i in range(min(n, 12)):
                    v, m = d[64 + i*2], d[65 + i*2]
                    print(f"  [{i:2}] {hx(v)} (+{v-m:#x} @ {hx(m)})")
            # ---- 快照离线分析: Ebp 链走真实调用栈 ----
            snap = struct.unpack_from("<16I", (ctypes.c_char * 64).from_address(
                host._view + 0x200000))
            eip, esp, ebp = snap[0], snap[1], snap[2]
            sb, sl = d[60], d[61]
            stack = (ctypes.c_char * 0x10000).from_address(
                host._view + 0x200000 + 64)          # [copyFrom, sb)
            copy_from = max(sb - 0x10000, sl)
            sbytes = bytes(stack)
            def sget(addr, cnt=4):                    # 读快照栈上地址
                off = addr - copy_from
                return struct.unpack_from("<I", sbytes, off)[0] if 0 <= off <= 0x10000-cnt else None
            print(f"快照寄存器: Eip={hx(eip)} Esp={hx(esp)} Ebp={hx(ebp)} "
                  f"Eax={hx(snap[3])} Ebx={hx(snap[4])} Ecx={hx(snap[5])} "
                  f"Edx={hx(snap[6])} Esi={hx(snap[7])} Edi={hx(snap[8])}")
            def where(v):
                for name, base, size in mods:
                    if base and base <= v < base + size:
                        return f"{name}+{v-base:#x}"
                return None
            print("Ebp 链调用栈:")
            cur, depth = ebp, 0
            while cur and depth < 24:
                nxt = sget(cur)
                ret = sget(cur + 4)
                w = where(ret) if ret else None
                print(f"  #{depth:2} ebp={hx(cur)} ret={hx(ret) if ret else '?'}"
                      f"{f' ({w})' if w else ''}")
                if not nxt or nxt <= cur or nxt > sb:
                    break
                cur, depth = nxt, depth + 1
            # Eip 处 256 字节(快照尾段)
            eipz = bytes((ctypes.c_char * 256).from_address(
                host._view + 0x200000 + 64 + 0x10000))
            print("Eip 前后字节(Eip-128..+128):")
            for k in range(0, 256, 16):
                print(f"  {eip-128+k:08X}: " + " ".join(f"{x:02X}" for x in eipz[k:k+16]))
            print(f"Eip所在模块={'无(裸地址)' if not d[18] else hx(d[18])}")
            for who, off in (("Eip", 24), ("Esp", 28)):
                t, pr, ab, st = d[off], d[off+1], d[off+2], d[off+3]
                ty = {0x20000: "PRIVATE", 0x40000: "MAPPED", 0x1000000: "IMAGE"}.get(t, hx(t))
                print(f"{who}区块: type={ty} protect={hx(pr)} "
                      f"allocBase={hx(ab) if ab else '-'} state={hx(st)}")
            print(f"栈转储[1..15]: {[hx(x) for x in d[1:16]]}")
            print(f"蹦床={hx(d[32])}")
            names = {48: "vt17", 49: "realPresent现值", 50: "影子原值",
                     51: "vt", 52: "dev", 53: "lastCallTarget",
                     54: "shm基址", 56: "sysmem像素pBits", 57: "backbuffer",
                     58: "sysSurf对象"}
            for i, name in names.items():
                print(f"[{2048 + i*4}] {name} = {hx(d[i])}")
            # 自动解读
            lct = d[53]
            base = h[21]
            if eip == lct:
                print(f">>> Eip == lastCallTarget: 崩在这条间接调用上, 目标={hx(lct)}")
            if d[49] != d[50] and d[50]:
                print(f">>> g_realPresent 被踩: {hx(d[50])} -> {hx(d[49])}")
            shm = d[54]
            regions = [
                ("shm帧区(640x480)", shm + 4096, shm + 4096 + 640*480*4),
                ("shm整段(32MB)", shm, shm + 32*1024*1024),
                ("sysmem像素区", d[56], d[56] + 640*480*4 if d[56] else 0),
            ]
            for name, lo, hi in regions:
                if lo and lo <= eip < hi:
                    print(f">>> Eip 落在 {name}: +{eip - lo:#x}")
            if base <= d[48] < base + 0x30000:
                print(">>> vt17 仍在 DLL 范围内(应仍指 our_present)")
            else:
                print(f">>> vt17 不在 DLL 范围: {hx(d[48])}")
            return 0
        finally:
            try:
                host.unbind()
            except TimeoutError:
                pass
    finally:
        app.terminate()


if __name__ == "__main__":
    sys.exit(main())
