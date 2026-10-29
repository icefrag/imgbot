# -*- coding: utf-8 -*-
"""[一次性脚手架] 查 PE 导出: 是否转发、函数前导字节。用法:
uv run scripts/inspect_export.py <dll路径> <函数名> [函数名...]
"""
import struct
import sys


def sections(data, pe):
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    optsz = struct.unpack_from("<H", data, pe + 20)[0]
    sec0 = pe + 24 + optsz
    out = []
    for i in range(nsec):
        nm, vsz, va, rsz, rawoff = struct.unpack_from("<8sIIII", data, sec0 + i * 40)
        out.append((va, max(vsz, rsz), rawoff))
    return out


def main():
    path = sys.argv[1]
    data = open(path, "rb").read()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    secs = sections(data, pe)

    def r2o(rva):
        for va, sz, off in secs:
            if va <= rva < va + sz:
                return rva - va + off
        return None

    magic = struct.unpack_from("<H", data, pe + 24)[0]
    ddir = pe + 24 + (112 if magic == 0x20B else 96)
    exp_rva, exp_sz = struct.unpack_from("<II", data, ddir)
    o = r2o(exp_rva)
    aof, aon, aoo = struct.unpack_from("<III", data, o + 28)
    nnam = struct.unpack_from("<I", data, o + 24)[0]
    by_name = {}
    for i in range(nnam):
        nrva = struct.unpack_from("<I", data, r2o(aon) + i * 4)[0]
        s = r2o(nrva)
        name = data[s:data.index(b"\x00", s)].decode()
        ordn = struct.unpack_from("<H", data, r2o(aoo) + i * 2)[0]
        by_name[name] = struct.unpack_from("<I", data, r2o(aof) + ordn * 4)[0]
    for func in sys.argv[2:]:
        frva = by_name.get(func)
        if frva is None:
            print(f"{func}: 不存在")
            continue

        off = r2o(frva)
        print(f"{func}: rva=0x{frva:X} 前导 {data[off:off + 12].hex(' ')}")


if __name__ == "__main__":
    main()
