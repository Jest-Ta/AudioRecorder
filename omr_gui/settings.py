"""Small JSON settings store for the GUI."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def default_output_directory() -> Path:
    music = Path.home() / "Music"
    base = music if music.exists() else Path.home()
    return base / "OMR Recordings"


def default_settings_path() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "OmniMeetingRecorderGUI" / "settings.json"


@dataclass(slots=True)
class AppSettings:
    mic_name: str = ""
    loopback_name: str = ""
    output_directory: str = ""
    output_format: str = "MP3"
    mp3_bitrate: int = 192
    recording_mode: str = "mix"
    aec_enabled: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AppSettings":
        values = {
            key: raw[key]
            for key in cls.__dataclass_fields__
            if key in raw
        }
        settings = cls(**values)
        if settings.output_format not in {"MP3", "WAV"}:
            settings.output_format = "MP3"
        if settings.mp3_bitrate not in {128, 192, 256, 320}:
            settings.mp3_bitrate = 192
        if settings.recording_mode not in {"mix", "stereo-split"}:
            settings.recording_mode = "mix"
        return settings


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_settings_path()

    def load(self) -> tuple[AppSettings, str | None]:
        if not self.path.exists():
            settings = AppSettings(output_directory=str(default_output_directory()))
            return settings, None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("settings root must be an object")
            settings = AppSettings.from_dict(raw)
            if not settings.output_directory:
                settings.output_directory = str(default_output_directory())
            return settings, None
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            settings = AppSettings(output_directory=str(default_output_directory()))
            return settings, f"Saved settings could not be read; defaults were used ({exc})."

    def save(self, settings: AppSettings) -> str | None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(asdict(settings), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary.replace(self.path)
            return None
        except OSError as exc:
            return f"Settings could not be saved: {exc}"
