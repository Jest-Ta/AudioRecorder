"""OMR CLI discovery, device enumeration, metering, and process control."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010


@dataclass(frozen=True, slots=True)
class AudioDevice:
    index: int
    name: str
    kind: str
    channels: int
    sample_rate: int
    is_default: bool

    @classmethod
    def from_json(cls, raw: dict[str, object]) -> "AudioDevice":
        return cls(
            index=int(raw["index"]),
            name=str(raw["name"]),
            kind=str(raw["kind"]),
            channels=int(raw["channels"]),
            sample_rate=int(float(raw["sample_rate"])),
            is_default=bool(raw["is_default"]),
        )

    @property
    def display_name(self) -> str:
        return f"{self.name}{'  (Default)' if self.is_default else ''}"


@dataclass(frozen=True, slots=True)
class RecordingOptions:
    mic_index: int
    loopback_index: int
    output_directory: Path
    output_format: str
    bitrate: int
    stereo_split: bool = False
    aec_enabled: bool = False


class BackendError(RuntimeError):
    pass


class OMRBackend:
    def __init__(self, event_queue: "queue.Queue[tuple]") -> None:
        self.events = event_queue
        self.is_frozen = bool(getattr(sys, "frozen", False))
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        self.project_root = Path(sys.executable).parent if self.is_frozen else bundle_root
        self.worker_script = bundle_root / "omr_gui" / "meter_worker.py"
        self.entry_script = self.project_root / "Main.py"
        self.omr_executable = self._find_omr_executable()
        self.omr_python = self._find_omr_python()
        self.recording_process: subprocess.Popen[str] | None = None
        self.meter_process: subprocess.Popen[str] | None = None
        self._record_lock = threading.Lock()
        self._meter_lock = threading.Lock()
        self.log_lines: list[str] = []
        self.current_output: Path | None = None

    @staticmethod
    def _find_omr_executable() -> Path:
        override = os.environ.get("OMR_EXECUTABLE")
        candidates = [
            Path(override) if override else None,
            Path.home() / ".local" / "bin" / "omr.exe",
        ]
        for candidate in candidates:
            if candidate and candidate.is_file():
                return candidate
        raise BackendError(
            "OMR was not found. Expected it at "
            f"{Path.home() / '.local' / 'bin' / 'omr.exe'}."
        )

    @staticmethod
    def _find_omr_python() -> Path:
        override = os.environ.get("OMR_TOOL_PYTHON")
        appdata = os.environ.get("APPDATA")
        candidates = [Path(override) if override else None]
        if appdata:
            candidates.append(
                Path(appdata)
                / "uv"
                / "tools"
                / "omni-meeting-recorder"
                / "Scripts"
                / "python.exe"
            )
        for candidate in candidates:
            if candidate and candidate.is_file():
                # uv's Scripts\python.exe can be a launcher that starts the
                # actual managed interpreter as a child. Resolve sys.executable
                # once so meter termination cannot orphan that child process.
                try:
                    result = subprocess.run(
                        [str(candidate), "-c", "import sys; print(sys.executable)"],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=5,
                        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
                        startupinfo=_hidden_startupinfo(),
                        check=False,
                    )
                    resolved = Path(result.stdout.strip())
                    if result.returncode == 0 and resolved.is_file():
                        return resolved
                except (OSError, subprocess.TimeoutExpired):
                    pass
                return candidate
        raise BackendError(
            "The uv-managed OMR Python environment was not found. Reinstall "
            "omni-meeting-recorder with uv, or set OMR_TOOL_PYTHON."
        )

    def enumerate_devices(self, timeout: float = 15.0) -> list[AudioDevice]:
        command = [str(self.omr_python), str(self.worker_script), "--devices"]
        startupinfo = _hidden_startupinfo()
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
                startupinfo=startupinfo,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackendError(f"Device enumeration failed: {exc}") from exc
        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout).strip()
            raise BackendError(f"Device enumeration failed: {details or 'unknown error'}")
        try:
            raw = json.loads(completed.stdout)
            return [AudioDevice.from_json(item) for item in raw]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise BackendError(f"OMR returned invalid device information: {exc}") from exc

    def start_metering(self, mic_index: int, loopback_index: int) -> None:
        self.stop_metering()
        command = [
            str(self.omr_python),
            str(self.worker_script),
            "--meter",
            str(mic_index),
            str(loopback_index),
        ]
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
                startupinfo=_hidden_startupinfo(),
            )
        except OSError as exc:
            self.events.put(("meter_error", f"Meters could not start: {exc}"))
            return
        with self._meter_lock:
            self.meter_process = process
        threading.Thread(
            target=self._read_meter_output,
            args=(process,),
            daemon=True,
            name="meter-output",
        ).start()

    def _read_meter_output(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        saw_levels = False
        for line in process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "mic" in message and "loopback" in message:
                saw_levels = True
                self.events.put(("levels", float(message["mic"]), float(message["loopback"])))
            elif "warning" in message:
                self.events.put(("meter_warning", str(message["warning"])))
            elif "error" in message:
                self.events.put(("meter_error", str(message["error"])))
        return_code = process.wait()
        with self._meter_lock:
            if self.meter_process is process:
                self.meter_process = None
        if return_code and not saw_levels:
            self.events.put(("meter_error", "Live meters are unavailable for the selected devices."))

    def stop_metering(self) -> None:
        with self._meter_lock:
            process = self.meter_process
            self.meter_process = None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

    def build_recording_command(self, options: RecordingOptions) -> tuple[list[str], Path]:
        output_path = unique_output_path(options.output_directory, options.output_format.lower())
        command = [
            str(self.omr_executable),
            "start",
            "--mic-device",
            str(options.mic_index),
            "--loopback-device",
            str(options.loopback_index),
            "--stereo-split" if options.stereo_split else "--mix",
            "--aec" if options.aec_enabled else "--no-aec",
            "--output",
            str(output_path),
            "--format",
            options.output_format.lower(),
        ]
        if options.output_format.upper() == "MP3":
            command.extend(["--bitrate", str(options.bitrate)])
        return command, output_path

    def start_recording(self, options: RecordingOptions) -> Path:
        with self._record_lock:
            if self.recording_process and self.recording_process.poll() is None:
                raise BackendError("A recording is already active.")
            options.output_directory.mkdir(parents=True, exist_ok=True)
            if not options.output_directory.is_dir():
                raise BackendError("The selected output location is not a directory.")
            probe = options.output_directory / ".omr_gui_write_test"
            try:
                probe.write_bytes(b"")
                probe.unlink()
            except OSError as exc:
                raise BackendError(f"The output directory is not writable: {exc}") from exc

            command, output_path = self.build_recording_command(options)
            environment = os.environ.copy()
            environment.update({"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "NO_COLOR": "1"})
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=environment,
                    creationflags=CREATE_NEW_CONSOLE if os.name == "nt" else 0,
                    startupinfo=_hidden_startupinfo(),
                )
            except OSError as exc:
                raise BackendError(f"OMR failed to launch: {exc}") from exc
            self.recording_process = process
            self.current_output = output_path
            self.log_lines.clear()

        # Queue this before starting the waiter so even an immediate CLI
        # failure cannot race an "exited" event ahead of "started".
        self.events.put(("recording_started", str(output_path)))

        threading.Thread(
            target=self._read_recording_output,
            args=(process,),
            daemon=True,
            name="omr-output",
        ).start()
        threading.Thread(
            target=self._watch_recording,
            args=(process, output_path),
            daemon=True,
            name="omr-wait",
        ).start()
        return output_path

    def _read_recording_output(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip()
            if not line:
                continue
            self.log_lines.append(line)
            if len(self.log_lines) > 500:
                del self.log_lines[:100]
            self.events.put(("log", line))
            if "Recording started!" in line:
                self.events.put(("omr_ready",))

    def _watch_recording(self, process: subprocess.Popen[str], output_path: Path) -> None:
        while True:
            try:
                return_code = process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                pass
            else:
                break
            self._queue_recording_size(output_path)

        # Include the final size after OMR has flushed headers and closed the
        # encoder, which can increase a WAV file slightly during finalisation.
        self._queue_recording_size(output_path)
        with self._record_lock:
            if self.recording_process is process:
                self.recording_process = None
        self.events.put(("recording_exited", return_code, str(output_path)))

    def _queue_recording_size(self, output_path: Path) -> None:
        try:
            size_bytes = output_path.stat().st_size
        except OSError:
            return
        self.events.put(("recording_size", size_bytes))

    def request_graceful_stop(self, timeout: float = 12.0) -> None:
        with self._record_lock:
            process = self.recording_process
        if not process or process.poll() is not None:
            return

        def worker() -> None:
            flags = CREATE_NO_WINDOW if os.name == "nt" else 0
            try:
                if self.is_frozen:
                    helper_command = [sys.executable, "--send-quit-key", str(process.pid)]
                else:
                    helper_command = [
                        sys.executable,
                        str(self.entry_script),
                        "--send-quit-key",
                        str(process.pid),
                    ]
                result = subprocess.run(
                    helper_command,
                    timeout=3,
                    creationflags=flags,
                    startupinfo=_hidden_startupinfo(),
                    check=False,
                )
                if result.returncode != 0:
                    self.events.put(("stop_error", "Could not send OMR its graceful stop signal."))
                    return
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.events.put(("stop_timeout",))
            except OSError as exc:
                self.events.put(("stop_error", f"Graceful stop failed: {exc}"))

        threading.Thread(target=worker, daemon=True, name="omr-stop").start()

    def force_stop(self) -> None:
        with self._record_lock:
            process = self.recording_process
        if process and process.poll() is None:
            process.terminate()

    def technical_log(self) -> str:
        return "\n".join(self.log_lines) or "No OMR output has been captured."

    def shutdown(self) -> None:
        self.stop_metering()


def unique_output_path(directory: Path, extension: str, now: datetime | None = None) -> Path:
    timestamp = (now or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    base = directory / f"OMR_{timestamp}.{extension}"
    if not base.exists():
        return base
    for suffix in range(2, 10_000):
        candidate = directory / f"OMR_{timestamp}_{suffix}.{extension}"
        if not candidate.exists():
            return candidate
    raise BackendError("Could not create a unique recording filename.")


def format_file_size(size_bytes: int) -> str:
    """Format a file size like OMR's live recording status panel."""
    size = float(max(0, size_bytes))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def find_device_by_name(devices: list[AudioDevice], name: str) -> AudioDevice | None:
    wanted = name.strip().casefold()
    if wanted:
        for device in devices:
            if device.name.strip().casefold() == wanted:
                return device
    return next((device for device in devices if device.is_default), devices[0] if devices else None)


def _hidden_startupinfo() -> subprocess.STARTUPINFO | None:
    if os.name != "nt":
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0
    return info
