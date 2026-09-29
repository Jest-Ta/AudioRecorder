from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omr_gui.settings import AppSettings, SettingsStore


class SettingsStoreTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            expected = AppSettings(
                mic_name="Mic",
                loopback_name="Speakers [Loopback]",
                output_directory=directory,
                output_format="WAV",
                mp3_bitrate=320,
                recording_mode="stereo-split",
                aec_enabled=True,
            )
            self.assertIsNone(store.save(expected))
            actual, warning = store.load()
            self.assertIsNone(warning)
            self.assertEqual(actual, expected)

    def test_invalid_json_falls_back_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("not json", encoding="utf-8")
            settings, warning = SettingsStore(path).load()
            self.assertEqual(settings.output_format, "MP3")
            self.assertIsNotNone(warning)


if __name__ == "__main__":
    unittest.main()
