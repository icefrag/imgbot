"""通用 OCR 文字定位: RapidOCR 单例引擎 + 长关键词按序比例匹配.

决策树第1条: 要找的目标是文字就用 OCR. 关键词规矩: ≥4字、去标点;
SequenceMatcher 按序比例匹配容忍个别错字(实测: 7字关键词错1字仍 0.83✓, 2字短词上限 0.44✗).
fast=True 走 max 快引擎: 只给"大字窄条"场景(板面标题行), 小字标签会漏读 — 默认别开.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path

_engines: dict[bool, object] = {}

# 引擎诊断日志落盘路径(消费项目 set_log_file() 注入; None=只打 stderr——打包 exe
# 下 stderr 看不见, 等于没日志). 记每次调用起点/慢调用/重建事件: 2026-11-11 三台
# 熔断案里 worker 静默卡死 55 分钟没有任何 breadcrumbs, 有了起点行才能定死
# "哪个调用进去了没出来".
_logf: Path | None = None
_loglock = threading.Lock()

# 超过这个秒数的调用落"慢调用"行(慢引擎常态 1.4~1.9s, 全屏 ~3s, 5s=必有异常)
_SLOW_CALL_S = 5.0


# onnxruntime 每模型推理线程数(2026-10-28): 默认-1=吃满全部核——16核机单次全屏OCR
# 3s墙钟烧23s CPU(7.7x放大), 多开worker各自OCR互相叠加直接把CPU打满(实测主控558%),
# 而模拟器本体才各~1核. 封顶后放大倍数≈线程数.
# env 旋钮(2026-11-12, ADR-0004): 进程形态下按机况兜底调小, 如 IMGBOT_ORT_INTRA_THREADS=1
ORT_INTRA_THREADS = int(os.environ.get("IMGBOT_ORT_INTRA_THREADS", "2"))
ORT_INTER_THREADS = 1    # 并行分层禁掉(默认随核): 多开下只有放大线程数的副作用没收益

# 慢引擎进程级并发闸(2026-11-07 客户机5开CPU 50%案): 慢OCR单发1.4~1.9s墙钟×intra2线程,
# 5 worker同相位无封顶=10线程冲顶. 同时最多 3 发在推(≈3核封顶), 排队方等前一发跑完——
# 结果零变化只延峰. 快引擎不闸(窄条25ms小拍, 闸了反被慢调用堵住轮询节奏)
# env 旋钮(2026-11-12, ADR-0004): 每设备一进程形态下 icefrag 子进程注入默认 1
# (4台峰值=慢OCR 4线程级, 不再是 3发×N进程 无界叠加); 线程形态(单进程多worker)不注入,
# 维持 3. 机况紧张也可手动 IMGBOT_OCR_MAX_CONCURRENT=1.
OCR_MAX_CONCURRENT = int(os.environ.get("IMGBOT_OCR_MAX_CONCURRENT", "3"))
_sem_slow = threading.Semaphore(OCR_MAX_CONCURRENT)


class _Capped:
    """慢引擎并发闸代理: engine() 返回值的调用方(含消费项目直调 engine()(img))无感."""
    __slots__ = ("_raw",)

    def __init__(self, raw):
        self._raw = raw

    def __call__(self, img):
        with _sem_slow:
            return self._raw(img)


def set_log_file(path) -> None:
    """消费项目注入 OCR 引擎日志落盘路径(多线程 append, 内部加锁); None=退回只打 stderr."""
    global _logf
    _logf = Path(path) if path is not None else None


def _olog(text: str) -> None:
    """引擎诊断行: 注入了日志文件就只写文件(消费项目进程形态下 stderr 并进
    stdout 行协议会漏进用户中控台, 2026-11-12 用户反馈); 没注入才退 stderr(开发态)."""
    line = f"{time.strftime('%m-%d %H:%M:%S')} {text}"
    if _logf is None:
        print(line, file=sys.stderr, flush=True)
        return
    with _loglock:
        with open(_logf, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def _build(fast: bool):
    """真建一个 RapidOCR 引擎(重建自愈与首次建共用, 测试可 monkeypatch 此函数)."""
    from rapidocr_onnxruntime import RapidOCR
    # 必须走**无前缀Global键**: 本版rapidocr的UpdateParameters有bug——带前缀的
    # det_/rec_线程键会被 Global(-1) 反向覆盖; 只有Global键经 update_global_to_module
    # 复制进三个模型段才生效(2026-10-28 实测 det_前缀传了等于没传)
    caps = dict(intra_op_num_threads=ORT_INTRA_THREADS,
                inter_op_num_threads=ORT_INTER_THREADS)
    return RapidOCR(det_limit_side_len=736, det_limit_type='max', **caps) if fast \
        else RapidOCR(**caps)


class _SelfHeal:
    """bad allocation 自愈代理(2026-11-11 三台熔断案): 长跑进程里 ORT 对大输入
    连续抛 `FAIL : bad allocation`(小图能过、大图挂, 机器 commit 未耗尽——
    pagefile 峰值 848MB/无资源耗尽事件; 嫌疑=进程堆碎片化), worker 重启不重建
    `_engines` 单例, 只有整个进程重启才治. 撞上就重建引擎重试一发, 不再烧熔断;
    重建后仍坏照旧抛(不无限重试). 每次调用落起点行、>5s 落慢调用行、重建落
    事件行(带 img.shape)——下次再犯, 三行定死是"碎片化"(重建即愈)还是
    "个别输入异常"(特定尺寸必挂), 卡死案也能看出哪个调用进去了没出来."""

    __slots__ = ("_fast",)

    def __init__(self, fast: bool):
        self._fast = fast

    def __call__(self, img):
        shape = getattr(img, "shape", None)
        _olog(f"[ocr] start fast={self._fast} shape={shape}")
        t0 = time.monotonic()
        for attempt in (0, 1):
            raw = _engines[self._fast]
            try:
                if self._fast:
                    out = raw(img)
                else:
                    with _sem_slow:             # 慢引擎并发闸(只闸真推理)
                        out = raw(img)
                dt = time.monotonic() - t0
                if dt > _SLOW_CALL_S:
                    _olog(f"[ocr] 慢调用 {dt:.1f}s fast={self._fast} shape={shape}")
                return out
            except Exception as e:
                if attempt == 0 and "bad allocation" in str(e):
                    _olog(f"[ocr] 引擎 bad allocation, 重建重试(fast={self._fast}, "
                          f"img.shape={shape}): {e}")
                    _engines[self._fast] = _build(self._fast)
                    continue
                _olog(f"[ocr] 引擎异常不重建(fast={self._fast}, shape={shape}): {e!r}")
                raise


def engine(fast: bool = False):
    """取 OCR 单例引擎(消费项目的等级读取等可直接用, 免各自建单例).

    fast=True 走 det_limit_type='max' 的快引擎: 小图不被放大, 窄条OCR 1553ms→250ms(实测).
    只给"大字窄条"场景用(板面标题行) — parity 实测它对任务窗/大地图的小字标签会整行漏读,
    所以全局默认引擎保持 min 不动(宁可慢不可漏), 双引擎并存.
    """
    if fast not in _engines:
        _engines[fast] = _build(fast)
    return _SelfHeal(fast)


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


def find_texts(screen_bgr, keyword: str, region=None, min_ratio: float = 0.7,
               fast: bool = False):
    """OCR 找**所有**像 keyword 的文字(同名按钮/多处命中), 按 y 排序返回 [(x, y, 相似度)].
    fast=True 走快引擎: 大字按钮(领奖/确定类)正合快引擎口径, 大区域也能 ~0.3s/拍."""
    out = [(x, y, r) for x, y, text in _run_ocr(screen_bgr, region, fast)
           if (r := _ratio(keyword, text)) >= min_ratio]
    out.sort(key=lambda t: t[1])
    return out


def find_text(screen_bgr, keyword: str, region=None, min_ratio: float = 0.7,
              fast: bool = False):
    """OCR 在 region(None=全屏)内找最像 keyword 的文字, 返回(中心x, 中心y, 相似度)或 None."""
    return best_match(_run_ocr(screen_bgr, region, fast), keyword, min_ratio)
