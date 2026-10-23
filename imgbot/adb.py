"""ADB 封装(多设备): 设备命令全部带 -s <serial> —— 安卓模拟器/真机后端."""
from __future__ import annotations

import subprocess
from pathlib import Path


class AdbError(RuntimeError):
    pass


def _decode(b: bytes) -> str:
    return b.decode("utf-8", errors="ignore")


class AdbDevice:
    BACK = 4  # keyevent: 返回键

    def __init__(self, serial: str, adb_path: str = "adb"):
        self.serial = serial
        self.adb_path = adb_path

    def _run(self, *args: str, timeout: float = 15) -> bytes:
        """执行 adb 命令, 失败重试 1 次."""
        cmd = [self.adb_path, "-s", self.serial, *args]
        err = "未知错误"
        for _ in range(2):
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=timeout)
            except (subprocess.TimeoutExpired, OSError) as e:
                err = repr(e)
                continue
            if r.returncode == 0:
                return r.stdout
            err = _decode(r.stderr).strip() or f"exit={r.returncode}"
        raise AdbError(f"adb {' '.join(args)}: {err}")

    # ---- 画面 ----
    def screenshot(self) -> bytes:
        # 必须 exec-out: 走 shell 管道会把 PNG 里的 \n 损坏成 \r\n
        return self._run("exec-out", "screencap", "-p")

    def save_screenshot(self, path: Path) -> tuple[int, int]:
        """截图存文件, 返回宽高."""
        png = self.screenshot()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        return png_size(png)

    # ---- 输入 ----
    def tap(self, x: int, y: int):
        self._run("shell", "input", "tap", str(x), str(y))

    def shell(self, *args: str) -> str:
        """adb shell 辅助命令(输入法/广播等), 返回 stdout 文本."""
        return _decode(self._run("shell", *args))

    def install(self, apk: Path, timeout: float = 120) -> str:
        """安装 apk(已装则覆盖), 返回 adb 输出(含 Success/Failure)."""
        return _decode(self._run("install", "-r", str(apk), timeout=timeout))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 300):
        self._run("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms))

    def keyevent(self, code: int):
        self._run("shell", "input", "keyevent", str(code))

    # ---- 应用启停 ----
    def start_app(self, package: str, activity: str):
        self._run("shell", "am", "start", "-n", f"{package}/{activity}")

    def stop_app(self, package: str):
        self._run("shell", "am", "force-stop", package)


def png_size(png: bytes) -> tuple[int, int]:
    """从 PNG 头解析宽高 —— 不引 numpy/cv2 即可验证截图链路."""
    if len(png) < 24 or not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AdbError(f"非法 PNG 数据 ({len(png)} bytes)")
    return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")


def adb(adb_path: str, *args: str, timeout: float = 15) -> str:
    """无 -s 的全局 adb 命令(connect / devices 等)."""
    try:
        r = subprocess.run([adb_path, *args], capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        raise AdbError(f"adb {' '.join(args)}: {e!r}") from e
    if r.returncode != 0:
        raise AdbError(_decode(r.stderr).strip() or f"exit={r.returncode}")
    return _decode(r.stdout)


def parse_devices(text: str) -> list[str]:
    """解析 `adb devices` 输出, 只保留在线(device)的序列号.

    同一台模拟器会以两条传输通道出现(控制台 emulator-N + TCP 127.0.0.1:N+1,
    模型信息完全相同), 只留 TCP 那条 —— 它才是 `adb connect` 用的稳定序列号."""
    serials, tcp_seen = [], set()
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        serial = parts[0]
        if serial.startswith("emulator-") and serial[9:].isdigit() \
                and f"127.0.0.1:{int(serial[9:]) + 1}" in tcp_seen:
            continue  # TCP 通道已在列表, 控制台通道是同一台
        if serial.startswith("127.0.0.1:"):
            tcp_seen.add(serial)
        serials.append(serial)
    return serials


def list_devices(adb_path: str = "adb") -> list[str]:
    return parse_devices(adb(adb_path, "devices"))
