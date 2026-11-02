# -*- coding: utf-8 -*-
"""潜影 SHM 协议单源: 宿主(host.py)与注入件(dmcore.c)消费同一份定义。

dmcore.c 不直接写这份 Python —— 构建时 build.py 由本文件生成 proto.gen.h
(照 obfstrings.gen.h 先例)。协议扩展只改本文件 + 两端重新构建; 改协议必须
PROTO_VERSION +1, 心跳握手会拒掉旧版 DLL(大漠 0x8b0 段命令漂移的教训:
双份手写常量终会失同步, 单源+生成断掉这条路)。
"""
from __future__ import annotations

SHM_MAGIC = 0x49425844            # "DXBI"
PROTO_VERSION = 8
SHM_SIZE = 32 * 1024 * 1024       # 32MB
FRAME_OFF = 4096                  # 帧区在 SHM 内的偏移

# 命令码: 宿主写 cmd/cmd_seq/arg 槽, DLL 执行后写 ack_result/ack_seq 应答
CMD_PING = 1                      # 连通探测(应答 0x50494731)
CMD_BYE = 2                       # 解钩退场(unhook 全家 + state->0)
CMD_HOOK_GL = 3                   # opengl32!SwapBuffers hotpatch 截帧
CMD_BIND = 4                      # arg0=hwnd: 键鼠命令的目标窗口
CMD_KEY = 5                       # arg0=vk, arg1=mode(见 KEY_*)
CMD_MOUSE = 6                     # arg0=(y<<16)|x 客户区, arg1=op(见 M_*)
CMD_RAWINFO = 7                   # 诊断: raw input 注册情况, 结果写 arg0/arg1
CMD_FAKE_ACTIVE = 8               # arg0=1/0: 子类化目标窗口吞失焦/失活(假激活)
CMD_HIDE = 9                      # 从 PEB 模块链摘除本 DLL; 结果落 arg1
CMD_HOOK_D3D9 = 10                # d3d9 vtable 钩子(挂好后再调 Direct3DCreate9)
CMD_KEYSTATE = 11                 # 钩 GetKeyState/GetAsyncKeyState 喂虚拟键态
CMD_MOUSE_LOCK = 12               # 钩 GetCursorPos 返回虚拟光标
CMD_MOUSE_PACK = 13               # dx 鼠标全家桶一键武装; arg1=武装位图

# CMD_KEY 的 mode
KEY_DOWN = 0
KEY_UP = 1
KEY_CHAR = 2                      # arg0 传 UTF-16 码元

# CMD_MOUSE 的 op
M_MOVE = 0
M_LDOWN = 1
M_LUP = 2
M_RDOWN = 3
M_RUP = 4
M_MDOWN = 5
M_MUP = 6
M_DBLCLK = 7                      # WM_LBUTTONDBLCLK(第二击双击语义)
M_WHEEL_UP = 9
M_WHEEL_DOWN = 10

# SHM 头布局(全 DWORD, 偏移=按序累计; build.py 由此生成 C 的 Header 结构体)
# frame_fmt: 1=BGRA8 行序自底向上(宿主侧翻正), 2=RGB8 自底向上
HEADER_FIELDS = [
    ("magic", 1), ("version", 1), ("state", 1), ("heartbeat", 1),
    ("cmd", 1), ("cmd_seq", 1), ("ack_seq", 1), ("ack_result", 1),
    ("frame_seq", 1), ("frame_w", 1), ("frame_h", 1), ("frame_fmt", 1),
    ("swap_calls", 1), ("gl_err", 1), ("vp_w", 1), ("vp_h", 1),
    ("arg0", 1), ("arg1", 1),                     # 命令参数/结果槽(多路复用)
    ("msg_filtered", 1),                           # 假激活吞掉的消息数
    ("crash_code", 1), ("crash_addr", 1), ("self_base", 1),   # VEH 首例(参考)
    ("ring", 8),                                   # VEH 环形: 最近 4 例 (code, addr)
]

# 展平名表(数组字段拆成 name0..nameN): _hdr 快照的键, 与 30 个 DWORD 一一对应
FIELD_NAMES: list[str] = []
for _n, _c in HEADER_FIELDS:
    FIELD_NAMES.extend([_n] if _c == 1 else [f"{_n}{i}" for i in range(_c)])
OFF: dict[str, int] = {}                          # 字段名 → 字节偏移(数组字段给首元素)
_off = 0
for _n, _c in HEADER_FIELDS:
    OFF[_n] = _off
    _off += 4 * _c
HEADER_SIZE = _off                                 # 120 = 30 DWORD
