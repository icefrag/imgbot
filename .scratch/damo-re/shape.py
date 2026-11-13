# -*- coding: utf-8 -*-
import struct
p = r"C:\Windows\SysWOW64\user32.dll"
data = open(p, "rb").read()
pe = struct.unpack_from("<I", data, 0x3C)[0]
nsec = struct.unpack_from("<H", data, pe + 6)[0]
optsz = struct.unpack_from("<H", data, pe + 20)[0]
secs = []
so = pe + 24 + optsz
for i in range(nsec):
    o = so + 40 * i
    vs, va, rs, ra = struct.unpack_from("<IIII", data, o + 8)
    secs.append((va, vs, ra, rs))
def r2o(rva):
    for va, vs, ra, rs in secs:
        if va <= rva < va + max(vs, rs):
            return ra + (rva - va)
    return None
dd = pe + 24 + 96
exp_rva, exp_sz = struct.unpack_from("<II", data, dd)
eo = r2o(exp_rva)
base = struct.unpack_from("<I", data, eo + 16)[0]
nnames = struct.unpack_from("<I", data, eo + 24)[0]
funs_o = r2o(struct.unpack_from("<I", data, eo + 28)[0])
ords_o = r2o(struct.unpack_from("<I", data, eo + 32)[0])
names_o = r2o(struct.unpack_from("<I", data, eo + 36)[0])
NUL = bytes([0])
want = {"GetCapture", "GetCursorInfo", "ClipCursor", "SetCapture", "ReleaseCapture",
        "PeekMessageW", "PeekMessageA", "GetMessageW", "GetMessageA", "GetCursorPos",
        "GetKeyState", "GetAsyncKeyState", "GetFocus", "GetActiveWindow",
        "GetForegroundWindow", "GetGUIThreadInfo", "SendInput", "ShowCursor"}
found = 0
for i in range(nnames):
    no = names_o + 4 * i
    end = data.index(NUL, no)
    nm = data[no:end].decode("latin-1")
    if nm not in want:
        continue
    found += 1
    ordv = struct.unpack_from("<H", data, ords_o + 2 * i)[0]
    rva = struct.unpack_from("<I", data, funs_o + 4 * (ordv - base))[0]
    if exp_rva <= rva < exp_rva + exp_sz:
        fo = r2o(rva)
        fwd = data[fo:fo + 40].split(NUL)[0].decode("latin-1")
        print(f"{nm:22s} FORWARDER -> {fwd}")
        continue
    fo = r2o(rva)
    if fo is None:
        print(f"{nm:22s} rva={rva:08x} UNMAPPED")
        continue
    print(f"{nm:22s} rva={rva:06x}  {data[fo:fo + 12].hex(' ')}")
print(f"matched {found}/{len(want)}")

print("--- debug: first 6 names ---")
for i in range(6):
    no = names_o + 4 * i
    end = data.index(NUL, no)
    print(i, data[no:end])

print("--- debug: EAT raw ---")
print("exp_rva", hex(exp_rva), "eo", hex(eo) if eo else None)
print("base", base, "nnames", nnames,
      "funs_rva", hex(struct.unpack_from('<I', data, eo+28)[0]),
      "ords_rva", hex(struct.unpack_from('<I', data, eo+32)[0]),
      "names_rva", hex(struct.unpack_from('<I', data, eo+36)[0]))
print("raw eo..eo+40:", data[eo:eo+40].hex(' '))
