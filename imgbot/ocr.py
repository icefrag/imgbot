"""通用 OCR 文字定位: RapidOCR 单例引擎 + 长关键词按序比例匹配.

决策树第1条: 要找的目标是文字就用 OCR. 关键词规矩: ≥4字、去标点;
SequenceMatcher 按序比例匹配容忍个别错字(实测: 7字关键词错1字仍 0.83✓, 2字短词上限 0.44✗).
fast=True 走 max 快引擎: 只给"大字窄条"场景(板面标题行), 小字标签会漏读 — 默认别开.
"""
from __future__ import annotations

from difflib import SequenceMatcher

_ocr = None
_ocr_fast = None


def _engine():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    return _ocr


def _engine_fast():
    """det_limit_type='max' 的快引擎: 小图不被放大, 窄条OCR 1553ms→250ms(实测).

    只给"大字窄条"场景用(板面标题行) — parity 实测它对任务窗/大地图的小字标签会整行漏读,
    所以全局默认引擎保持 min 不动(宁可慢不可漏), 双引擎并存.
    """
    global _ocr_fast
    if _ocr_fast is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_fast = RapidOCR(det_limit_side_len=736, det_limit_type='max')
    return _ocr_fast


def engine(fast: bool = False):
    """取 OCR 单例引擎(消费项目的等级读取等可直接用, 免各自建单例)."""
    return _engine_fast() if fast else _engine()


def _run_ocr(screen_bgr, region, fast: bool = False):
    """裁区域→引擎推理→[(中心x, 中心y, 文本)]; region=None 时全屏."""
    off_x, off_y = 0, 0
    if region:
        off_x, off_y, x2, y2 = region
        screen_bgr = screen_bgr[off_y:y2, off_x:x2]
    result, _ = engine(fast)(screen_bgr)
    out = []
    for box, text, _conf in result or []:
        xs = [int(p[0]) for p in box]
        ys = [int(p[1]) for p in box]
        out.append((off_x + (min(xs) + max(xs)) // 2,
                    off_y + (min(ys) + max(ys)) // 2, text))
    return out


def best_match(candidates, keyword: str, min_ratio: float = 0.7):
    """candidates=[(x, y, text)] 里选与 keyword 最像且 ≥min_ratio 的, 返回(x, y, 相似度)或 None. 纯逻辑可测."""
    best = None
    for x, y, text in candidates:
        ratio = SequenceMatcher(None, keyword, text.strip()).ratio()
        if ratio >= min_ratio and (best is None or ratio > best[2]):
            best = (x, y, ratio)
    return best


def find_lines(screen_bgr, region=None, fast: bool = False):
    """OCR 全部文本行, 返回 [(中心x, 中心y, 文本)], 无筛选 — 调用方自己做包含/正则匹配."""
    return _run_ocr(screen_bgr, region, fast)


def raw_texts(screen_bgr, region=None, fast: bool = False):
    """OCR 原始文本列表 [(文本, 置信度)], 调用方自己做正则/数值解析(如读面板计数器)."""
    off_x, off_y = 0, 0
    if region:
        off_x, off_y, x2, y2 = region
        screen_bgr = screen_bgr[off_y:y2, off_x:x2]
    result, _ = engine(fast)(screen_bgr)
    return [(text, conf) for _, text, conf in result or []]


def find_texts(screen_bgr, keyword: str, region=None, min_ratio: float = 0.7):
    """OCR 找**所有**像 keyword 的文字(同名按钮/多处命中), 按 y 排序返回 [(x, y, 相似度)]."""
    cands = _run_ocr(screen_bgr, region)
    out = []
    for x, y, text in cands:
        ratio = SequenceMatcher(None, keyword, text.strip()).ratio()
        if ratio >= min_ratio:
            out.append((x, y, ratio))
    out.sort(key=lambda t: t[1])
    return out


def find_text(screen_bgr, keyword: str, region=None, min_ratio: float = 0.7):
    """OCR 在 region(None=全屏)内找最像 keyword 的文字, 返回(中心x, 中心y, 相似度)或 None."""
    return best_match(_run_ocr(screen_bgr, region), keyword, min_ratio)
