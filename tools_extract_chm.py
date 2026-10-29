# -*- coding: utf-8 -*-
"""从反编译 CHM 页面提取纯文本(GBK/Word 导出 HTML), 用法: uv run tools_extract_chm.py <htm> [max_lines]"""
import html
import re
import sys


def main():
    raw = open(sys.argv[1], "rb").read()
    text = None
    for enc in ("gb18030", "gbk", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    text = re.sub(r"<[^>]+>", "\n", text)
    text = html.unescape(text)
    lines = [l.strip() for l in text.split("\n")
             if l.strip() and not l.strip().startswith(("mso-", "table.", "{", "}", "/*", "Normal", "User"))]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 400
    print("\n".join(lines[:limit]))


if __name__ == "__main__":
    main()
