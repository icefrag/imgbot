"""安卓输入常驻会话(ADR-0002): 绑定时刻建立, 动作走行协议, 免每动作起进程.

构成: 安卓侧驻留件(imgbot/assets/adbhelper.jar, 推到 /data/local/tmp)+ 宿主侧
常驻通道 + 行协议(每行一指令一应答: OK/ERR)。

通道优先级(2026-11-23 实测修订): TCP(adb forward tcp:{宿主口} tcp:7555 + 驻留件
--listen)优先 —— adb shell 数据面实测 40ms/包, socket 是 scrcpy 同款标准解; TCP
起不来退管道模式(adb shell stdin/stdout, 行为不劣于 shell 路)。宿主口每会话独占
(20000+ 探测空闲), 与 winnat 保留段无涉; 设备口恒 7555, 旧实例直接复用。

容错: 断线自动重建一次并重发原指令, 仍败永久回落 shell 路(基类原方法, 行为不变),
回落记 warn —— 最坏等于现状。默认通道仍 "shell"; 只有 AdbTarget(serial,
input="session") 才建立会话。

诊断日志: set_log 注入回调(msg, level), 默认不输出(签名同 ime 的 log 回调)。
"""
from __future__ import annotations

import queue
import socket
import subprocess
import sys
import threading
import time

from pathlib import Path

from .adb import AdbDevice

HELPER_JAR = Path(__file__).resolve().parent / "assets" / "adbhelper.jar"
REMOTE_JAR = "/data/local/tmp/imgbot_helper.jar"
LAUNCH = f"CLASSPATH={REMOTE_JAR} app_process /system/bin com.imgbot.helper.Main"
DEV_PORT = 7555          # 设备侧监听口(guest 环回, 经 forward 暴露)
SOCKET_NAME = "imgbot_input"  # 设备侧 localabstract 名(与驻留件 Main.java 一致)
HOST_PORT_FROM = 20000   # 宿主口探测起点(避开保留段/常用段)

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class AdbSessionError(RuntimeError):
    """会话层失败(建立/写入/应答/EOF); 抛出即由 SessionDevice 走重建或回落。"""


def _noop(msg, level="info"):
    pass


_log_cb = _noop


def set_log(cb) -> None:
    """注入诊断回调(msg, level="warn"), 回落/降级时被调; 默认不输出。"""
    global _log_cb
    _log_cb = cb


def _log(msg, level="warn"):
    try:
        _log_cb(msg, level=level)
    except Exception:
        pass


class _PipeChan:
    """管道通道: 常驻 `adb shell` 进程, stdin/stdout 行协议。TCP 不可用时的兜底。"""

    def __init__(self, device: AdbDevice):
        self._proc = subprocess.Popen(
            [device.adb_path, "-s", device.serial, "shell", LAUNCH],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=_NO_WINDOW)
        self._lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self._proc.stdout:
            self._lines.put(line.rstrip("\r\n"))
        self._lines.put(None)  # EOF 哨兵

    @property
    def alive(self) -> bool:
        return self._proc.poll() is None

    def send_line(self, line: str):
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()

    def read_line(self, timeout: float) -> str:
        try:
            resp = self._lines.get(timeout=timeout)
        except queue.Empty:
            raise AdbSessionError(f"应答超时({timeout}s)") from None
        if resp is None:
            raise AdbSessionError("会话已断(EOF)")
        return resp

    def close(self):
        try:
            self._proc.terminate()
        except Exception:
            pass


class _TcpChan:
    """socket 通道(adb forward 转入设备侧 ServerSocket)。read 靠 settimeout 实现超时。"""

    def __init__(self, host: str, port: int, timeout: float = 8.0):
        deadline = time.monotonic() + timeout
        while True:
            try:
                self._sock = socket.create_connection((host, port), timeout=2)
                break
            except OSError as e:
                if time.monotonic() >= deadline:
                    raise AdbSessionError(f"连接设备侧 {host}:{port} 失败: {e}") from e
                time.sleep(0.2)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # 实测 Nagle 致 40ms/包
        self._rfile = self._sock.makefile("r", encoding="utf-8", errors="replace", newline="\n")

    def probe(self, timeout: float = 2.0) -> bool:
        """ping 探活: False = 此连接不可用(如设备端 JVM 尚未 listen, 转发被关), 调用方重试。"""
        try:
            self.send_line("ping")
            return self.read_line(timeout).startswith("OK")
        except AdbSessionError:
            return False

    @property
    def alive(self) -> bool:
        return self._sock is not None

    def send_line(self, line: str):
        self._sock.sendall((line + "\n").encode("utf-8"))

    def read_line(self, timeout: float) -> str:
        self._sock.settimeout(timeout)
        resp = self._rfile.readline()
        if not resp:
            raise AdbSessionError("会话已断(EOF)")
        return resp.rstrip("\r\n")

    def close(self):
        try:
            self._sock.close()
        except Exception:
            pass
        self._sock = None


class AdbSession:
    """单设备会话: 推驻留件并常驻, 发指令收应答。

    线程安全: cmd 全程持锁(icefrag 每目标另有看门狗线程)。应答超时即视为会话废,
    由 SessionDevice 弃会话重建 —— 不做跨指令应答配对, 迟到的应答随旧会话一起丢弃。
    """

    def __init__(self, device: AdbDevice, log=_noop):
        self.device = device
        self._chan = None            # _TcpChan | _PipeChan
        self._host_port: int | None = None
        self._helper: subprocess.Popen | None = None  # TCP 模式的驻留件进程句柄(尽力拉起)
        self._lock = threading.Lock()
        self._pending = 0            # 已发未收应答的异步指令数(应答行严格按发送序)

    @property
    def alive(self) -> bool:
        return self._chan is not None and self._chan.alive

    def start(self):
        """推驻留件, 起 TCP 会话(失败退管道), ping 握手(顺带验证注入入口可用)。"""
        if not HELPER_JAR.exists():
            raise AdbSessionError(f"驻留件缺失: {HELPER_JAR}")
        self.device._run("push", str(HELPER_JAR), REMOTE_JAR)
        try:
            self._start_tcp()
        except Exception as e:
            _log(f"TCP 会话不可用({e}), 退管道模式")
            self._start_pipe()
        self.cmd("ping", timeout=10)  # 首发含设备端 JVM 启动, 放宽

    def _start_tcp(self):
        # 宿主口: 探测空闲(同 adb server 下多设备多会话各占一口)
        self._host_port = None
        for port in range(HOST_PORT_FROM, HOST_PORT_FROM + 200):
            try:
                s = socket.socket()
                s.bind(("127.0.0.1", port))
                s.close()
                self._host_port = port
                break
            except OSError:
                continue
        if self._host_port is None:
            raise AdbSessionError("无空闲宿主口")
        # 拉驻留件服务模式(双监听 localabstract+tcp; 旧实例在听时 bind 失败只 WARN,
        # 连接照常复用旧实例)。注意: 拉起用的 adb shell 客户端进程可能自发退出, 但
        # 设备端驻留件不受影响(实测 2026-11-23)—— 生命周期以 quit 协议为准。
        self._helper = subprocess.Popen(
            [self.device.adb_path, "-s", self.device.serial, "shell",
             f"{LAUNCH} --listen {DEV_PORT}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW)
        # localabstract 优先(scrcpy 同款, 绕 guest TCP 栈), tcp:7555 兜底。两条路
        # 往返延迟实测相同(~40ms 停顿在 adb 传输层不在 guest), abstract 仅少一跳依赖。
        last = None
        for remote in (f"localabstract:{SOCKET_NAME}", f"tcp:{DEV_PORT}"):
            try:
                self.device._run("forward", f"tcp:{self._host_port}", remote)
            except Exception as e:
                last = e
                continue
            # ping 探活重试: connect 到 adb server 总会成功, 但设备端 JVM 启动要 1-3s,
            # 此间转发无对端会被关(EOF) —— 探活失败退避重连, 不把它当会话故障。
            deadline = time.monotonic() + 12
            while True:
                chan = _TcpChan("127.0.0.1", self._host_port)
                if chan.probe():
                    self._chan = chan
                    return
                chan.close()
                last = "probe failed"
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.4)
        raise AdbSessionError(f"设备侧驻留件探活失败: {last}")

    def _start_pipe(self):
        self._chan = _PipeChan(self.device)

    def close(self):
        """quit 礼貌退出 + 收通道 + 撤 forward; 幂等。宿主进程崩溃场景: 通道随进程
        消亡, 设备侧驻留件与 forward 残留 —— 下次 start 复用旧实例, 不留死会话。"""
        chan, self._chan = self._chan, None
        if chan is None:
            return
        try:
            self.cmd("quit", timeout=2)
        except Exception:
            pass
        chan.close()
        if self._helper is not None:
            try:
                self._helper.terminate()
            except Exception:
                pass
            self._helper = None
        if self._host_port is not None:
            try:
                self.device._run("forward", "--remove", f"tcp:{self._host_port}")
            except Exception:
                pass
            self._host_port = None

    def cmd(self, line: str, timeout: float = 5.0, sync: bool = True) -> str:
        """发一条指令。

        sync=True(默认): 等到本指令的应答行(先清完此前异步指令的应答), ERR/EOF/超时
        抛 AdbSessionError。swipe 用它 —— 手势时长语义对齐 shell 路(input swipe 阻塞)。
        sync=False(tap/key 派发): 只发送不等待; 应答行由下一条指令的同步读顺带清点。
        —— adb 传输层单次往返实测有 ~40ms 固定停顿(管道/tcp/abstract 三路同; 管道化
        吞吐正常, 停顿在 adb server+VBox NAT 中继, 修不了), scrcpy control 同样以
        发后不等绕开(2026-11-23 实测定案)。
        """
        with self._lock:
            if not self.alive:
                raise AdbSessionError("会话未运行")
            self._chan.send_line(line)
            if not sync:
                self._pending += 1
                return ""
            resp = ""
            for _ in range(self._pending + 1):     # 先清积压异步应答, 末行才是本指令的
                resp = self._chan.read_line(timeout)
                if not resp.startswith("OK") and _ < self._pending:
                    _log(f"驻留件异步指令报错: {resp}")  # 积压行只记日志, 不占错误位
            self._pending = 0
        if not resp.startswith("OK"):
            raise AdbSessionError(f"驻留件报错: {resp}")
        return resp


class SessionDevice(AdbDevice):
    """tap/swipe/keyevent 走常驻会话, 断线重建一次, 仍败永久回落 shell 路。

    其余能力(screenshot/shell/install/...)原样继承 —— 会话只接管输入三件,
    接口签名与基类一致, actions/ime 层与消费项目零改动。多线程安全: _send 全程持锁。
    """

    _factory = AdbSession  # 单测桩位: 覆盖为假会话类

    def __init__(self, serial: str, adb_path: str = "adb"):
        super().__init__(serial, adb_path)
        self._session = None
        self._locked_out = False  # 永久回落标志(建立失败或重建失败后置位)
        self._lock = threading.Lock()
        self._last_ok = 0.0       # 最近一次同步存活确认(monotonic); 异步死窗兜底用

    def _send(self, line: str, sync: bool = True) -> str:
        if self._locked_out:
            raise AdbSessionError("已回落 shell 路")
        with self._lock:
            try:
                if self._session is None or not self._session.alive:
                    self._drop()
                    self._session = self._factory(self)
                    self._session.start()
                    self._last_ok = time.monotonic()
            except Exception as e:
                self._locked_out = True
                self._drop()
                _log("常驻会话建立失败, 本设备回落 shell 路")
                raise AdbSessionError(f"会话建立失败: {e}") from e
            try:
                resp = self._session.cmd(line, sync=sync)
                if not sync and time.monotonic() - self._last_ok > 2.0:
                    # 异步死窗兜底: tap/key 发后不等, 会话死了写缓冲照样成功 —— 每 ≤2s
                    # 借一条 ping 同步校验存活(顺带清异步应答), 死窗有界, 成本 ~40ms/2s。
                    self._session.cmd("ping", timeout=2)
                    self._last_ok = time.monotonic()
                return resp
            except Exception as e:
                self._drop()
                _log("常驻会话中断, 重建重试")
                try:  # 决策 4: 重建一次并重发原指令
                    self._session = self._factory(self)
                    self._session.start()
                    self._last_ok = time.monotonic()
                    return self._session.cmd(line, sync=sync)
                except Exception as e2:
                    self._locked_out = True
                    self._drop()
                    _log("常驻会话重建失败, 本设备回落 shell 路")
                    raise AdbSessionError(f"会话重连失败: {e2}") from e2

    def _drop(self):
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

    def tap(self, x: int, y: int):
        try:
            self._send(f"tap {int(x)} {int(y)}", sync=False)  # 发后不等(应答顺带清)
        except AdbSessionError:
            super().tap(x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 300):
        try:
            self._send(f"swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(ms)}")
        except AdbSessionError:
            super().swipe(x1, y1, x2, y2, ms)

    def keyevent(self, code: int):
        try:
            self._send(f"key {int(code)}", sync=False)
        except AdbSessionError:
            super().keyevent(code)

    def close(self):
        """收口会话(宿主进程退出同样收口, 双保险)。"""
        with self._lock:
            self._drop()
