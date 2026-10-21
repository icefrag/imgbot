"""安卓中文输入: ADBKeyboard 输入法(apk 随库携带, imgbot/assets/).

adb input text 只能打 ASCII, 中文会静默丢字. 装上 ADBKeyboard 后, 中文经广播直达
聚焦的输入框. ensure() 幂等(缺就装+切换), 用完 restore() 还原原输入法, 对模拟器零残留.
签名只吃 (device, log), 不绑上层框架 —— 各项目用自己的日志对象包一层即可.
"""
from __future__ import annotations

from pathlib import Path

from .adb import AdbDevice

ASSET_APK = Path(__file__).resolve().parent / "assets" / "ADBKeyboard.apk"
IME_ID = "com.android.adbkeyboard/.AdbIME"

_ready: dict[str, str] = {}  # serial -> 进入前的原输入法id(restore 用)


def _noop(msg, level="info"):
    pass


def ensure(device: AdbDevice, log=_noop) -> bool:
    """确保 ADBKeyboard 已安装(切换尽力而为). 返回 True = 可以广播输入.

    雷电ROM的 ime set 可能不落盘(settings 仍显示原输入法, 实测如此), 但广播注入照样
    生效 —— 所以不用 settings 判定成败, 只要求 apk 已安装; 输入有没有真落框由调用方
    OCR 验证.
    """
    serial = device.serial
    if serial in _ready:
        return True
    if not ASSET_APK.exists():
        log("键盘组件文件缺失, 无法输入文字", "error")
        return False
    try:
        if IME_ID not in device.shell("ime list -s"):
            out = device.install(ASSET_APK)
            if "Success" not in out:
                log("键盘组件安装失败", "error")
                return False
        orig = device.shell("settings get secure default_input_method")
        device.shell(f"ime set {IME_ID}")
        if IME_ID not in device.shell("settings get secure default_input_method"):
            log("输入法组件就绪(兼容模式)", "warn")
        _ready[serial] = orig
        return True
    except Exception:
        log("键盘组件就绪失败", "error")
        return False


def type_text(device: AdbDevice, text: str):
    """向当前聚焦的输入框注入文字(中文/符号都行). 前提: ensure() 已通过."""
    device.shell("am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", text)


def restore(device: AdbDevice):
    """还原进入前的输入法(用完即恢复, 模拟器不留痕)."""
    orig = _ready.pop(device.serial, None)
    if orig:
        try:
            device.shell(f"ime set {orig}")
        except Exception:
            pass
