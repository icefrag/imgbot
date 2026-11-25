# -*- coding: utf-8 -*-
"""构建安卓驻留件(ADR-0002): javac → d8 → jar.

用法: uv run imgbot/helper/build.py [--r8 路径] [--android-jar 路径]
产物: imgbot/assets/adbhelper.jar(KB 级, 入库; 消费项目免构建工具)

开发机一次性依赖(不入仓不随产品发):
- JDK(javac; 本机 JAVA_HOME 已设)
- r8 jar(含 D8; 默认取 D:/tools/android-build-tools/, 从 Google maven 一次性下载:
  https://dl.google.com/android/maven2/com/android/tools/r8/<ver>/r8-<ver>.jar)
- android.jar(任意 API level 均可 —— 源码只依赖公开类 android.view.*,
  隐藏方法走运行时反射, 编译期不出现; 默认取本机 gradle 缓存一份)
"""
import argparse
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"
OUT_JAR = ASSETS / "adbhelper.jar"

DEFAULT_R8 = Path("D:/tools/android-build-tools/r8-8.5.35.jar")
DEFAULT_ANDROID_JAR = Path("C:/Users/icefr/.gradle/caches/transforms-4/"
                           "06371448b9e70fde870e741c25a6391a/transformed/android.jar")


def _java_home() -> Path:
    home = Path(sys.executable).drive + "\\"  # 占位, 防空
    import os
    jh = os.environ.get("JAVA_HOME")
    if not jh:
        raise SystemExit("JAVA_HOME 未设(本机: D:\\Program Files\\Java\\jdk-17.0.20.1+1)")
    return Path(jh)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--r8", type=Path, default=DEFAULT_R8)
    ap.add_argument("--android-jar", type=Path, default=DEFAULT_ANDROID_JAR)
    args = ap.parse_args()
    r8, android_jar = args.r8, args.android_jar
    for f in (r8, android_jar):
        if not f.is_file():
            raise SystemExit(f"缺文件: {f}")
    javac = _java_home() / "bin" / "javac.exe"
    java = _java_home() / "bin" / "java.exe"

    with tempfile.TemporaryDirectory(prefix="adbhelper_") as td:
        classes = Path(td) / "classes"
        dex = Path(td) / "dex"
        classes.mkdir()
        dex.mkdir()

        subprocess.run([str(javac), "-encoding", "UTF-8", "-source", "8", "-target", "8",
                        "-cp", str(android_jar), "-d", str(classes),
                        str(HERE / "Main.java")], check=True)

        subprocess.run([str(java), "-cp", str(r8), "com.android.tools.r8.D8",
                        "--release", "--lib", str(android_jar),
                        "--output", str(dex),
                        *[str(p) for p in classes.rglob("*.class")]], check=True)

        ASSETS.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(OUT_JAR, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(dex / "classes.dex", "classes.dex")
    print(f"OK -> {OUT_JAR} ({OUT_JAR.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
