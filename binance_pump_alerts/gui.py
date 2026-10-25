"""Tkinter window: settings form, Start/Stop buttons, status line and log."""

from __future__ import annotations

import queue
import re
import threading
import webbrowser
import sys
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from . import __version__, notifier, popups, vataga
from .binance_api import BinanceApi
from .monitor import Monitor
from .platform_utils import SleepGuard, open_file
from .settings import APP_NAME, Settings, alerts_log_path, load_settings, save_settings

MAX_LOG_LINES = 500

# label shown in the window -> value stored in settings
DIRECTIONS = {"Growth (+)": "up", "Drop (-), mirror mode": "down", "Growth and drop": "both"}

# (settings key, label, python type, minimum allowed value)
FIELDS = [
    ("Volume filter", None, None, None),
    ("min_volume_million_usdt", "Minimum 24h volume (million USDT)", float, 0.0),
    ("volume_refresh_sec", "Volume list refresh interval (seconds)", int, 1),
    ("Price move filter", None, None, None),
    ("price_change_percent", "Minimum price move (%)", float, 0.01),
    ("window_minutes", "Time window (minutes)", int, 1),
    ("Trade volume spike filter", None, None, None),
    ("volume_spike_ratio", "Min volume spike (x usual volume, 0 = off)", float, 0.0),
    ("volume_spike_baseline_min", "History used as 'usual' volume (minutes)", int, 5),
    ("General", None, None, None),
    ("stale_warning_sec", "Warn if no market data for (seconds)", int, 10),
    ("check_interval_sec", "Price check interval (seconds)", int, 1),
    ("cooldown_minutes", "Alert cooldown per coin (minutes)", int, 0),
    ("popup_auto_close_sec", "Close alert popups after (seconds, 0 = never)", int, 0),
]


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} {__version__}")
        height = min(960, max(self.winfo_screenheight() - 90, 600))  # fit small MacBook screens
        self.geometry(f"680x{height}+40+20")
        self.minsize(600, 600)

        self.api = BinanceApi()
        self.monitor: Monitor | None = None
        self.messages: queue.Queue = queue.Queue()
        self.vars: dict[str, tk.StringVar] = {}
        self.labels: dict[str, str] = {}
        self.types: dict[str, tuple] = {}
        self.autostart_var = tk.BooleanVar(value=False)
        self.sound_var = tk.BooleanVar(value=True)
        self.popup_var = tk.BooleanVar(value=True)
        self.dir_var = tk.StringVar(value="Growth (+)")
        self.toast_var = tk.BooleanVar(value=True)
        self.sleep_var = tk.BooleanVar(value=True)
        self.sleep_guard = SleepGuard()
        self.vtype_var = tk.StringVar(value="F")
        self.vport_var = tk.StringVar(value="5005")

        self._build_widgets()
        settings = load_settings()
        self._show_settings(settings)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if sys.platform == "darwin":
            self.createcommand("::tk::mac::Quit", self._on_close)  # Cmd+Q
        notifier.alert_listener = lambda symbol, title, message, direction=0: self.messages.put(("popup", (symbol, title, message, direction)))
        self.after(200, self._drain_messages)
        if settings.autostart_monitoring:
            self.after(500, self.start_monitoring)

    # ------------------------------------------------------------------ UI

    def _build_widgets(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="Binance Futures Pump Alerts", font=("Helvetica", 17, "bold")).pack(anchor="w")
        self.status_var = tk.StringVar(value="Stopped")
        ttk.Label(root, textvariable=self.status_var, wraplength=520).pack(anchor="w", pady=(4, 10))

        form = ttk.Frame(root)
        form.pack(fill="x")
        form.columnconfigure(0, weight=1)

        row = 0
        for key, label, py_type, minimum in FIELDS:
            if label is None:  # section header
                ttk.Label(form, text=key, font=("Helvetica", 12, "bold")).grid(
                    row=row, column=0, columnspan=2, sticky="w", pady=(10, 2)
                )
            else:
                var = tk.StringVar()
                self.vars[key] = var
                self.labels[key] = label
                self.types[key] = (py_type, minimum)
                ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=2)
                ttk.Entry(form, textvariable=var, width=14).grid(row=row, column=1, sticky="e", pady=2)
            row += 1

        drow = ttk.Frame(root)
        drow.pack(fill="x", pady=(8, 0))
        ttk.Label(drow, text="Alert on").pack(side="left")
        ttk.Combobox(
            drow, textvariable=self.dir_var, values=list(DIRECTIONS), width=22, state="readonly"
        ).pack(side="right")

        ttk.Checkbutton(
            root, text="Start monitoring automatically when the app opens", variable=self.autostart_var
        ).pack(anchor="w", pady=(12, 0))
        ttk.Checkbutton(root, text="Play a sound on every alert", variable=self.sound_var).pack(anchor="w")
        ttk.Checkbutton(
            root, text="Show a popup window with buttons (always on top)", variable=self.popup_var
        ).pack(anchor="w")
        ttk.Checkbutton(root, text="Show a macOS notification banner", variable=self.toast_var).pack(anchor="w")
        ttk.Checkbutton(root, text="Keep the Mac awake while monitoring", variable=self.sleep_var).pack(anchor="w")

        ttk.Label(root, text="Vataga.terminal", font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(10, 0))
        vrow = ttk.Frame(root)
        vrow.pack(fill="x")
        ttk.Label(vrow, text="Market").pack(side="left")
        ttk.Combobox(vrow, textvariable=self.vtype_var, values=["F", "S"], width=3, state="readonly").pack(side="left", padx=4)
        ttk.Label(vrow, text="(F = futures, S = spot)   Port").pack(side="left")
        ttk.Entry(vrow, textvariable=self.vport_var, width=7).pack(side="left", padx=4)
        ttk.Button(vrow, text="Test Vataga", command=self.test_vataga).pack(side="right")

        buttons = ttk.Frame(root)
        buttons.pack(fill="x", pady=12)
        self.save_button = ttk.Button(buttons, text="Save settings", command=self.save_clicked)
        self.save_button.pack(side="left")
        self.start_button = ttk.Button(buttons, text="Start monitoring", command=self.start_monitoring)
        self.start_button.pack(side="left", padx=6)
        self.stop_button = ttk.Button(buttons, text="Stop", command=self.stop_monitoring, state="disabled")
        self.stop_button.pack(side="left")
        ttk.Button(buttons, text="Test notification", command=self.test_notification).pack(side="right")
        ttk.Button(buttons, text="Alerts log", command=self.open_alerts_log).pack(side="right", padx=6)

        bold = ("Helvetica", 12, "bold")
        ttk.Label(root, text="Recent alerts (double-click = open in Vataga)", font=bold).pack(anchor="w")
        hist = ttk.Frame(root)
        hist.pack(fill="x")
        self.history = ttk.Treeview(hist, columns=("time", "alert", "details"), show="headings", height=6)
        for col, text, width in (("time", "Time", 70), ("alert", "Alert", 130), ("details", "Details", 300)):
            self.history.heading(col, text=text)
            self.history.column(col, width=width, anchor="w")
        bar = ttk.Scrollbar(hist, orient="vertical", command=self.history.yview)
        self.history.configure(yscrollcommand=bar.set)
        self.history.pack(side="left", fill="x", expand=True)
        bar.pack(side="right", fill="y")
        self.history.bind("<Double-1>", lambda _e: self.history_open_vataga())
        hbtn = ttk.Frame(root)
        hbtn.pack(fill="x", pady=(4, 4))
        ttk.Button(hbtn, text="Open in Vataga", command=self.history_open_vataga).pack(side="left")
        ttk.Button(hbtn, text="Open on Binance", command=self.history_open_binance).pack(side="left", padx=6)
        ttk.Button(hbtn, text="Close all popups", command=popups.close_all).pack(side="right")
        self.history_status = tk.StringVar(value="")
        ttk.Label(root, textvariable=self.history_status).pack(anchor="w", pady=(0, 6))
        self._load_history()

        ttk.Label(root, text="Log", font=("Helvetica", 12, "bold")).pack(anchor="w")
        self.log_widget = scrolledtext.ScrolledText(root, height=12, state="disabled", wrap="word")
        self.log_widget.pack(fill="both", expand=True)

    def _show_settings(self, s: Settings) -> None:
        for key, var in self.vars.items():
            value = getattr(s, key)
            var.set(f"{value:g}" if isinstance(value, float) else str(value))
        self.autostart_var.set(s.autostart_monitoring)
        self.sound_var.set(s.sound_enabled)
        self.popup_var.set(s.popup_enabled)
        self.dir_var.set(next((k for k, v in DIRECTIONS.items() if v == s.alert_direction), "Growth (+)"))
        self.toast_var.set(s.toast_enabled)
        self.sleep_var.set(s.prevent_sleep)
        self.vtype_var.set(s.vataga_type)
        self.vport_var.set(str(s.vataga_port))

    def _read_form(self) -> Settings | None:
        """Validate the form. Shows an error dialog and returns None if something is invalid."""
        values: dict = {}
        for key, var in self.vars.items():
            py_type, minimum = self.types[key]
            raw = var.get().strip().replace(",", ".")
            try:
                value = py_type(raw)
            except ValueError:
                messagebox.showerror(APP_NAME, f"'{self.labels[key]}' must be a valid number.")
                return None
            if value < minimum:
                messagebox.showerror(APP_NAME, f"'{self.labels[key]}' must be at least {minimum:g}.")
                return None
            values[key] = value
        try:
            port = int(self.vport_var.get().strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror(APP_NAME, "Port must be a number from 1 to 65535.")
            return None
        return Settings(
            autostart_monitoring=self.autostart_var.get(),
            sound_enabled=self.sound_var.get(),
            popup_enabled=self.popup_var.get(),
            alert_direction=DIRECTIONS.get(self.dir_var.get(), "up"),
            toast_enabled=self.toast_var.get(),
            prevent_sleep=self.sleep_var.get(),
            vataga_type=self.vtype_var.get() or "F",
            vataga_port=port,
            **values,
        )

    # ------------------------------------------------------------- actions

    def save_clicked(self) -> Settings | None:
        settings = self._read_form()
        if settings is None:
            return None
        try:
            save_settings(settings)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not save settings: {exc}")
            return None
        self.add_log("Settings saved")
        return settings

    def start_monitoring(self) -> None:
        if self.monitor is not None and self.monitor.is_alive():
            return
        if self.save_clicked() is None:
            return
        self.monitor = Monitor(
            api=self.api,
            load_settings=load_settings,
            notify=lambda symbol, change, price, window, ratio=None: notifier.show_alert(
                symbol, change, price, window, sound=load_settings().sound_enabled, volume_ratio=ratio
            ),
            log=lambda text: self.messages.put(("log", text)),
            set_status=lambda text: self.messages.put(("status", text)),
            warn=lambda title, text: notifier.show_warning(title, text, sound=load_settings().sound_enabled),
        )
        if load_settings().prevent_sleep:
            self.sleep_guard.start()
        self.monitor.start()
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.status_var.set("Starting...")

    def stop_monitoring(self) -> None:
        if self.monitor is not None:
            self.monitor.stop()
        self.sleep_guard.stop()
        self.stop_button.config(state="disabled")

    _ALERT_RE = re.compile(r"^(\S+ \S+)\s+ALERT (\S+) ([+-][\d.]+%) in (\d+) min, price ([^,\s]+)")

    def _load_history(self) -> None:
        """Show alerts from previous runs (read from alerts.log)."""
        try:
            lines = alerts_log_path().read_text(encoding="utf-8").splitlines()[-300:]
        except OSError:
            return
        for line in lines:
            m = self._ALERT_RE.match(line)
            if m:
                stamp, symbol, change, minutes, price = m.groups()
                self._add_history(stamp[11:], symbol, f"{symbol}  {change}", f"in {minutes} min, price {price}")

    def _add_history(self, stamp: str, symbol: str, title: str, details: str) -> None:
        self.history.insert("", 0, values=(stamp, title, details), tags=(symbol,))
        for old in self.history.get_children()[500:]:
            self.history.delete(old)

    def _selected_symbol(self) -> str | None:
        selection = self.history.selection()
        if not selection:
            self.history_status.set("Select an alert in the list first.")
            return None
        tags = self.history.item(selection[0], "tags")
        return tags[0] if tags else None

    def history_open_vataga(self) -> None:
        symbol = self._selected_symbol()
        if symbol is None:
            return
        self.history_status.set(f"Sending {symbol} to Vataga.terminal...")
        settings = load_settings()

        def work() -> None:
            ok, message = vataga.open_symbol(symbol, settings)
            self.messages.put(("hstatus", f"{symbol} opened in Vataga.terminal" if ok else message))

        threading.Thread(target=work, daemon=True).start()

    def history_open_binance(self) -> None:
        symbol = self._selected_symbol()
        if symbol is not None:
            webbrowser.open(f"https://www.binance.com/en/futures/{symbol}")

    def test_vataga(self) -> None:
        settings = self._read_form()
        if settings is None:
            return
        ok, message = vataga.open_symbol("BTCUSDT", settings)
        if ok:
            messagebox.showinfo(APP_NAME, "Request sent. BTCUSDT should open in the window linked to 'Screener'.")
        else:
            messagebox.showerror(APP_NAME, message)

    def test_notification(self) -> None:
        try:
            notifier.show_test(sound=self.sound_var.get())
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not show notification: {exc}")

    def open_alerts_log(self) -> None:
        """Open alerts.log (every alert with time, coin, growth and price) in the default text editor."""
        path = alerts_log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)
            open_file(path)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not open {path}: {exc}")

    # ---------------------------------------------------------------- misc

    def add_log(self, text: str) -> None:
        self.log_widget.config(state="normal")
        self.log_widget.insert("end", f"{time.strftime('%H:%M:%S')}  {text}\n")
        lines = int(self.log_widget.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            self.log_widget.delete("1.0", f"{lines - MAX_LOG_LINES + 1}.0")
        self.log_widget.see("end")
        self.log_widget.config(state="disabled")

    def _drain_messages(self) -> None:
        """Move messages from the monitor thread into the UI (runs on the UI thread).
        One bad message must never stop the loop, otherwise the whole UI stops updating."""
        while True:
            try:
                kind, text = self.messages.get_nowait()
            except queue.Empty:
                break
            try:
                if kind == "log":
                    self.add_log(text)
                elif kind == "popup":
                    symbol, title, message, direction = text
                    try:
                        if symbol:  # warnings (no coin) are shown as a popup only
                            self._add_history(time.strftime("%H:%M:%S"), symbol, title, message.replace("\n", " | "))
                    finally:
                        popups.show_popup(self, symbol, title, message, direction)
                elif kind == "hstatus":
                    self.history_status.set(text)
                else:
                    self.status_var.set(text)
            except Exception as exc:  # keep going; the problem is recorded in the log
                try:
                    self.add_log(f"UI error ({kind}): {exc}")
                except Exception:
                    pass

        if self.monitor is not None and not self.monitor.is_alive():
            self.monitor = None
            self.sleep_guard.stop()
            self.start_button.config(state="normal")
            self.stop_button.config(state="disabled")
        self.after(200, self._drain_messages)

    def _on_close(self) -> None:
        if self.monitor is not None:
            self.monitor.stop()
        self.sleep_guard.stop()
        self.destroy()


def main() -> None:
    App().mainloop()
