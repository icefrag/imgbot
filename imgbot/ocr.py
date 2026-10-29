"""通用 OCR 文字定位: RapidOCR 单例引擎 + 长关键词按序比例匹配.

决策树第1条: 要找的目标是文字就用 OCR. 关键词规矩: ≥4字、去标点;
SequenceMatcher 按序比例匹配容忍个别错字(实测: 7字关键词错1字仍 0.83✓, 2字短词上限 0.44✗).
fast=True 走 max 快引擎: 只给"大字窄条"场景(板面标题行), 小字标签会漏读 — 默认别开.
"""
from __future__ import annotations

from difflib import SequenceMatcher

_engines: dict[bool, object] = {}


# onnxruntime 每模型推理线程数(2026-10-28): 默认-1=吃满全部核——16核机单次全屏OCR
# 3s墙钟烧23s CPU(7.7x放大), 多开worker各自OCR互相叠加直接把CPU打满(实测主控558%),
# 而模拟器本体才各~1核. 封顶后放大倍数≈线程数.
ORT_INTRA_THREADS = 2


def engine(fast: bool = False):
    """取 OCR 单例引擎(消费项目的等级读取等可直接用, 免各自建单例).

    fast=True 走 det_limit_type='max' 的快引擎: 小图不被放大, 窄条OCR 1553ms→250ms(实测).
    只给"大字窄条"场景用(板面标题行) — parity 实测它对任务窗/大地图的小字标签会整行漏读,
    所以全局默认引擎保持 min 不动(宁可慢不可漏), 双引擎并存.
    """
    if fast not in _engines:
        from rapidocr_onnxruntime import RapidOCR
        # 必须走**无前缀Global键**: 本版rapidocr的UpdateParameters有bug——带前缀的
        # det_/rec_线程键会被 Global(-1) 反向覆盖; 只有Global键经 update_global_to_module
        # 复制进三个模型段才生效(2026-10-28 实测 det_前缀传了等于没传)
        caps = dict(intra_op_num_threads=ORT_INTRA_THREADS)
        _engines[fast] = RapidOCR(det_limit_side_len=736, det_limit_type='max', **caps) if fast \
            else RapidOCR(**caps)
    return _engines[fast]


def _crop(screen_bgr, region):
    """裁 region=(x1,y1,x2,y2) 并返回 (子图, 偏移x, 偏移y); region=None 时全屏."""
    if not region:
        return screen_bgr, 0, 0
    off_x, off_y, x2, y2 = region
    return screen_bgr[off_y:y2, off_x:x2], off_x, off_y


def _run_ocr(screen_bgr, region, fast: bool = False):
    """裁区域→引擎推理→[(中心x, 中心y, 文本)]; region=None 时全屏."""
    img, off_x, off_y = _crop(screen_bgr, region)
    result, _ = engine(fast)(img)
    out = []
    for box, text, _conf in result or []:
        xs = [int(p[0]) for p in box]
        ys = [int(p[1]) for p in box]
        out.append((off_x + (min(xs) + max(xs)) // 2,
                    off_y + (min(ys) + max(ys)) // 2, text))
    return out


def _ratio(keyword: str, text: str) -> float:
    """关键词与文本的按序比例相似度(容忍个别错字)."""
    return SequenceMatcher(None, keyword, text.strip()).ratio()


def best_match(candidates, keyword: str, min_ratio: float = 0.7):
    """candidates=[(x, y, text)] 里选与 keyword 最像且 ≥min_ratio 的, 返回(x, y, 相似度)或 None. 纯逻辑可测."""
    best = None
    for x, y, text in candidates:
        ratio = _ratio(keyword, text)
        if ratio >= min_ratio and (best is None or ratio > best[2]):
            best = (x, y, ratio)
    return best


def find_lines(screen_bgr, region=None, fast: bool = False):
    """OCR 全部文本行, 返回 [(中心x, 中心y, 文本)], 无筛选 — 调用方自己做包含/正则匹配."""
    return _run_ocr(screen_bgr, region, fast)


def raw_texts(screen_bgr, region=None, fast: bool = False):
    """OCR 原始文本列表 [(文本, 置信度)], 调用方自己做正则/数值解析(如读面板计数器)."""
    img, _, _ = _crop(screen_bgr, region)
    result, _ = engine(fast)(img)
    return [(text, conf) for _, text, conf in result or []]


def find_texts(screen_bgr, keyword: str, region=None, min_ratio: float = 0.7):
    """OCR 找**所有**像 keyword 的文字(同名按钮/多处命中), 按 y 排序返回 [(x, y, 相似度)]."""
    out = [(x, y, r) for x, y, text in _run_ocr(screen_bgr, region)
           if (r := _ratio(keyword, text)) >= min_ratio]
    out.sort(key=lambda t: t[1])
    return out


def find_text(screen_bgr, keyword: str, region=None, min_ratio: float = 0.7):
    """OCR 在 region(None=全屏)内找最像 keyword 的文字, 返回(中心x, 中心y, 相似度)或 None."""
    return best_match(_run_ocr(screen_bgr, region), keyword, min_ratio)
