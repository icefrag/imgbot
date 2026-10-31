# -*- coding: utf-8 -*-
"""提权工蜂: 以管理员常驻, 轮询 .elework/ 下的 *.job.py 逐个执行, 结果写 *.out。

用途: 本体(ZCode 会话)非提权, 目标游戏提权 —— UIPI 墙下一切探测/注入都得
管理员身份。本体写 job 文件, 工蜂跑完写结果, 会话不中断。

用法(提权启动, UAC 点一次是):
    uv run scripts/ele_worker.py
停止: .elework/stop 文件出现即退场。job 按文件名序执行, 跑完删 job 留 out。
"""
import subprocess
import sys
import time
from pathlib import Path

WORK = Path(__file__).resolve().parent.parent / ".elework"


def main():
    WORK.mkdir(exist_ok=True)
    print(f"ele_worker 就绪: {WORK}", flush=True)
    while True:
        if (WORK / "stop").exists():
            (WORK / "stop").unlink()
            print("收到 stop, 退场", flush=True)
            return
        jobs = sorted(WORK.glob("*.job.py"))
        for job in jobs:
            out = job.with_suffix(".out")
            print(f"跑 {job.name}", flush=True)
            r = subprocess.run([sys.executable, str(job)], cwd=str(WORK.parent),
                               capture_output=True, text=True, errors="replace",
                               timeout=600)
            out.write_text(f"--- exit={r.returncode} ---\n"
                           f"{r.stdout}\n{r.stderr}", encoding="utf-8")
            job.unlink()
        time.sleep(0.3)


if __name__ == "__main__":
    main()
