"""
CyberNet Monitor - A cyberpunk-themed Windows network monitoring application.
Requirements: pip install customtkinter psutil pystray pillow
"""

import customtkinter as ctk
import psutil
import threading
import time
import csv
import os
import subprocess
import datetime
import sys
from collections import deque
from tkinter import Canvas, messagebox

# --- Attempt to import pystray and PIL for tray icon ---
try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

# ─────────────────────────────────────────────
#  CONSTANTS & THEME
# ─────────────────────────────────────────────
BG         = "#000000"
BG2        = "#0a0a0a"
BG3        = "#0f0f0f"
CYAN       = "#00FFFF"
CYAN_DIM   = "#007777"
CYAN_GLOW  = "#00e5e5"
GREEN      = "#00FF88"
RED        = "#FF2055"
YELLOW     = "#FFD700"
GRAY       = "#1a1a1a"
GRAY2      = "#2a2a2a"
TEXT       = "#c8f7f7"
MONO_FONT  = ("Courier New", 11)
TITLE_FONT = ("Courier New", 20, "bold")
LABEL_FONT = ("Courier New", 12, "bold")
SMALL_FONT = ("Courier New", 10)

POLL_INTERVAL_ACTIVE = 1       # seconds when window is visible
POLL_INTERVAL_TRAY   = 5       # seconds when minimized to tray
HISTORY_LEN          = 30      # seconds of graph history
SUMMARY_INTERVAL     = 15      # seconds between terminal summaries

# ─────────────────────────────────────────────
#  HELPER FUNCTIONS
# ─────────────────────────────────────────────

def convert_bytes(num_bytes: float, precision: int = 2) -> str:
    """Convert raw bytes to a human-readable string (KB / MB / GB)."""
    if num_bytes < 0:
        num_bytes = 0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024.0:
            return f"{num_bytes:.{precision}f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.{precision}f} PB"


def convert_bytes_raw(num_bytes: float, unit: str = "MB") -> float:
    """Return numeric value in the requested unit."""
    divisors = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
    return num_bytes / divisors.get(unit, 1024**2)


def run_netsh_disconnect():
    """Drop the Wi-Fi connection via netsh."""
    try:
        subprocess.run(
            ["netsh", "wlan", "disconnect"],
            capture_output=True, check=False
        )
    except Exception as e:
        print(f"[netsh] Error: {e}")


def windows_notification(title: str, message: str):
    """Fire a Windows toast notification using PowerShell."""
    ps_script = (
        f"Add-Type -AssemblyName System.Windows.Forms;"
        f"$n = New-Object System.Windows.Forms.NotifyIcon;"
        f"$n.Icon = [System.Drawing.SystemIcons]::Warning;"
        f"$n.Visible = $true;"
        f"$n.ShowBalloonTip(5000, '{title}', '{message}', "
        f"[System.Windows.Forms.ToolTipIcon]::Warning);"
        f"Start-Sleep -s 6; $n.Dispose()"
    )
    try:
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-Command", ps_script],
            creationflags=subprocess.CREATE_NO_WINDOW
        )
    except Exception as e:
        print(f"[Notification] {e}")


# ─────────────────────────────────────────────
#  STARTUP DIALOG  –  CSV logging prompt
# ─────────────────────────────────────────────

class StartupDialog(ctk.CTkToplevel):
    """Ask the user whether to enable CSV session logging."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("CyberNet Monitor — Session Init")
        self.geometry("460x220")
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self.grab_set()
        self.result = None

        ctk.CTkLabel(
            self, text="CYBERNETMONITOR", font=("Courier New", 18, "bold"),
            text_color=CYAN
        ).pack(pady=(22, 4))

        ctk.CTkLabel(
            self,
            text="Enable persistent session logging to CSV?",
            font=LABEL_FONT, text_color=TEXT
        ).pack(pady=6)

        btn_frame = ctk.CTkFrame(self, fg_color=BG)
        btn_frame.pack(pady=14)

        yes_btn = ctk.CTkButton(
            btn_frame, text="[ YES ]", width=140, height=40,
            font=("Courier New", 13, "bold"),
            fg_color=BG, border_color=CYAN, border_width=2,
            text_color=CYAN, hover_color=CYAN_DIM,
            corner_radius=8,
            command=self._yes
        )
        yes_btn.pack(side="left", padx=12)

        no_btn = ctk.CTkButton(
            btn_frame, text="[ NO ]", width=140, height=40,
            font=("Courier New", 13, "bold"),
            fg_color=BG, border_color=GRAY2, border_width=2,
            text_color=GRAY2, hover_color="#111111",
            corner_radius=8,
            command=self._no
        )
        no_btn.pack(side="left", padx=12)

        self.protocol("WM_DELETE_WINDOW", self._no)

    def _yes(self):
        self.result = True
        self.destroy()

    def _no(self):
        self.result = False
        self.destroy()


# ─────────────────────────────────────────────
#  SPEED GRAPH CANVAS WIDGET
# ─────────────────────────────────────────────

class SpeedGraph(Canvas):
    """Lightweight line graph (no matplotlib) for the last N seconds."""

    def __init__(self, parent, history_len=HISTORY_LEN, **kwargs):
        super().__init__(
            parent,
            bg=BG, highlightthickness=1,
            highlightbackground=CYAN_DIM,
            **kwargs
        )
        self.history_len = history_len
        self.dl_history  = deque([0.0] * history_len, maxlen=history_len)
        self.ul_history  = deque([0.0] * history_len, maxlen=history_len)
        self.bind("<Configure>", lambda e: self._redraw())

    def push(self, dl_bytes_s: float, ul_bytes_s: float):
        self.dl_history.append(dl_bytes_s)
        self.ul_history.append(ul_bytes_s)
        self._redraw()

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 10 or h < 10:
            return

        pad = 6
        inner_w = w - 2 * pad
        inner_h = h - 2 * pad

        # Grid lines
        for i in range(1, 4):
            y = pad + inner_h * i // 4
            self.create_line(pad, y, w - pad, y, fill="#0d2222", dash=(3, 4))

        max_val = max(max(self.dl_history, default=1),
                      max(self.ul_history, default=1), 1)

        def points(series):
            pts = []
            n = len(series)
            for i, v in enumerate(series):
                x = pad + inner_w * i / (n - 1) if n > 1 else pad
                y = pad + inner_h * (1 - v / max_val)
                pts.extend([x, y])
            return pts

        dl_pts = points(self.dl_history)
        ul_pts = points(self.ul_history)

        if len(dl_pts) >= 4:
            self.create_line(*dl_pts, fill=CYAN,   width=2, smooth=True)
        if len(ul_pts) >= 4:
            self.create_line(*ul_pts, fill=GREEN,  width=1, smooth=True, dash=(4, 3))

        # Legend
        self.create_text(pad + 4, pad + 4, anchor="nw",
                         text="▼ DL", fill=CYAN,  font=("Courier New", 8))
        self.create_text(pad + 52, pad + 4, anchor="nw",
                         text="▲ UL", fill=GREEN, font=("Courier New", 8))


# ─────────────────────────────────────────────
#  MAIN APPLICATION CLASS
# ─────────────────────────────────────────────

class CyberNetMonitor(ctk.CTk):

    def __init__(self, csv_logging: bool):
        super().__init__()

        # ── State ──────────────────────────────
        self.csv_logging      = csv_logging
        self.csv_file         = None
        self.csv_writer       = None
        self._running         = True
        self._minimized_tray  = False
        self._poll_interval   = POLL_INTERVAL_ACTIVE
        self._last_net        = psutil.net_io_counters()
        self._session_dl      = 0.0   # bytes
        self._session_ul      = 0.0
        self._limit_bytes     = None  # None = unlimited
        self._limit_reached   = False
        self._service_active  = True  # Kill-switch state
        self._last_summary_t  = time.time()
        self._tray_icon       = None
        self._tray_thread     = None
        self._lock            = threading.Lock()

        # ── Window Setup ───────────────────────
        ctk.set_appearance_mode("dark")
        self.title("CyberNet Monitor")
        self.geometry("820x680")
        self.minsize(720, 580)
        self.configure(fg_color=BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._init_csv()
        self._build_ui()
        self._start_monitor_thread()

    # ── CSV ─────────────────────────────────────────────────────────────

    def _init_csv(self):
        if not self.csv_logging:
            return
        ts  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        fn  = f"cybernet_session_{ts}.csv"
        self.csv_file   = open(fn, "w", newline="", encoding="utf-8")
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow(
            ["timestamp", "dl_speed_bps", "ul_speed_bps",
             "session_dl_mb", "session_ul_mb"]
        )

    def _write_csv_row(self, dl_s, ul_s):
        if self.csv_writer:
            self.csv_writer.writerow([
                datetime.datetime.now().isoformat(),
                f"{dl_s:.2f}", f"{ul_s:.2f}",
                f"{convert_bytes_raw(self._session_dl):.4f}",
                f"{convert_bytes_raw(self._session_ul):.4f}",
            ])

    # ── UI CONSTRUCTION ─────────────────────────────────────────────────

    def _build_ui(self):
        # ── Header ──────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color=BG, height=60)
        hdr.pack(fill="x", padx=16, pady=(14, 0))

        ctk.CTkLabel(
            hdr, text="◈  CYBERNET MONITOR", font=TITLE_FONT,
            text_color=CYAN
        ).pack(side="left", padx=6)

        self.status_dot = ctk.CTkLabel(
            hdr, text="●  ONLINE", font=LABEL_FONT, text_color=GREEN
        )
        self.status_dot.pack(side="right", padx=10)

        self.clock_label = ctk.CTkLabel(
            hdr, text="", font=SMALL_FONT, text_color=CYAN_DIM
        )
        self.clock_label.pack(side="right", padx=16)

        # ── Separator ───────────────────────────
        ctk.CTkFrame(self, fg_color=CYAN_DIM, height=1).pack(fill="x", padx=16, pady=6)

        # ── Main Content Area ───────────────────
        content = ctk.CTkFrame(self, fg_color=BG)
        content.pack(fill="both", expand=True, padx=16, pady=4)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(1, weight=1)

        # ── Speed Cards ─────────────────────────
        card_frame = ctk.CTkFrame(content, fg_color=BG)
        card_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        card_frame.columnconfigure((0, 1, 2, 3), weight=1)

        self.dl_speed_lbl  = self._speed_card(card_frame, "▼ DOWNLOAD", CYAN,  0)
        self.ul_speed_lbl  = self._speed_card(card_frame, "▲ UPLOAD",   GREEN, 1)
        self.dl_total_lbl  = self._speed_card(card_frame, "SESSION ▼",  CYAN_DIM, 2)
        self.ul_total_lbl  = self._speed_card(card_frame, "SESSION ▲",  "#007740", 3)

        # ── Graph ───────────────────────────────
        graph_wrap = ctk.CTkFrame(content, fg_color=BG3, corner_radius=8,
                                  border_width=1, border_color=CYAN_DIM)
        graph_wrap.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=4)

        ctk.CTkLabel(
            graph_wrap, text="LIVE SPEED GRAPH  (30s)", font=SMALL_FONT,
            text_color=CYAN_DIM
        ).pack(anchor="nw", padx=8, pady=(6, 0))

        self.graph = SpeedGraph(graph_wrap, height=140)
        self.graph.pack(fill="both", expand=True, padx=8, pady=8)

        # ── Progress / Limit Panel ───────────────
        right_panel = ctk.CTkFrame(content, fg_color=BG3, corner_radius=8,
                                   border_width=1, border_color=CYAN_DIM)
        right_panel.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=4)

        ctk.CTkLabel(
            right_panel, text="DATA LIMIT CONTROL", font=SMALL_FONT,
            text_color=CYAN_DIM
        ).pack(anchor="nw", padx=10, pady=(8, 2))

        limit_row = ctk.CTkFrame(right_panel, fg_color=BG3)
        limit_row.pack(fill="x", padx=10, pady=4)

        self.limit_entry = ctk.CTkEntry(
            limit_row, placeholder_text="Limit (e.g. 500)",
            font=MONO_FONT, width=110,
            fg_color=BG, border_color=CYAN_DIM, text_color=CYAN
        )
        self.limit_entry.pack(side="left", padx=(0, 6))

        self.limit_unit = ctk.CTkOptionMenu(
            limit_row, values=["MB", "GB"], width=70,
            font=MONO_FONT, fg_color=BG, button_color=CYAN_DIM,
            text_color=CYAN, dropdown_fg_color=BG, dropdown_text_color=CYAN
        )
        self.limit_unit.pack(side="left", padx=(0, 6))

        set_btn = self._cyber_button(limit_row, "SET", self._apply_limit,
                                     width=60, color=CYAN)
        set_btn.pack(side="left")

        clear_btn = self._cyber_button(limit_row, "CLR", self._clear_limit,
                                       width=60, color=YELLOW)
        clear_btn.pack(side="left", padx=(6, 0))

        self.limit_label = ctk.CTkLabel(
            right_panel, text="Limit: UNLIMITED", font=SMALL_FONT,
            text_color=GRAY2
        )
        self.limit_label.pack(anchor="nw", padx=10)

        self.usage_bar = ctk.CTkProgressBar(
            right_panel, width=220, height=14,
            fg_color=GRAY, progress_color=CYAN,
            corner_radius=4
        )
        self.usage_bar.set(0)
        self.usage_bar.pack(padx=10, pady=8, fill="x")

        self.usage_pct_label = ctk.CTkLabel(
            right_panel, text="0.00% used", font=SMALL_FONT, text_color=CYAN_DIM
        )
        self.usage_pct_label.pack(anchor="nw", padx=10)

        # ── DL / UL bar ─────────────────────────
        ctk.CTkLabel(
            right_panel, text="SPEED BARS", font=SMALL_FONT, text_color=CYAN_DIM
        ).pack(anchor="nw", padx=10, pady=(14, 0))

        self.dl_bar = ctk.CTkProgressBar(right_panel, height=10,
                                         fg_color=GRAY, progress_color=CYAN,
                                         corner_radius=3)
        self.dl_bar.set(0)
        self.dl_bar.pack(padx=10, pady=(4, 2), fill="x")

        self.ul_bar = ctk.CTkProgressBar(right_panel, height=10,
                                         fg_color=GRAY, progress_color=GREEN,
                                         corner_radius=3)
        self.ul_bar.set(0)
        self.ul_bar.pack(padx=10, pady=(2, 8), fill="x")

        # ── Terminal Log ─────────────────────────
        log_frame = ctk.CTkFrame(self, fg_color=BG3, corner_radius=8,
                                 border_width=1, border_color=CYAN_DIM)
        log_frame.pack(fill="x", padx=16, pady=(4, 4))

        ctk.CTkLabel(
            log_frame, text="TERMINAL LOG", font=SMALL_FONT, text_color=CYAN_DIM
        ).pack(anchor="nw", padx=10, pady=(6, 0))

        self.terminal = ctk.CTkTextbox(
            log_frame, height=130, font=MONO_FONT,
            fg_color=BG, text_color=CYAN,
            border_width=0, corner_radius=0,
            wrap="none", activate_scrollbars=True
        )
        self.terminal.pack(fill="x", padx=8, pady=(2, 8))
        self.terminal.configure(state="disabled")

        # ── Bottom Buttons ───────────────────────
        btn_row = ctk.CTkFrame(self, fg_color=BG)
        btn_row.pack(fill="x", padx=16, pady=(0, 12))

        self.kill_btn = self._cyber_button(
            btn_row, "⏹  KILL SERVICE", self._kill_service,
            width=180, height=38, color=RED
        )
        self.kill_btn.pack(side="left", padx=(0, 10))

        self.restart_btn = self._cyber_button(
            btn_row, "▶  RESTART SERVICE", self._restart_service,
            width=190, height=38, color=GREEN
        )
        self.restart_btn.pack(side="left", padx=(0, 10))
        self.restart_btn.configure(state="disabled")

        tray_btn = self._cyber_button(
            btn_row, "⊟  MINIMIZE TO TRAY", self._minimize_to_tray,
            width=190, height=38, color=CYAN_DIM
        )
        tray_btn.pack(side="left")

        if self.csv_logging:
            ctk.CTkLabel(
                btn_row, text="● CSV LOGGING ACTIVE", font=SMALL_FONT,
                text_color=GREEN
            ).pack(side="right", padx=8)

        # Seed terminal
        self._log_terminal("CyberNet Monitor initialised. Monitoring started.")
        if self.csv_logging:
            self._log_terminal("CSV session logging: ENABLED")
        else:
            self._log_terminal("CSV session logging: DISABLED")

        self._update_clock()

    def _speed_card(self, parent, label, color, col):
        frame = ctk.CTkFrame(parent, fg_color=BG3, corner_radius=8,
                              border_width=1, border_color=color)
        frame.grid(row=0, column=col, padx=5, pady=4, sticky="ew")
        ctk.CTkLabel(frame, text=label, font=SMALL_FONT, text_color=color).pack(pady=(8, 0))
        val_lbl = ctk.CTkLabel(frame, text="0.00 B/s", font=("Courier New", 16, "bold"),
                                text_color=color)
        val_lbl.pack(pady=(2, 8))
        return val_lbl

    def _cyber_button(self, parent, text, cmd, width=120, height=34, color=CYAN):
        btn = ctk.CTkButton(
            parent, text=text, command=cmd,
            width=width, height=height,
            font=("Courier New", 11, "bold"),
            fg_color=BG, border_color=color, border_width=2,
            text_color=color, hover_color="#0d1a1a",
            corner_radius=6
        )
        return btn

    # ── TERMINAL LOG ──────────────────────────────────────────────────

    def _log_terminal(self, msg: str):
        ts  = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        self.terminal.configure(state="normal")
        self.terminal.insert("end", line)
        self.terminal.see("end")
        self.terminal.configure(state="disabled")

    # ── CLOCK ─────────────────────────────────────────────────────────

    def _update_clock(self):
        now = datetime.datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
        self.clock_label.configure(text=now)
        self.after(1000, self._update_clock)

    # ── LIMIT CONTROLS ────────────────────────────────────────────────

    def _apply_limit(self):
        raw = self.limit_entry.get().strip()
        if not raw:
            messagebox.showwarning("Input Error", "Please enter a numeric limit.", parent=self)
            return
        try:
            val = float(raw)
        except ValueError:
            messagebox.showwarning("Input Error", "Invalid number.", parent=self)
            return
        unit = self.limit_unit.get()
        factor = 1024**2 if unit == "MB" else 1024**3
        with self._lock:
            self._limit_bytes  = val * factor
            self._limit_reached = False
        self.limit_label.configure(text=f"Limit: {val} {unit}", text_color=YELLOW)
        self._log_terminal(f"Data limit SET: {val} {unit}")
        if TRAY_AVAILABLE:
            self._start_tray()

    def _clear_limit(self):
        with self._lock:
            self._limit_bytes  = None
            self._limit_reached = False
        self.limit_label.configure(text="Limit: UNLIMITED", text_color=GRAY2)
        self.usage_bar.set(0)
        self.usage_pct_label.configure(text="0.00% used")
        self._log_terminal("Data limit CLEARED — running unlimited.")
        self._stop_tray()

    # ── MONITOR THREAD ────────────────────────────────────────────────

    def _start_monitor_thread(self):
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True
        )
        self._monitor_thread.start()

    def _monitor_loop(self):
        peak_dl = peak_ul = 0.0
        summary_timer = 0

        while self._running:
            interval = self._poll_interval

            if not self._service_active:
                time.sleep(interval)
                continue

            try:
                net_now = psutil.net_io_counters()
            except Exception:
                time.sleep(interval)
                continue

            with self._lock:
                prev = self._last_net
                dl_s = (net_now.bytes_recv - prev.bytes_recv) / interval
                ul_s = (net_now.bytes_sent - prev.bytes_sent) / interval
                dl_s = max(dl_s, 0)
                ul_s = max(ul_s, 0)
                self._last_net    = net_now
                self._session_dl += dl_s * interval
                self._session_ul += ul_s * interval
                limit   = self._limit_bytes
                ses_dl  = self._session_dl
                ses_ul  = self._session_ul
                reached = self._limit_reached

            peak_dl = max(peak_dl, dl_s)
            peak_ul = max(peak_ul, ul_s)

            # CSV row
            if self.csv_logging:
                self._write_csv_row(dl_s, ul_s)

            # Limit check
            if limit and not reached:
                total_used = ses_dl + ses_ul
                if total_used >= limit:
                    with self._lock:
                        self._limit_reached = True
                    self.after(0, self._trigger_kill_switch)

            # Summary log every SUMMARY_INTERVAL seconds
            summary_timer += interval
            if summary_timer >= SUMMARY_INTERVAL:
                summary_timer = 0
                msg = (
                    f"SUMMARY | DL: {convert_bytes(ses_dl)} | "
                    f"UL: {convert_bytes(ses_ul)} | "
                    f"Peak↓: {convert_bytes(peak_dl)}/s | "
                    f"Peak↑: {convert_bytes(peak_ul)}/s"
                )
                if not self._minimized_tray:
                    self.after(0, lambda m=msg: self._log_terminal(m))

            # Update GUI (only if not tray-minimized)
            if not self._minimized_tray:
                self.after(0, lambda d=dl_s, u=ul_s, sd=ses_dl, su=ses_ul,
                           lim=limit: self._update_ui(d, u, sd, su, lim))

            # Update tray icon if limit active
            if TRAY_AVAILABLE and self._tray_icon and limit:
                pct = min((ses_dl + ses_ul) / limit, 1.0)
                self._update_tray_icon(pct)

            time.sleep(interval)

    def _update_ui(self, dl_s, ul_s, ses_dl, ses_ul, limit):
        # Speed labels
        self.dl_speed_lbl.configure(text=f"{convert_bytes(dl_s)}/s")
        self.ul_speed_lbl.configure(text=f"{convert_bytes(ul_s)}/s")
        self.dl_total_lbl.configure(text=convert_bytes(ses_dl))
        self.ul_total_lbl.configure(text=convert_bytes(ses_ul))

        # Graph
        self.graph.push(dl_s, ul_s)

        # Speed bars (max assumed 10 MB/s for normalisation)
        MAX_SPEED = 10 * 1024 * 1024
        self.dl_bar.set(min(dl_s / MAX_SPEED, 1.0))
        self.ul_bar.set(min(ul_s / MAX_SPEED, 1.0))

        # Usage bar
        if limit:
            total = ses_dl + ses_ul
            pct   = min(total / limit, 1.0)
            self.usage_bar.set(pct)
            color = CYAN if pct < 0.75 else (YELLOW if pct < 0.90 else RED)
            self.usage_bar.configure(progress_color=color)
            self.usage_pct_label.configure(
                text=f"{pct*100:.2f}% used  ({convert_bytes(total)} / {convert_bytes(limit)})"
            )

    # ── KILL SWITCH ──────────────────────────────────────────────────

    def _trigger_kill_switch(self):
        self._log_terminal("⚠  DATA LIMIT REACHED — Disconnecting Wi-Fi...")
        self.status_dot.configure(text="●  LIMIT HIT", text_color=RED)
        run_netsh_disconnect()
        windows_notification(
            "CyberNet Monitor — Limit Reached",
            "Your data limit has been hit. Wi-Fi disconnected."
        )
        self._log_terminal("Wi-Fi disconnected via netsh wlan disconnect.")

    # ── KILL / RESTART SERVICE ────────────────────────────────────────

    def _kill_service(self):
        if not self._service_active:
            return
        self._service_active = False
        self.kill_btn.configure(state="disabled")
        self.restart_btn.configure(state="normal")
        self.status_dot.configure(text="●  SERVICE STOPPED", text_color=RED)
        self._log_terminal("▶ Service STOPPED by user.")
        # Reset speed displays
        self.dl_speed_lbl.configure(text="-- --")
        self.ul_speed_lbl.configure(text="-- --")
        self.dl_bar.set(0)
        self.ul_bar.set(0)

    def _restart_service(self):
        if self._service_active:
            return
        self._service_active = True
        # Reset baseline
        with self._lock:
            self._last_net = psutil.net_io_counters()
        self.kill_btn.configure(state="normal")
        self.restart_btn.configure(state="disabled")
        self.status_dot.configure(text="●  ONLINE", text_color=GREEN)
        self._log_terminal("▶ Service RESTARTED by user.")

    # ── TRAY ICON ─────────────────────────────────────────────────────

    def _make_tray_image(self, pct: float = 0.0):
        size = 64
        img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Background circle
        draw.ellipse([2, 2, 62, 62], fill=(0, 30, 30), outline=(0, 200, 200), width=2)
        # Arc for usage
        if pct > 0:
            color = (0, 255, 255) if pct < 0.75 else (255, 215, 0) if pct < 0.90 else (255, 32, 85)
            end_angle = int(-90 + 360 * pct)
            draw.arc([6, 6, 58, 58], start=-90, end=end_angle, fill=color, width=6)
        return img

    def _start_tray(self):
        if not TRAY_AVAILABLE or self._tray_icon:
            return
        img  = self._make_tray_image()
        menu = pystray.Menu(
            pystray.MenuItem("Open CyberNet Monitor", self._restore_from_tray),
            pystray.MenuItem("Quit", self._quit_from_tray)
        )
        self._tray_icon = pystray.Icon("CyberNet", img, "CyberNet Monitor", menu)
        self._tray_thread = threading.Thread(target=self._tray_icon.run, daemon=True)
        self._tray_thread.start()

    def _stop_tray(self):
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None

    def _update_tray_icon(self, pct: float):
        if self._tray_icon:
            try:
                self._tray_icon.icon = self._make_tray_image(pct)
            except Exception:
                pass

    def _minimize_to_tray(self):
        if not TRAY_AVAILABLE:
            self.iconify()
            return
        self.withdraw()
        self._minimized_tray = True
        self._poll_interval  = POLL_INTERVAL_TRAY
        if not self._tray_icon:
            self._start_tray()
        self._log_terminal("Minimised to tray. Polling reduced to 5s.")

    def _restore_from_tray(self, icon=None, item=None):
        self._minimized_tray = False
        self._poll_interval  = POLL_INTERVAL_ACTIVE
        self.after(0, self.deiconify)
        self.after(0, self.lift)

    def _quit_from_tray(self, icon=None, item=None):
        self.after(0, self._on_close)

    # ── SHUTDOWN ──────────────────────────────────────────────────────

    def _on_close(self):
        self._running = False
        self._stop_tray()
        if self.csv_file:
            self.csv_file.close()
        self.destroy()


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

def main():
    # Build a hidden root to host the startup dialog
    root = ctk.CTk()
    root.withdraw()

    dialog = StartupDialog(root)
    root.wait_window(dialog)
    csv_logging = bool(dialog.result)

    root.destroy()

    app = CyberNetMonitor(csv_logging=csv_logging)
    app.mainloop()


if __name__ == "__main__":
    main()