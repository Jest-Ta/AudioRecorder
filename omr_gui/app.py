"""Tkinter user interface for Omni Meeting Recorder."""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .backend import (
    AudioDevice,
    BackendError,
    OMRBackend,
    RecordingOptions,
    find_device_by_name,
)
from .settings import AppSettings, SettingsStore


class LevelMeter(tk.Canvas):
    """A small real-signal level meter rendered with native Tk."""

    def __init__(self, master: tk.Misc, **kwargs: object) -> None:
        super().__init__(
            master,
            height=12,
            highlightthickness=1,
            highlightbackground="#a8a8a8",
            background="#e6e6e6",
            **kwargs,
        )
        self._level = 0.0
        self.bind("<Configure>", self._draw)

    def set_level(self, level: float) -> None:
        # A little visual release smoothing; every target value still comes
        # from the live WASAPI signal, never a timer or simulated source.
        self._level = max(float(level), self._level * 0.72)
        self._draw()

    def reset(self) -> None:
        self._level = 0.0
        self._draw()

    def _draw(self, _event: tk.Event | None = None) -> None:
        self.delete("all")
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        filled = int(width * min(1.0, max(0.0, self._level)))
        if filled <= 0:
            return
        green_end = min(filled, int(width * 0.72))
        yellow_end = min(filled, int(width * 0.9))
        if green_end:
            self.create_rectangle(0, 0, green_end, height, fill="#34a853", outline="")
        if yellow_end > green_end:
            self.create_rectangle(green_end, 0, yellow_end, height, fill="#f9ab00", outline="")
        if filled > yellow_end:
            self.create_rectangle(yellow_end, 0, filled, height, fill="#d93025", outline="")


class RecorderApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Omni Meeting Recorder")
        self.root.geometry("620x540")
        self.root.minsize(570, 500)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        icon_path = bundle_root / "assets" / "omr_icon.png"
        try:
            self._window_icon = tk.PhotoImage(file=str(icon_path))
            self.root.iconphoto(True, self._window_icon)
        except tk.TclError:
            self._window_icon = None

        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.store = SettingsStore()
        self.settings, settings_warning = self.store.load()
        self.backend: OMRBackend | None = None
        self.mic_devices: list[AudioDevice] = []
        self.loopback_devices: list[AudioDevice] = []
        self.state = "IDLE"
        self.started_at = 0.0
        self.close_after_stop = False
        self.meter_restart_id: str | None = None
        self._last_output: Path | None = None

        self.mic_var = tk.StringVar()
        self.loopback_var = tk.StringVar()
        self.output_var = tk.StringVar(value=self.settings.output_directory)
        self.format_var = tk.StringVar(value=self.settings.output_format)
        self.bitrate_var = tk.StringVar(value=f"{self.settings.mp3_bitrate} kbps")
        self.status_var = tk.StringVar(value="IDLE")
        self.timer_var = tk.StringVar(value="00:00:00")
        self.message_var = tk.StringVar(value="Starting…")
        self.meter_status_var = tk.StringVar(value="Initialising live meters…")
        self.aec_var = tk.BooleanVar(value=self.settings.aec_enabled)
        self.stereo_var = tk.BooleanVar(value=self.settings.recording_mode == "stereo-split")

        self._configure_style()
        self._build_ui()
        self._update_format_controls()

        try:
            self.backend = OMRBackend(self.events)
        except BackendError as exc:
            self.message_var.set(str(exc))
            self.record_button.configure(state="disabled")
            self.refresh_button.configure(state="disabled")
            self.root.after(100, lambda: messagebox.showerror("OMR unavailable", str(exc), parent=self.root))
        else:
            self.root.after(100, self.refresh_devices)

        if settings_warning:
            self.root.after(150, lambda: messagebox.showwarning("Settings", settings_warning, parent=self.root))
        self.root.after(80, self._poll_events)
        self.root.after(250, self._update_timer)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        self.root.configure(background="#f4f6fa")
        style.configure("TFrame", background="#f4f6fa")
        style.configure("TLabel", background="#f4f6fa", foreground="#202534", font=("Segoe UI", 9))
        style.configure("Header.TLabel", font=("Segoe UI Semibold", 17), foreground="#172033")
        style.configure("Source.TLabel", font=("Segoe UI Semibold", 9), foreground="#3c4558")
        style.configure("Timer.TLabel", font=("Segoe UI Light", 32), foreground="#172033")
        style.configure("Idle.TLabel", font=("Segoe UI Semibold", 9), foreground="#697386")
        style.configure("Recording.TLabel", font=("Segoe UI Semibold", 9), foreground="#c5221f")
        style.configure("Stopping.TLabel", font=("Segoe UI Semibold", 9), foreground="#a15c00")
        style.configure(
            "Record.TButton",
            font=("Segoe UI Semibold", 10),
            padding=(24, 10),
            foreground="#ffffff",
            background="#d93025",
            borderwidth=0,
        )
        style.map(
            "Record.TButton",
            background=[("active", "#b3261e"), ("disabled", "#e2b8b5")],
            foreground=[("disabled", "#ffffff")],
        )
        style.configure("Transport.TButton", font=("Segoe UI Semibold", 9), padding=(18, 10))
        style.configure("Secondary.TButton", font=("Segoe UI Semibold", 9), padding=(10, 6))
        style.configure("Link.TButton", font=("Segoe UI Semibold", 9), padding=(12, 6), foreground="#1456a0")
        style.configure("Hint.TLabel", foreground="#737b8c", font=("Segoe UI", 8))
        style.configure("Error.TLabel", foreground="#a61b1b")
        style.configure("TCombobox", padding=5)
        style.configure("TEntry", padding=5)
        style.configure("TLabelframe", background="#f4f6fa", borderwidth=1, relief="solid")
        style.configure("TLabelframe.Label", background="#f4f6fa", foreground="#40495b", font=("Segoe UI Semibold", 9))

    def _build_ui(self) -> None:
        self.root.geometry("660x650")
        self.root.minsize(610, 620)
        container = ttk.Frame(self.root, padding=(22, 18, 22, 16))
        container.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        container.columnconfigure(1, weight=1)

        ttk.Label(container, text="New recording", style="Header.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 10)
        )
        ttk.Label(container, text="MICROPHONE", style="Source.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 12)
        )
        self.mic_combo = ttk.Combobox(container, textvariable=self.mic_var, state="readonly")
        self.mic_combo.grid(row=1, column=1, sticky="ew")
        self.mic_combo.bind("<<ComboboxSelected>>", self._on_device_selected)

        ttk.Label(container, text="SYSTEM AUDIO", style="Source.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 12), pady=(10, 0)
        )
        self.loopback_combo = ttk.Combobox(container, textvariable=self.loopback_var, state="readonly")
        self.loopback_combo.grid(row=2, column=1, sticky="ew", pady=(10, 0))
        self.loopback_combo.bind("<<ComboboxSelected>>", self._on_device_selected)
        self.refresh_button = ttk.Button(
            container,
            text="Refresh devices",
            command=self.refresh_devices,
            style="Secondary.TButton",
        )
        self.refresh_button.grid(row=1, column=2, rowspan=2, padx=(8, 0))

        ttk.Separator(container).grid(row=3, column=0, columnspan=3, sticky="ew", pady=16)
        ttk.Label(container, text="Mic level", style="Source.TLabel").grid(
            row=4, column=0, sticky="w", padx=(0, 12)
        )
        self.mic_meter = LevelMeter(container)
        self.mic_meter.grid(row=4, column=1, columnspan=2, sticky="ew")
        ttk.Label(container, text="System level", style="Source.TLabel").grid(
            row=5, column=0, sticky="w", padx=(0, 12), pady=(9, 0)
        )
        self.loopback_meter = LevelMeter(container)
        self.loopback_meter.grid(row=5, column=1, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(container, textvariable=self.meter_status_var, style="Hint.TLabel").grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(6, 0)
        )

        status_frame = ttk.Frame(container)
        status_frame.grid(row=7, column=0, columnspan=3, pady=(14, 6))
        ttk.Label(status_frame, textvariable=self.timer_var, style="Timer.TLabel").pack()
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, style="Idle.TLabel")
        self.status_label.pack()

        transport = ttk.Frame(container)
        transport.grid(row=8, column=0, columnspan=3, pady=(4, 3))
        self.record_button = ttk.Button(
            transport, text="●  Record", style="Record.TButton", command=self._record
        )
        self.record_button.grid(row=0, column=0, padx=4)
        self.pause_button = ttk.Button(
            transport, text="Pause", style="Transport.TButton", state="disabled"
        )
        self.pause_button.grid(row=0, column=1, padx=4)
        self.stop_button = ttk.Button(
            transport, text="Stop", style="Transport.TButton", state="disabled", command=self._stop
        )
        self.stop_button.grid(row=0, column=2, padx=4)
        ttk.Label(
            container,
            text="Pause is unavailable in OMR 0.7.2 · Stop always finalises the current file",
            style="Hint.TLabel",
        ).grid(row=9, column=0, columnspan=3, pady=(2, 10))

        options = ttk.LabelFrame(container, text="Recording output", padding=12)
        options.grid(row=10, column=0, columnspan=3, sticky="ew")
        options.columnconfigure(1, weight=1)
        ttk.Label(options, text="Save to").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.output_entry = ttk.Entry(options, textvariable=self.output_var)
        self.output_entry.grid(row=0, column=1, sticky="ew")
        self.browse_button = ttk.Button(options, text="Browse…", command=self._browse, width=10)
        self.browse_button.configure(style="Secondary.TButton")
        self.browse_button.grid(row=0, column=2, padx=(8, 0))

        ttk.Label(options, text="Format").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        self.format_combo = ttk.Combobox(
            options, textvariable=self.format_var, state="readonly", values=("MP3", "WAV"), width=8
        )
        self.format_combo.grid(row=1, column=1, sticky="w", pady=(8, 0))
        self.format_combo.bind("<<ComboboxSelected>>", self._on_format_changed)
        self.bitrate_combo = ttk.Combobox(
            options,
            textvariable=self.bitrate_var,
            state="readonly",
            values=("128 kbps", "192 kbps", "256 kbps", "320 kbps"),
            width=11,
        )
        self.bitrate_combo.grid(row=1, column=1, sticky="w", padx=(78, 0), pady=(8, 0))
        self.open_button = ttk.Button(
            options,
            text="View Recordings",
            command=self._open_folder,
            style="Link.TButton",
        )
        self.open_button.grid(row=1, column=2, padx=(8, 0), pady=(8, 0))

        self.advanced = ttk.LabelFrame(container, text="Advanced recording", padding=(10, 7))
        self.advanced.grid(row=12, column=0, columnspan=3, sticky="ew", pady=(5, 0))
        self.stereo_check = ttk.Checkbutton(
            self.advanced,
            text="Stereo split (left mic / right system)",
            variable=self.stereo_var,
            command=self._save_settings,
        )
        self.stereo_check.grid(row=0, column=0, sticky="w")
        self.aec_check = ttk.Checkbutton(
            self.advanced,
            text="Acoustic echo cancellation (AEC)",
            variable=self.aec_var,
            command=self._save_settings,
        )
        self.aec_check.grid(row=0, column=1, sticky="w", padx=(20, 0))

        self.advanced_visible = False
        self.advanced.grid_remove()
        self.advanced_button = ttk.Button(
            container,
            text="Advanced settings  ▾",
            command=self._toggle_advanced,
            style="Link.TButton",
        )
        self.advanced_button.grid(row=11, column=0, columnspan=3, sticky="w", pady=(4, 0))

        footer = ttk.Frame(container)
        footer.grid(row=13, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.message_var, wraplength=460).grid(row=0, column=0, sticky="w")
        ttk.Button(
            footer,
            text="Details",
            command=self._show_details,
            style="Secondary.TButton",
        ).grid(row=0, column=1)

    def _toggle_advanced(self) -> None:
        self.advanced_visible = not self.advanced_visible
        if self.advanced_visible:
            self.advanced.grid()
            self.advanced_button.configure(text="Advanced settings  ▴")
        else:
            self.advanced.grid_remove()
            self.advanced_button.configure(text="Advanced settings  ▾")
        self.root.update_idletasks()
        target_height = max(650, self.root.winfo_reqheight() + 10)
        self.root.geometry(f"{max(660, self.root.winfo_width())}x{target_height}")

    def run(self) -> None:
        self.root.mainloop()

    def refresh_devices(self) -> None:
        if not self.backend or self.state != "IDLE":
            return
        self.refresh_button.configure(state="disabled")
        self.record_button.configure(state="disabled")
        self.message_var.set("Refreshing audio devices…")
        self.backend.stop_metering()
        self.mic_meter.reset()
        self.loopback_meter.reset()

        def worker() -> None:
            try:
                devices = self.backend.enumerate_devices() if self.backend else []
                self.events.put(("devices", devices))
            except BackendError as exc:
                self.events.put(("device_error", str(exc)))

        threading.Thread(target=worker, daemon=True, name="device-enumeration").start()

    def _apply_devices(self, devices: list[AudioDevice]) -> None:
        self.mic_devices = [device for device in devices if device.kind == "input"]
        self.loopback_devices = [device for device in devices if device.kind == "loopback"]
        self.mic_combo["values"] = [device.display_name for device in self.mic_devices]
        self.loopback_combo["values"] = [device.display_name for device in self.loopback_devices]

        missing: list[str] = []
        mic = find_device_by_name(self.mic_devices, self.settings.mic_name)
        loopback = find_device_by_name(self.loopback_devices, self.settings.loopback_name)
        if self.settings.mic_name and (not mic or mic.name != self.settings.mic_name):
            missing.append("saved microphone")
        if self.settings.loopback_name and (
            not loopback or loopback.name != self.settings.loopback_name
        ):
            missing.append("saved system-audio source")
        if mic:
            self.mic_var.set(mic.display_name)
            self.settings.mic_name = mic.name
        if loopback:
            self.loopback_var.set(loopback.display_name)
            self.settings.loopback_name = loopback.name
        self.refresh_button.configure(state="normal")
        can_record = bool(mic and loopback)
        self.record_button.configure(state="normal" if can_record else "disabled")
        if missing:
            self.message_var.set(f"The {' and '.join(missing)} was unavailable; a current default was selected.")
        elif can_record:
            self.message_var.set("Ready")
        else:
            self.message_var.set("A microphone and system-audio source are required.")
        self._save_settings()
        self._schedule_meter_restart()

    def _selected_device(self, devices: list[AudioDevice], display_value: str) -> AudioDevice | None:
        return next((device for device in devices if device.display_name == display_value), None)

    def _on_device_selected(self, _event: tk.Event | None = None) -> None:
        mic = self._selected_device(self.mic_devices, self.mic_var.get())
        loopback = self._selected_device(self.loopback_devices, self.loopback_var.get())
        if mic:
            self.settings.mic_name = mic.name
        if loopback:
            self.settings.loopback_name = loopback.name
        self._save_settings()
        self._schedule_meter_restart()

    def _schedule_meter_restart(self, delay: int = 300) -> None:
        if self.meter_restart_id:
            self.root.after_cancel(self.meter_restart_id)
        self.meter_restart_id = self.root.after(delay, self._start_meters)

    def _start_meters(self) -> None:
        self.meter_restart_id = None
        if not self.backend or self.state not in {"IDLE", "RECORDING"}:
            return
        mic = self._selected_device(self.mic_devices, self.mic_var.get())
        loopback = self._selected_device(self.loopback_devices, self.loopback_var.get())
        if not mic or not loopback:
            return
        self.meter_status_var.set("Live levels (shared-mode WASAPI)")
        self.backend.start_metering(mic.index, loopback.index)

    def _record(self) -> None:
        if not self.backend or self.state != "IDLE":
            return
        mic = self._selected_device(self.mic_devices, self.mic_var.get())
        loopback = self._selected_device(self.loopback_devices, self.loopback_var.get())
        if not mic or not loopback:
            messagebox.showerror("Audio sources", "Select both audio sources first.", parent=self.root)
            return
        output_dir = Path(os.path.expandvars(self.output_var.get().strip())).expanduser()
        if not self.output_var.get().strip():
            messagebox.showerror("Output directory", "Choose an output directory first.", parent=self.root)
            return
        self.settings.mic_name = mic.name
        self.settings.loopback_name = loopback.name
        self.settings.output_directory = str(output_dir)
        self._save_settings()
        self._set_state("PREPARING")
        self.message_var.set("Checking devices and preparing OMR…")
        self.backend.stop_metering()
        self.mic_meter.reset()
        self.loopback_meter.reset()

        selected_mic_name = mic.name
        selected_loopback_name = loopback.name

        def worker() -> None:
            assert self.backend is not None
            try:
                current = self.backend.enumerate_devices()
                current_mics = [device for device in current if device.kind == "input"]
                current_loops = [device for device in current if device.kind == "loopback"]
                current_mic = next((d for d in current_mics if d.name == selected_mic_name), None)
                current_loop = next((d for d in current_loops if d.name == selected_loopback_name), None)
                if current_mic is None:
                    raise BackendError("The selected microphone is no longer available. Refresh devices.")
                if current_loop is None:
                    raise BackendError("The selected system-audio source is no longer available. Refresh devices.")
                bitrate = int(self.bitrate_var.get().split()[0])
                options = RecordingOptions(
                    mic_index=current_mic.index,
                    loopback_index=current_loop.index,
                    output_directory=output_dir,
                    output_format=self.format_var.get(),
                    bitrate=bitrate,
                    stereo_split=self.stereo_var.get(),
                    aec_enabled=self.aec_var.get(),
                )
                self.backend.start_recording(options)
            except (BackendError, OSError, ValueError) as exc:
                self.events.put(("recording_start_error", str(exc)))

        threading.Thread(target=worker, daemon=True, name="recording-start").start()

    def _recording_started(self, path_text: str) -> None:
        self._last_output = Path(path_text)
        self.started_at = time.monotonic()
        self.timer_var.set("00:00:00")
        self._set_state("RECORDING")
        self.message_var.set(f"Recording to {self._last_output.name}")
        # OMR's output normally confirms when its capture streams are open.
        # This fallback also starts meters if a future Rich version suppresses
        # that line on redirected output.
        self._schedule_meter_restart(delay=2500)

    def _stop(self) -> None:
        if not self.backend or self.state != "RECORDING":
            return
        self._set_state("STOPPING")
        self.message_var.set("Stopping and finalising the recording…")
        self.backend.request_graceful_stop()

    def _set_state(self, state: str) -> None:
        self.state = state
        if state == "IDLE":
            self.status_var.set("IDLE")
            self.status_label.configure(style="Idle.TLabel")
            self.record_button.configure(state="normal" if self.mic_devices and self.loopback_devices else "disabled")
            self.stop_button.configure(state="disabled")
            self.refresh_button.configure(state="normal")
            self.mic_combo.configure(state="readonly")
            self.loopback_combo.configure(state="readonly")
            self.format_combo.configure(state="readonly")
            self.output_entry.configure(state="normal")
            self.browse_button.configure(state="normal")
            self.stereo_check.configure(state="normal")
            self.aec_check.configure(state="normal")
            self.advanced_button.configure(state="normal")
        elif state == "RECORDING":
            self.status_var.set("●  RECORDING")
            self.status_label.configure(style="Recording.TLabel")
            self.record_button.configure(state="disabled")
            self.stop_button.configure(state="normal")
            self.refresh_button.configure(state="disabled")
            self.mic_combo.configure(state="disabled")
            self.loopback_combo.configure(state="disabled")
            self.format_combo.configure(state="disabled")
            self.output_entry.configure(state="disabled")
            self.browse_button.configure(state="disabled")
            self.stereo_check.configure(state="disabled")
            self.aec_check.configure(state="disabled")
            self.advanced_button.configure(state="disabled")
        else:
            self.status_var.set("STOPPING…" if state == "STOPPING" else "PREPARING…")
            self.status_label.configure(style="Stopping.TLabel")
            self.record_button.configure(state="disabled")
            self.stop_button.configure(state="disabled")
            self.refresh_button.configure(state="disabled")
            self.mic_combo.configure(state="disabled")
            self.loopback_combo.configure(state="disabled")
            self.format_combo.configure(state="disabled")
            self.output_entry.configure(state="disabled")
            self.browse_button.configure(state="disabled")
            self.stereo_check.configure(state="disabled")
            self.aec_check.configure(state="disabled")
            self.advanced_button.configure(state="disabled")
        self._update_format_controls()

    def _update_timer(self) -> None:
        if self.state in {"RECORDING", "STOPPING"} and self.started_at:
            elapsed = max(0, int(time.monotonic() - self.started_at))
            hours, remainder = divmod(elapsed, 3600)
            minutes, seconds = divmod(remainder, 60)
            self.timer_var.set(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
        self.root.after(250, self._update_timer)

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "devices":
                    self._apply_devices(event[1])
                elif kind == "device_error":
                    self.refresh_button.configure(state="normal")
                    self.record_button.configure(state="disabled")
                    self.message_var.set(event[1])
                    messagebox.showerror("Device enumeration", event[1], parent=self.root)
                elif kind == "levels":
                    self.mic_meter.set_level(event[1])
                    self.loopback_meter.set_level(event[2])
                elif kind == "meter_error":
                    self.meter_status_var.set(f"Metering unavailable: {event[1]}")
                    self.mic_meter.reset()
                    self.loopback_meter.reset()
                elif kind == "meter_warning":
                    self.meter_status_var.set(str(event[1]))
                elif kind == "recording_started":
                    self._recording_started(event[1])
                elif kind == "omr_ready" and self.state == "RECORDING":
                    # Recording owns priority; meters join only after OMR has
                    # successfully opened its shared-mode streams.
                    self._schedule_meter_restart(delay=100)
                elif kind == "recording_start_error":
                    self._set_state("IDLE")
                    self.message_var.set(event[1])
                    messagebox.showerror("Recording could not start", event[1], parent=self.root)
                    self._schedule_meter_restart()
                elif kind == "recording_exited":
                    self._recording_exited(event[1], Path(event[2]))
                elif kind == "stop_timeout":
                    self._handle_stop_timeout()
                elif kind == "stop_error":
                    self._handle_stop_error(event[1])
                # "log" is retained by the backend for the details window.
        except queue.Empty:
            pass
        try:
            if self.root.winfo_exists():
                self.root.after(80, self._poll_events)
        except tk.TclError:
            return

    def _recording_exited(self, return_code: int, output_path: Path) -> None:
        expected = self.state == "STOPPING"
        was_starting = self.state == "PREPARING"
        self._set_state("IDLE")
        exists = output_path.exists() and output_path.stat().st_size > 0
        if expected and return_code == 0 and exists:
            self.message_var.set(f"Saved {output_path.name}")
        elif expected and exists:
            self.message_var.set(f"OMR stopped with an error; a partial file remains: {output_path.name}")
            messagebox.showwarning(
                "Recording ended with an error",
                "OMR reported an error while stopping. The recording file was preserved. "
                "Open Technical details for the OMR output.",
                parent=self.root,
            )
        elif was_starting or not expected:
            self.message_var.set("OMR ended unexpectedly.")
            messagebox.showerror(
                "Recording ended unexpectedly",
                "OMR exited before Stop was requested. Open Technical details for the OMR output.",
                parent=self.root,
            )
        else:
            self.message_var.set("OMR stopped, but no completed recording file was found.")
            messagebox.showerror(
                "Recording not finalised",
                "No completed recording was found. Open Technical details before trying again.",
                parent=self.root,
            )
        if self.close_after_stop:
            self._destroy()
        else:
            self._schedule_meter_restart(delay=500)

    def _handle_stop_timeout(self) -> None:
        if self.state != "STOPPING" or not self.backend:
            return
        force = messagebox.askyesno(
            "OMR is still stopping",
            "OMR did not finish within 12 seconds. Force stopping can leave the current file incomplete. "
            "Force stop now?",
            parent=self.root,
        )
        if force:
            self.backend.force_stop()
        else:
            self.close_after_stop = False
            self._set_state("RECORDING")
            self.message_var.set("Still recording; press Stop to try graceful finalisation again.")

    def _handle_stop_error(self, message: str) -> None:
        if self.state != "STOPPING":
            return
        self.close_after_stop = False
        self._set_state("RECORDING")
        self.message_var.set(message)
        messagebox.showerror("Could not stop OMR", message, parent=self.root)

    def _browse(self) -> None:
        initial = self.output_var.get() or str(Path.home())
        selected = filedialog.askdirectory(parent=self.root, initialdir=initial, mustexist=False)
        if selected:
            self.output_var.set(selected)
            self.settings.output_directory = selected
            self._save_settings()

    def _open_folder(self) -> None:
        path = Path(os.path.expandvars(self.output_var.get())).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror(
                "View recordings",
                f"The currently selected recordings folder could not be opened: {exc}",
                parent=self.root,
            )

    def _on_format_changed(self, _event: tk.Event | None = None) -> None:
        self._update_format_controls()
        self._save_settings()

    def _update_format_controls(self) -> None:
        enabled = self.format_var.get() == "MP3" and self.state == "IDLE"
        self.bitrate_combo.configure(state="readonly" if enabled else "disabled")

    def _current_settings(self) -> AppSettings:
        try:
            bitrate = int(self.bitrate_var.get().split()[0])
        except (ValueError, IndexError):
            bitrate = 192
        self.settings.output_directory = self.output_var.get().strip()
        self.settings.output_format = self.format_var.get()
        self.settings.mp3_bitrate = bitrate
        self.settings.recording_mode = "stereo-split" if self.stereo_var.get() else "mix"
        self.settings.aec_enabled = self.aec_var.get()
        return self.settings

    def _save_settings(self) -> None:
        warning = self.store.save(self._current_settings())
        if warning:
            self.message_var.set(warning)

    def _show_details(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("OMR technical details")
        window.geometry("720x390")
        window.transient(self.root)
        frame = ttk.Frame(window, padding=10)
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, wrap="word", font=("Consolas", 9))
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        content = self.backend.technical_log() if self.backend else "OMR backend is unavailable."
        text.insert("1.0", content)
        text.configure(state="disabled")

    def _on_close(self) -> None:
        if self.state in {"RECORDING", "STOPPING", "PREPARING"}:
            if self.state == "PREPARING":
                messagebox.showinfo(
                    "Recording is preparing",
                    "Please wait until OMR has started or reported an error before closing.",
                    parent=self.root,
                )
                return
            should_stop = messagebox.askyesno(
                "Recording in progress",
                "Stop and finalise the active recording, then exit?",
                parent=self.root,
            )
            if should_stop:
                self.close_after_stop = True
                if self.state == "RECORDING":
                    self._stop()
            return
        self._destroy()

    def _destroy(self) -> None:
        self._save_settings()
        if self.backend:
            self.backend.shutdown()
        self.root.destroy()
