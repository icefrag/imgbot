# -*- coding: utf-8 -*-
"""OCR 引擎 bad allocation 自愈回归测试(2026-11-11 三台熔断案):

长跑进程里 ORT 对大输入连续抛 `FAIL : bad allocation`(小图能过、大图挂,
进程堆碎片化嫌疑), worker 重启不重建 `_engines` 单例, 只有进程重启才治.
冻结: 引擎调用撞 bad allocation → 自动重建引擎并重试一次; 其他异常照旧抛.

跑法: cd ../imgbot && uv run python scripts/test_ocr_rebuild.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imgbot import ocr


class Boom(Exception):
    pass


class FakeEngine:
    """桩: 可编程每次调用的行为, 记录调用次数."""

    def __init__(self, script):
        self.script = list(script)      # 每项: Exception 或 返回值
        self.calls = 0

    def __call__(self, img):
        self.calls += 1
        step = self.script.pop(0) if self.script else "ok"
        if isinstance(step, Exception):
            raise step
        return step


def _reset(fast_build):
    ocr._engines.clear()
    ocr._build = fast_build             # 重建时不许真加载 RapidOCR 模型


def main():
    img = "fake-img"

    # ① 慢引擎: 第一发 bad allocation → 重建 → 第二发成功
    sick = FakeEngine([Exception("[ONNXRuntimeError] : 1 : FAIL : bad allocation")])
    fresh = FakeEngine([("ok", 1.0)])
    built = []
    _reset(lambda fast: (built.append(fast), fresh)[1])
    ocr._engines[False] = sick
    assert ocr.engine()(img) == ("ok", 1.0), "重建后重试应成功"
    assert sick.calls == 1 and fresh.calls == 1, "坏引擎1发+新引擎1发"
    assert ocr._engines[False] is fresh, "单例应换成重建的引擎"
    assert built == [False], "重建只建了慢引擎"

    # ② 快引擎同样走重建
    sick2 = FakeEngine([Exception("Non-zero status code returned while running "
                                  "ConvTranspose node. Status Message: bad allocation")])
    fresh2 = FakeEngine([("ok2", 0.9)])
    _reset(lambda fast: fresh2)
    ocr._engines[True] = sick2
    assert ocr.engine(fast=True)(img) == ("ok2", 0.9)
    assert sick2.calls == 1 and fresh2.calls == 1

    # ③ 其他异常照旧抛, 不重建
    other = FakeEngine([ValueError("无关错误")])
    _reset(lambda fast: (_ for _ in ()).throw(AssertionError("不该重建")))
    ocr._engines[False] = other
    try:
        ocr.engine()(img)
        raise SystemExit("无关异常应照旧抛出")
    except ValueError:
        pass
    assert other.calls == 1

    # ④ 重建后仍 bad allocation → 第二发不再无限重试, 照旧抛
    sick3 = FakeEngine([Exception("bad allocation"), Exception("bad allocation")])
    _reset(lambda fast: sick3)          # 重建出来的还是坏的(极端堆坏)
    ocr._engines[False] = sick3
    try:
        ocr.engine()(img)
        raise SystemExit("重建后仍坏应抛出")
    except Exception as e:
        assert "bad allocation" in str(e)
    assert sick3.calls == 2, "最多两发(重建1次)"

    # ⑤ 诊断日志落盘: 重建行带 shape 进注入的日志文件
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        logp = Path(td) / "ocr.log"
        ocr.set_log_file(logp)
        sick4 = FakeEngine([Exception("bad allocation")])
        fresh4 = FakeEngine([("ok", 1.0)])
        _reset(lambda fast: fresh4)
        ocr._engines[False] = sick4
        ocr.engine()(img)
        text = logp.read_text(encoding="utf-8")
        assert "[ocr] start fast=False" in text, "起点行缺失"
        assert "bad allocation, 重建重试(fast=False" in text, "重建行缺失"
        ocr.set_log_file(None)              # 还原, 免得污染后续用例
    ocr._engines.clear()
    print("test_ocr_rebuild: 5/5 全过")


if __name__ == "__main__":
    main()
