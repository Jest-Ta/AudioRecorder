from __future__ import annotations

import queue
import subprocess
import threading
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from omr_gui.backend import (
    AudioDevice,
    OMRBackend,
    RecordingOptions,
    find_device_by_name,
    format_file_size,
    unique_output_path,
)


class OutputNameTests(unittest.TestCase):
    def test_timestamped_name_uses_requested_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = unique_output_path(
                Path(directory), "mp3", datetime(2026, 9, 29, 12, 34, 56)
            )
            self.assertEqual(result.name, "OMR_2026-09-29_12-34-56.mp3")

    def test_existing_name_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "OMR_2026-09-29_12-34-56.wav"
            first.touch()
            result = unique_output_path(root, "wav", datetime(2026, 9, 29, 12, 34, 56))
            self.assertEqual(result.name, "OMR_2026-09-29_12-34-56_2.wav")


class FileSizeFormatTests(unittest.TestCase):
    def test_uses_omr_human_readable_units(self) -> None:
        self.assertEqual(format_file_size(0), "0.0 B")
        self.assertEqual(format_file_size(1536), "1.5 KB")
        self.assertEqual(format_file_size(1_572_864), "1.5 MB")

    def test_negative_size_is_clamped_to_zero(self) -> None:
        self.assertEqual(format_file_size(-1), "0.0 B")

    def test_watcher_queues_live_and_final_file_sizes(self) -> None:
        class FakeProcess:
            def __init__(self, output_path: Path) -> None:
                self.output_path = output_path
                self.wait_count = 0

            def wait(self, timeout: float) -> int:
                self.wait_count += 1
                if self.wait_count == 1:
                    raise subprocess.TimeoutExpired("omr", timeout)
                self.output_path.write_bytes(b"x" * 2048)
                return 0

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "recording.mp3"
            output_path.write_bytes(b"x" * 512)
            process = FakeProcess(output_path)
            backend = OMRBackend.__new__(OMRBackend)
            backend.events = queue.Queue()
            backend._record_lock = threading.Lock()
            backend.recording_process = process

            backend._watch_recording(process, output_path)  # type: ignore[arg-type]

            events = [backend.events.get_nowait() for _ in range(3)]
            self.assertEqual(events[0], ("recording_size", 512))
            self.assertEqual(events[1], ("recording_size", 2048))
            self.assertEqual(events[2], ("recording_exited", 0, str(output_path)))
            self.assertIsNone(backend.recording_process)


class DeviceSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.devices = [
            AudioDevice(10, "First mic", "input", 2, 48_000, False),
            AudioDevice(29, "Headset mic", "input", 2, 48_000, True),
        ]

    def test_saved_name_resolves_new_index(self) -> None:
        selected = find_device_by_name(self.devices, "headset MIC")
        self.assertIsNotNone(selected)
        self.assertEqual(selected.index, 29)  # type: ignore[union-attr]

    def test_missing_saved_name_falls_back_to_default(self) -> None:
        selected = find_device_by_name(self.devices, "Disconnected mic")
        self.assertIsNotNone(selected)
        self.assertEqual(selected.name, "Headset mic")  # type: ignore[union-attr]


class RecordingOptionsTests(unittest.TestCase):
    def test_defaults_are_mix_and_no_aec(self) -> None:
        options = RecordingOptions(1, 2, Path("recordings"), "MP3", 192)
        self.assertFalse(options.stereo_split)
        self.assertFalse(options.aec_enabled)


class RecordingCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = OMRBackend.__new__(OMRBackend)
        self.backend.omr_executable = Path("omr.exe")

    def options(self, mic: int | None, loopback: int | None) -> RecordingOptions:
        return RecordingOptions(mic, loopback, Path("recordings"), "MP3", 128)

    def test_both_sources_use_balanced_mix(self) -> None:
        command, _ = self.backend.build_recording_command(self.options(30, 31))
        self.assertIn("--mix", command)
        self.assertIn("--mix-ratio", command)
        self.assertEqual(command[command.index("--mix-ratio") + 1], "0.5")
        self.assertIn("--mic-device", command)
        self.assertIn("--loopback-device", command)

    def test_one_available_source_uses_single_source_mode(self) -> None:
        mic_command, _ = self.backend.build_recording_command(self.options(30, None))
        loopback_command, _ = self.backend.build_recording_command(self.options(None, 31))
        self.assertIn("--mic-only", mic_command)
        self.assertNotIn("--loopback-device", mic_command)
        self.assertIn("--loopback-only", loopback_command)
        self.assertNotIn("--mic-device", loopback_command)

    def test_no_enumerated_sources_leaves_omr_on_default_mixed_mode(self) -> None:
        command, _ = self.backend.build_recording_command(self.options(None, None))
        self.assertIn("--mix", command)
        self.assertEqual(command.count("--mix-ratio"), 1)
        self.assertNotIn("--mic-device", command)
        self.assertNotIn("--loopback-device", command)


if __name__ == "__main__":
    unittest.main()
