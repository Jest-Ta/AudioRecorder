from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from omr_gui.backend import AudioDevice, RecordingOptions, find_device_by_name, unique_output_path


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


if __name__ == "__main__":
    unittest.main()
