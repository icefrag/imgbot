package com.imgbot.helper;

import android.os.Build;
import android.os.SystemClock;
import android.view.InputDevice;
import android.view.InputEvent;
import android.view.KeyEvent;
import android.view.MotionEvent;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.lang.reflect.Method;

/**
 * imgbot 驻留件: stdin 行协议代宿主注入输入(2026-11-23, ADR-0002)。
 *
 * 启动: CLASSPATH=/data/local/tmp/imgbot_helper.jar app_process /system/bin com.imgbot.helper.Main
 * (与系统 /system/bin/input 同一种启动方式与身份, shell uid 持 INJECT_EVENTS)。
 * 协议: 每行一指令, 每指令一应答行 "OK ..." / "ERR ..."; EOF 或 quit 退出。
 * 指令: ping / tap x y / swipe x1 y1 x2 y2 ms / key code / quit。
 *
 * 注入: InputManager.injectInputEvent ASYNC —— 与系统 input 命令同接口(源码核实
 * android-9/11 cmds/input Input.java 与 scrcpy wrappers 同路)。事件构造照 AOSP:
 * SOURCE_TOUCHSCREEN/KEYBOARD、FLAG_FROM_SYSTEM、按压 down=1.0 up=0.0、tap 按下抬起同
 * 时间戳、swipe 直线 lerp 同时长。反射入口跨版本: 9-13 InputManager.getInstance 公开;
 * 拿不到退 InputManagerGlobal(Android 14+, 未验)。
 */
public final class Main {

    private static final int MODE_ASYNC = 0;        // INJECT_INPUT_EVENT_MODE_ASYNC(scrcpy wrappers 同值)
    private static final int FLAG_FROM_SYSTEM = 8;  // KeyEvent.FLAG_FROM_SYSTEM
    private static final int SWIPE_STEP_MS = 16;    // swipe 插值步距(直线同时长契约不因步距改变)
    private static final String SOCKET_NAME = "imgbot_input";  // localabstract 名

    private static Object sManager;                 // InputManager(9-13) 或 InputManagerGlobal(14+) 实例
    private static Method sInject;                  // injectInputEvent(InputEvent, int)

    public static void main(String[] args) throws Exception {
        if (args.length >= 2 && "--listen".equals(args[0])) {
            serve(Integer.parseInt(args[1]));
            return;
        }
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, "UTF-8"), 8192);
        String line;
        while ((line = in.readLine()) != null) {
            line = line.trim();
            if (line.isEmpty()) {
                continue;
            }
            String resp = handle(line);
            System.out.println(resp);
            System.out.flush();
            if ("OK bye".equals(resp)) {
                return;
            }
        }
    }

    /**
     * TCP/abstract-unix 双监听服务模式(宿主 adb forward 转入; ADR-0002 决策 3 修订:
     * adb shell 数据面实测 40ms/包, socket 路为 scrcpy 同款标准解)。逐连接跑同一行协议,
     * EOF 关连接继续收; quit 退整个进程。端口/名占用时 bind 失败仅关该监听 —— 宿主侧按
     * "旧实例可复用"处理。
     */
    private static void serve(int port) throws Exception {
        // 两个 bind 都非致命: 占用只 WARN(旧实例可复用场景), 不许一处失败拖死进程
        // (否则 TCP 口被旧实例占着时, 新进程异常退出会把 localabstract 监听一起带走)。
        try {
            android.net.LocalServerSocket ls = new android.net.LocalServerSocket(SOCKET_NAME);
            Thread tAbs = new Thread(() -> acceptLoopAbs(ls));
            tAbs.setDaemon(true);
            tAbs.start();
        } catch (Throwable e) {
            System.out.println("WARN localabstract busy: " + e);
            System.out.flush();
        }
        try {
            java.net.ServerSocket ss = new java.net.ServerSocket(
                    port, 1, java.net.InetAddress.getByName("127.0.0.1"));
            Thread tTcp = new Thread(() -> acceptLoopTcp(ss));
            tTcp.setDaemon(true);
            tTcp.start();
        } catch (Throwable e) {
            System.out.println("WARN tcp busy: " + e);
            System.out.flush();
        }
        Thread.currentThread().join();   // 两个监听都是守护线程, 主线程挂起等 quit
    }

    /** 逐连接行协议泵: 一问一答, OK bye 收卷。 */
    private static boolean pump(BufferedReader in, java.io.Writer out) throws Exception {
        String line;
        while ((line = in.readLine()) != null) {
            line = line.trim();
            if (line.isEmpty()) {
                continue;
            }
            String resp = handle(line);
            out.write(resp);
            out.write("\n");
            out.flush();
            if ("OK bye".equals(resp)) {
                return false;
            }
        }
        return true;    // EOF: 客户端走了, 继续服务
    }

    private static void acceptLoopTcp(java.net.ServerSocket ss) {
        while (true) {
            java.net.Socket s = null;
            try {
                s = ss.accept();
                s.setTcpNoDelay(true);      // 设备侧 Nagle+延迟ACK 实测 40ms/包, scrcpy 同款解
                BufferedReader in = new BufferedReader(
                        new InputStreamReader(s.getInputStream(), "UTF-8"), 8192);
                if (!pump(in, new java.io.BufferedWriter(
                        new java.io.OutputStreamWriter(s.getOutputStream(), "UTF-8")))) {
                    ss.close();
                    System.exit(0);
                }
            } catch (Throwable e) {
                // 单连接异常: 关连接继续 accept
            } finally {
                try {
                    if (s != null) s.close();
                } catch (Exception ignored) {
                }
            }
        }
    }

    private static void acceptLoopAbs(android.net.LocalServerSocket ls) {
        while (true) {
            android.net.LocalSocket s = null;
            try {
                s = ls.accept();
                BufferedReader in = new BufferedReader(
                        new InputStreamReader(s.getInputStream(), "UTF-8"), 8192);
                if (!pump(in, new java.io.BufferedWriter(
                        new java.io.OutputStreamWriter(s.getOutputStream(), "UTF-8")))) {
                    System.exit(0);
                }
            } catch (Throwable e) {
                // 单连接异常: 关连接继续 accept
            } finally {
                try {
                    if (s != null) s.close();
                } catch (Exception ignored) {
                }
            }
        }
    }

    private static String handle(String line) {
        String[] t = line.split("\\s+");
        try {
            if ("ping".equals(t[0])) {
                bind();                              // 握手即验证注入入口可用
                return "OK ping sdk" + Build.VERSION.SDK_INT;
            }
            if ("tap".equals(t[0])) {
                int x = Integer.parseInt(t[1]), y = Integer.parseInt(t[2]);
                long now = SystemClock.uptimeMillis();
                inject(touch(now, now, MotionEvent.ACTION_DOWN, x, y, 1.0f));
                inject(touch(now, now, MotionEvent.ACTION_UP, x, y, 0.0f));
                return "OK";
            }
            if ("swipe".equals(t[0])) {
                swipe(Integer.parseInt(t[1]), Integer.parseInt(t[2]),
                      Integer.parseInt(t[3]), Integer.parseInt(t[4]),
                      Integer.parseInt(t[5]));
                return "OK";
            }
            if ("key".equals(t[0])) {
                int code = Integer.parseInt(t[1]);
                long now = SystemClock.uptimeMillis();
                inject(key(now, now, KeyEvent.ACTION_DOWN, code));
                inject(key(now, now, KeyEvent.ACTION_UP, code));
                return "OK";
            }
            if ("quit".equals(t[0])) {
                return "OK bye";
            }
            return "ERR unknown " + t[0];
        } catch (Throwable e) {
            return "ERR " + e.getClass().getSimpleName();
        }
    }

    /** down@起点 → lerp 直线 MOVE → up@终点(AOSP sendSwipe 形, 直线匀速)。 */
    private static void swipe(int x1, int y1, int x2, int y2, int ms) throws Exception {
        long down = SystemClock.uptimeMillis();
        inject(touch(down, down, MotionEvent.ACTION_DOWN, x1, y1, 1.0f));
        int steps = Math.max(1, Math.min(60, ms / SWIPE_STEP_MS));
        for (int i = 1; i <= steps; i++) {
            long target = down + (long) ms * i / steps;
            long rest = target - SystemClock.uptimeMillis();
            if (rest > 0) {
                Thread.sleep(rest);
            }
            float a = (float) i / steps;
            long now = SystemClock.uptimeMillis();
            inject(touch(down, now, MotionEvent.ACTION_MOVE,
                    Math.round(x1 + (x2 - x1) * a), Math.round(y1 + (y2 - y1) * a), 1.0f));
        }
        long now = SystemClock.uptimeMillis();
        inject(touch(down, now, MotionEvent.ACTION_UP, x2, y2, 0.0f));
    }

    private static void inject(InputEvent ev) throws Exception {
        bind();
        if (!((Boolean) sInject.invoke(sManager, ev, MODE_ASYNC))) {
            throw new IllegalStateException("inject rejected");
        }
    }

    /** 全版本反射绑定注入入口(scrcpy 同款思路): InputManager.getInstance 首选,
     *  14+ 实例与方法挪入 InputManagerGlobal 时退而反射之。拿不到不猜, 直接抛。 */
    private static void bind() throws Exception {
        if (sInject != null) {
            return;
        }
        Class<?> cls = Class.forName("android.hardware.input.InputManager");
        try {
            sManager = cls.getMethod("getInstance").invoke(null);
            sInject = cls.getMethod("injectInputEvent", InputEvent.class, int.class);
        } catch (ReflectiveOperationException e) {
            Class<?> g = Class.forName("android.hardware.input.InputManagerGlobal"); // Android 14+
            sManager = g.getMethod("getInstance").invoke(null);
            sInject = g.getMethod("injectInputEvent", InputEvent.class, int.class);
        }
    }

    /** MotionEvent 构造照 AOSP injectMotionEvent(pointer 族, 单点)。 */
    private static MotionEvent touch(long downTime, long eventTime, int action,
                                     int x, int y, float pressure) {
        MotionEvent ev = MotionEvent.obtain(downTime, eventTime, action, x, y, pressure, 1.0f,
                0, 1.0f, 1.0f, pointerDeviceId(), 0);
        ev.setSource(InputDevice.SOURCE_TOUCHSCREEN);
        return ev;
    }

    /** 第一个支持 pointer class 的输入设备 id, 没有则 -1(AOSP getInputDeviceId 兜底同值)。 */
    private static int pointerDeviceId() {
        for (int id : InputDevice.getDeviceIds()) {
            InputDevice d = InputDevice.getDevice(id);
            if (d != null && (d.getSources() & InputDevice.SOURCE_CLASS_POINTER) != 0) {
                return id;
            }
        }
        return -1;
    }

    /** KeyEvent 全参构造(AOSP sendKeySync 重排同语义: deviceId=0/scancode=0 + FLAG_FROM_SYSTEM)
     *  + SOURCE_KEYBOARD。(setFlags 已从新 SDK jar 移除, flags 走构造参数。) */
    private static KeyEvent key(long downTime, long eventTime, int action, int code) {
        KeyEvent ev = new KeyEvent(downTime, eventTime, action, code, 0, 0, 0, 0, FLAG_FROM_SYSTEM);
        ev.setSource(InputDevice.SOURCE_KEYBOARD);
        return ev;
    }

    private Main() {
    }
}
