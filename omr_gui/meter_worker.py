"""Runs inside OMR's uv environment to enumerate and meter WASAPI devices."""

from __future__ import annotations

import json
import math
import sys
import threading
import time
from array import array


def emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def list_devices() -> int:
    from omr.core.device_manager import DeviceManager

    manager = DeviceManager()
    manager.initialize()
    result = []
    for device in manager.get_input_devices() + manager.get_loopback_devices():
        result.append(
            {
                "index": device.index,
                "name": device.name,
                "kind": device.device_type.value,
                "channels": device.channels,
                "sample_rate": device.default_sample_rate,
                "is_default": device.is_default,
            }
        )
    emit(result)
    return 0


def dbfs_level(data: bytes) -> float:
    samples = array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return 0.0
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    if mean_square <= 1.0:
        return 0.0
    rms = math.sqrt(mean_square) / 32768.0
    dbfs = 20.0 * math.log10(max(rms, 1e-9))
    return max(0.0, min(1.0, (dbfs + 60.0) / 60.0))


def meter(mic_index: int, loopback_index: int) -> int:
    import pyaudiowpatch as pyaudio

    audio = pyaudio.PyAudio()
    stop = threading.Event()
    levels = {"mic": 0.0, "loopback": 0.0}
    errors: list[str] = []
    active = {"mic": False, "loopback": False}
    lock = threading.Lock()

    def open_stream(label: str, index: int):
        info = audio.get_device_info_by_index(index)
        channels = int(info.get("maxInputChannels", 0))
        rate = int(float(info.get("defaultSampleRate", 48_000)))
        if channels < 1:
            raise RuntimeError(f"{label} device has no input channels")
        return audio.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=index,
            frames_per_buffer=1024,
        )

    def reader(label: str, stream) -> None:
        with lock:
            active[label] = True
        try:
            while not stop.is_set():
                data = stream.read(1024, exception_on_overflow=False)
                with lock:
                    levels[label] = dbfs_level(data)
        except Exception as exc:  # Worker reports a concise error to the GUI.
            with lock:
                errors.append(f"{label.title()} meter: {exc}")
        finally:
            with lock:
                active[label] = False
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass

    # PortAudio/PyAudio stream creation is not thread-safe. Open both streams
    # serially, exactly as OMR's own dual-device backend does, then read them
    # concurrently.
    streams: list[tuple[str, object]] = []
    open_errors: list[str] = []
    for label, friendly_name, index in (
        ("mic", "Microphone", mic_index),
        ("loopback", "System audio", loopback_index),
    ):
        try:
            streams.append((label, open_stream(friendly_name, index)))
        except Exception as exc:
            open_errors.append(f"{friendly_name} meter: {exc}")
    if open_errors:
        emit({"warning": "; ".join(open_errors)})
    if not streams:
        emit({"error": "Neither selected audio source could be opened for metering."})
        audio.terminate()
        return 1

    threads = [
        threading.Thread(target=reader, args=(label, stream), daemon=True)
        for label, stream in streams
    ]
    for thread in threads:
        thread.start()
    try:
        while not stop.wait(0.08):
            with lock:
                emit({"mic": levels["mic"], "loopback": levels["loopback"]})
                if not any(active.values()):
                    stop.set()
        with lock:
            if errors:
                emit({"error": "; ".join(errors)})
                return 1
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=1)
        audio.terminate()


def main() -> int:
    try:
        if len(sys.argv) == 2 and sys.argv[1] == "--devices":
            return list_devices()
        if len(sys.argv) == 4 and sys.argv[1] == "--meter":
            return meter(int(sys.argv[2]), int(sys.argv[3]))
        emit({"error": "Invalid meter worker arguments."})
        return 2
    except Exception as exc:
        emit({"error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
