from __future__ import annotations

import copy
import os
import queue
import threading
import time

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .config import DEFAULTS
from .paths import data_dir, log_dir
from .profiles import ProfileStore
from . import startup


class UiThread:
    """Runs Tk on a dedicated thread and accepts callbacks from other threads."""

    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._root = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def post(self, callback) -> None:
        self._queue.put(callback)

    def _run(self) -> None:
        try:
            self._root = tk.Tk()
            self._root.withdraw()
        except Exception:
            return
        self._root.after(50, self._poll)
        self._root.mainloop()

    def _poll(self) -> None:
        try:
            while True:
                callback = self._queue.get_nowait()
                try:
                    callback(self._root)
                except Exception:
                    pass
        except queue.Empty:
            pass
        try:
            self._root.after(50, self._poll)
        except Exception:
            pass


def ask_password(parent, prompt: str = "请输入管理密码：") -> str | None:
    return simpledialog.askstring(
        "OpticalInputSvc", prompt, show="*", parent=parent
    )


def ask_new_password(parent) -> str | None:
    first = simpledialog.askstring(
        "OpticalInputSvc",
        "设置管理密码（至少 4 位）：",
        show="*",
        parent=parent,
    )
    if first is None:
        return None
    if len(first) < 4:
        messagebox.showerror(
            "OpticalInputSvc", "密码至少 4 位。", parent=parent
        )
        return None
    second = simpledialog.askstring(
        "OpticalInputSvc", "再次输入管理密码：", show="*", parent=parent
    )
    if second != first:
        messagebox.showerror(
            "OpticalInputSvc", "两次输入不一致。", parent=parent
        )
        return None
    return first


def show_error(parent, text: str) -> None:
    messagebox.showerror("OpticalInputSvc", text, parent=parent)


SECTIONS = [
    (
        "更新",
        [
            ("update.enabled", "自动更新", "bool"),
            ("update.channel", "通道", "choice", ["stable"]),
            ("update.interval_hours", "检查间隔（小时）", "int"),
            (
                "update.apply_when",
                "生效时机",
                "choice",
                ["startup", "next_start", "manual"],
            ),
            ("update.allow_downgrade", "允许降级", "bool"),
        ],
    ),
    (
        "启动",
        [
            ("startup.silent", "静默启动", "bool"),
            ("startup.auto_run", "开机自动运行", "bool"),
        ],
    ),
    (
        "性能",
        [
            ("performance.mode", "模式", "choice", ["auto", "high", "low"]),
            ("performance.cpu_limit_pct", "CPU 上限（%）", "int"),
            ("performance.rss_limit_mb", "内存上限（MB）", "int"),
            ("performance.recover_seconds", "恢复等待（秒）", "int"),
            ("logging.max_mb", "日志上限（MB）", "float"),
        ],
    ),
    (
        "摄像头",
        [
            ("camera.device", "设备", "str"),
            (
                "camera.backend",
                "后端",
                "choice",
                ["dshow", "msmf", "any"],
            ),
            ("camera.width", "宽度", "int"),
            ("camera.height", "高度", "int"),
            ("camera.fps", "帧率", "int"),
            ("camera.prefer_4k", "优先 4K", "bool"),
        ],
    ),
    (
        "档位",
        [
            ("tier.mode", "模式", "choice", ["auto", "manual"]),
            ("tier.lock", "锁定档位", "choice", ["", "A", "B", "C", "D", "E"]),
            ("tier.apply", "自动应用档位设置", "bool"),
            ("tier.apply_mode", "档位决定映射方式", "bool"),
            ("tier.apply_gesture", "档位决定手势方案", "bool"),
        ],
    ),
    (
        "指针",
        [
            (
                "pointer.mode",
                "映射",
                "choice",
                ["auto", "absolute", "relative"],
            ),
            ("pointer.hand", "手", "choice", ["right", "left"]),
            ("pointer.active_on_start", "启动即激活", "bool"),
            ("pointer.mouse_pause", "鼠标一动就暂停", "bool"),
            ("pointer.mouse_pause_sec", "暂停秒数", "int"),
            ("pointer.write_mode", "书写平滑", "bool"),
            ("pointer.write_smoothing", "书写平滑强度", "float"),
            ("pointer.write_min_step_px", "书写最小步长（px）", "float"),
            ("pointer.edge_scroll", "贴边滚动", "bool"),
            ("pointer.v_scroll", "近距 V 字手势滚动", "bool"),
            ("pointer.self_calib", "点击自动校正偏移", "bool"),
            ("pointer.gain_relative", "相对增益", "float"),
            ("pointer.deadzone_px", "死区（px）", "float"),
            ("pointer.prediction_ms", "预测（ms）", "int"),
            ("pointer.max_step_px", "单帧最大位移（px）", "float"),
            ("pointer.mincutoff", "平滑（越小越稳）", "float"),
            ("pointer.beta", "跟随（越大越快）", "float"),
            (
                "pose.model_complexity",
                "姿态模型",
                "choice",
                ["0", "1", "2"],
            ),
        ],
    ),
    (
        "手势",
        [
            (
                "gesture.scheme",
                "手方案",
                "choice",
                ["auto", "single", "double"],
            ),
            (
                "gesture.click",
                "点击手势",
                "choice",
                ["auto", "pinch", "fist"],
            ),
            ("gesture.right_click_ms", "右键长按（ms）", "int"),
            ("gesture.drag_threshold_px", "拖拽阈值（px）", "int"),
            ("gesture.double_click_ms", "双击间隔（ms）", "int"),
            ("hands.model_complexity", "手部模型", "choice", ["0", "1"]),
            ("gesture.pinch_enter", "捏合进入", "float"),
            ("gesture.pinch_exit", "捏合退出", "float"),
            ("gesture.fist_enter", "握拳进入", "float"),
            ("gesture.fist_exit", "握拳退出", "float"),
            ("gesture.personalized", "个性化手势（kNN）", "bool"),
            ("__train", "录制手势样本…", "action"),
        ],
    ),
    (
        "叠加",
        [
            ("overlay.status_bar", "状态条", "bool"),
            ("overlay.control_bar", "控制条", "bool"),
            ("overlay.box", "屏幕上显示映射框", "bool"),
            ("overlay.hold_ring", "长按进度环", "bool"),
            ("overlay.two_hand_bar", "双手张开呼出控制条", "bool"),
            ("overlay.two_hand_hold_ms", "双手张开时长（ms）", "int"),
            ("overlay.tier_toast", "档位变化提示", "bool"),
            ("overlay.magnifier", "书写放大镜", "bool"),
            ("overlay.magnifier_size", "放大镜尺寸（px）", "int"),
            ("overlay.magnifier_zoom", "放大倍数", "float"),
            ("overlay.presentation", "演示模式（边缘禁点）", "bool"),
            ("overlay.big_cursor", "大光标（退出时还原）", "bool"),
            ("overlay.big_cursor_size", "大光标倍数", "int"),
        ],
    ),
    (
        "热键",
        [
            ("hotkey.toggle_active", "激活/暂停", "str"),
            ("hotkey.toggle_mode", "切换模式", "str"),
            ("hotkey.control_bar", "显示控制条", "str"),
            ("hotkey.emergency_stop", "急停", "str"),
        ],
    ),
    (
        "录制",
        [
            ("recording.min_free_gb", "最低剩余空间（GB）", "int"),
        ],
    ),
]


def open_settings(
    root, config, security, version: str, on_saved=None, controller=None
) -> None:
    existing = getattr(root, "_settings_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                return
        except tk.TclError:
            pass

    win = tk.Toplevel(root)
    root._settings_window = win
    win.title("OpticalInputSvc 设置")
    win.geometry("780x580")
    win.minsize(660, 480)
    win.transient(root)

    notebook = ttk.Notebook(win)
    notebook.pack(fill="both", expand=True, padx=10, pady=10)

    bindings = []
    for title, fields in SECTIONS:
        frame = ttk.Frame(notebook, padding=12)
        notebook.add(frame, text=title)
        _build_fields(frame, config, fields, bindings, controller)

    _build_security(notebook, config, security, bindings)
    _build_about(notebook, version)

    def reopen() -> None:
        win.destroy()
        open_settings(root, config, security, version, on_saved)

    _build_profiles(notebook, config, on_saved, reopen)

    bar = ttk.Frame(win)
    bar.pack(fill="x", padx=10, pady=(0, 10))

    def save() -> None:
        try:
            for key, kind, var in bindings:
                raw = var.get()
                if kind == "bool":
                    value = bool(raw)
                elif kind == "int":
                    value = int(str(raw).strip())
                elif kind == "float":
                    value = float(str(raw).strip())
                else:
                    value = str(raw)
                config.set(key, value)
        except ValueError:
            messagebox.showerror(
                "OpticalInputSvc", "有数字项格式不对。", parent=win
            )
            return
        config.save()
        if not startup.set_enabled(bool(config.get("startup.auto_run"))):
            messagebox.showwarning(
                "OpticalInputSvc",
                "开机自启设置未写入注册表，其余设置已保存。",
                parent=win,
            )
        if on_saved is not None:
            on_saved()
        messagebox.showinfo("OpticalInputSvc", "已保存。", parent=win)
        win.destroy()

    def restore() -> None:
        if messagebox.askyesno(
            "OpticalInputSvc", "把所有设置恢复为默认值？", parent=win
        ):
            security_data = copy.deepcopy(config.data.get("security", {}))
            config.data = copy.deepcopy(DEFAULTS)
            config.set("security", security_data)
            config.save()
            win.destroy()
            open_settings(root, config, security, version, on_saved)

    ttk.Button(bar, text="保存", command=save).pack(side="right")
    ttk.Button(bar, text="取消", command=win.destroy).pack(side="right", padx=6)
    ttk.Button(bar, text="恢复默认", command=restore).pack(side="left")

    win.protocol("WM_DELETE_WINDOW", win.destroy)
    win.lift()


def _build_fields(frame, config, fields, bindings, controller=None) -> None:
    for row, field in enumerate(fields):
        key, label, kind = field[0], field[1], field[2]
        options = field[3] if len(field) > 3 else None
        value = config.get(key)

        ttk.Label(frame, text=label).grid(
            row=row, column=0, sticky="w", padx=(0, 16), pady=6
        )

        if kind == "action":
            ttk.Button(
                frame,
                text=label,
                command=lambda: _run_action(key, frame, controller),
            ).grid(row=row, column=1, sticky="w", pady=6)
            continue
        if kind == "bool":
            var = tk.BooleanVar(value=bool(value))
            ttk.Checkbutton(frame, variable=var).grid(
                row=row, column=1, sticky="w", pady=6
            )
        elif kind == "choice":
            var = tk.StringVar(value="" if value is None else str(value))
            ttk.Combobox(
                frame,
                textvariable=var,
                values=options or [],
                state="readonly",
                width=28,
            ).grid(row=row, column=1, sticky="w", pady=6)
        else:
            var = tk.StringVar(value="" if value is None else str(value))
            ttk.Entry(frame, textvariable=var, width=30).grid(
                row=row, column=1, sticky="w", pady=6
            )

        bindings.append((key, kind, var))

    frame.columnconfigure(1, weight=1)


def _run_action(key: str, frame, controller) -> None:
    if key == "__train":
        if controller is None:
            messagebox.showinfo(
                "OpticalInputSvc",
                "手势样本需要在主程序里录制。",
                parent=frame,
            )
            return
        from .handtrain import open_trainer

        open_trainer(frame.winfo_toplevel(), controller.preview, controller)


def _build_security(notebook, config, security, bindings) -> None:
    frame = ttk.Frame(notebook, padding=12)
    notebook.add(frame, text="安全")

    ttk.Label(
        frame, text="密码以 PBKDF2-HMAC-SHA256 哈希保存，不存明文。"
    ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

    ttk.Label(frame, text="连续失败锁定（秒）").grid(
        row=1, column=0, sticky="w", padx=(0, 16), pady=6
    )
    lockout = tk.StringVar(
        value=str(config.get("security.lockout_seconds", 30))
    )
    ttk.Entry(frame, textvariable=lockout, width=30).grid(
        row=1, column=1, sticky="w", pady=6
    )
    bindings.append(("security.lockout_seconds", "int", lockout))

    def change_password() -> None:
        if security.has_password:
            current = ask_password(frame, "当前管理密码：")
            if current is None:
                return
            if not security.check(current):
                show_error(frame, "当前密码错误。")
                return
        new = ask_new_password(frame)
        if new is None:
            return
        security.set_password(new)
        messagebox.showinfo(
            "OpticalInputSvc", "密码已更新。", parent=frame
        )

    ttk.Button(frame, text="修改密码…", command=change_password).grid(
        row=2, column=0, sticky="w", pady=(16, 0)
    )
    frame.columnconfigure(1, weight=1)


def _build_about(notebook, version: str) -> None:
    frame = ttk.Frame(notebook, padding=12)
    notebook.add(frame, text="关于")

    ttk.Label(
        frame,
        text=f"OpticalInputSvc {version}",
        font=("Segoe UI", 12, "bold"),
    ).grid(row=0, column=0, sticky="w", pady=(0, 12))
    ttk.Label(frame, text=f"数据目录：{data_dir()}").grid(
        row=1, column=0, sticky="w", pady=3
    )
    ttk.Label(frame, text=f"日志目录：{log_dir()}").grid(
        row=2, column=0, sticky="w", pady=3
    )

    def open_data() -> None:
        try:
            os.startfile(str(data_dir()))
        except OSError:
            pass

    ttk.Button(frame, text="打开数据目录", command=open_data).grid(
        row=3, column=0, sticky="w", pady=(16, 0)
    )


def _build_profiles(notebook, config, on_saved, reopen) -> None:
    frame = ttk.Frame(notebook, padding=12)
    notebook.add(frame, text="档案")

    listbox = tk.Listbox(frame, height=10, exportselection=False)
    listbox.grid(row=0, column=0, columnspan=4, sticky="nsew", pady=(0, 8))
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)

    store = ProfileStore(config)

    def refresh() -> None:
        listbox.delete(0, tk.END)
        for name in store.list():
            listbox.insert(tk.END, name)

    def selected():
        selection = listbox.curselection()
        if not selection:
            return None
        return listbox.get(selection[0])

    def save_current() -> None:
        name = simpledialog.askstring(
            "保存档案",
            "档案名：",
            initialvalue=time.strftime("座位_%Y%m%d_%H%M"),
            parent=frame,
        )
        if not name or not name.strip():
            return
        try:
            store.save(name.strip())
        except OSError as exc:
            messagebox.showerror(
                "OpticalInputSvc", f"保存失败：{exc}", parent=frame
            )
            return
        refresh()
        messagebox.showinfo(
            "OpticalInputSvc", "档案已保存。", parent=frame
        )

    def load_selected() -> None:
        name = selected()
        if not name:
            return
        try:
            store.load(name)
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "OpticalInputSvc", f"载入失败：{exc}", parent=frame
            )
            return
        if on_saved is not None:
            on_saved()
        reopen()

    def delete_selected() -> None:
        name = selected()
        if not name:
            return
        if messagebox.askyesno(
            "OpticalInputSvc", f"删除档案 {name}？", parent=frame
        ):
            store.delete(name)
            refresh()

    def rename_selected() -> None:
        name = selected()
        if not name:
            return
        new = simpledialog.askstring(
            "重命名档案", "新名称：", initialvalue=name, parent=frame
        )
        if not new or not new.strip() or new.strip() == name:
            return
        try:
            store.rename(name, new.strip())
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "OpticalInputSvc", f"重命名失败：{exc}", parent=frame
            )
            return
        refresh()

    ttk.Button(frame, text="保存当前…", command=save_current).grid(
        row=1, column=0, sticky="w"
    )
    ttk.Button(frame, text="载入", command=load_selected).grid(
        row=1, column=1, sticky="w", padx=6
    )
    ttk.Button(frame, text="重命名", command=rename_selected).grid(
        row=1, column=2, sticky="w"
    )
    ttk.Button(frame, text="删除", command=delete_selected).grid(
        row=1, column=3, sticky="w", padx=(6, 0)
    )
    ttk.Label(
        frame,
        text=(
            "档案保存框、9 点标定、指针手感、档位、手势和摄像头设置。"
            "保存前请先点“保存”写入当前设置。"
        ),
        wraplength=620,
    ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(12, 0))

    refresh()
