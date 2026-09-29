# Omni Meeting Recorder GUI

A compact Windows desktop frontend for the locally installed Omni Meeting Recorder (OMR) 0.7.2. The app keeps OMR's existing recording engine: it launches `omr.exe` directly with the selected microphone and WASAPI loopback source, while a separate lightweight helper reads shared-mode audio only for the two live meters.

## Run it

Double-click **Launch OMR GUI.vbs** for a normal window with no console, or run:

```bat
run_gui.bat
```

The launcher prefers:

```text
%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe
```

The GUI itself has no third-party Python dependency. Metering reuses the already-installed `PyAudioWPatch` in OMR's uv tool environment. The expected local installation is:

```text
%USERPROFILE%\.local\bin\omr.exe
%APPDATA%\uv\tools\omni-meeting-recorder\Scripts\python.exe
```

For a nonstandard installation, set `OMR_EXECUTABLE` and/or `OMR_TOOL_PYTHON` before launching.

## Normal use

1. Choose a microphone and **System audio source**. Device indices are rediscovered each launch/refresh; the saved choice is matched by device name.
2. Confirm that both real-time meters move.
3. Choose the recordings directory and MP3 or WAV. MP3 defaults to 192 kbps.
4. Click **Record**, then **Stop**. Stop writes OMR's normal `q` command to its private hidden console, so OMR follows its own stop-event cleanup path and closes the audio writer normally.
5. Use **View Recordings** to open the directory currently shown in **Save to** and find a file such as `OMR_2026-09-29_12-34-56.mp3`. Existing files are never overwritten.

Settings are stored in `%APPDATA%\OmniMeetingRecorderGUI\settings.json`.

## Verified OMR 0.7.2 behaviour

The installed CLI and package source were inspected before implementation:

- Normal recording uses `--mix --no-aec` by default.
- MP3 is streamed through OMR's installed `lameenc`; WAV uses OMR's WAV writer.
- The CLI enum mentions FLAC, but 0.7.2 does not connect FLAC to an encoder and would write WAV data under a `.flac` name. The GUI therefore correctly exposes only MP3 and WAV.
- A chosen directory cannot be combined with OMR's internal auto-name without also controlling OMR's config/working directory. The GUI supplies an automatically generated, collision-safe timestamped path with `--output`.
- OMR has no pause command, pause state, or safe public pause mechanism. The Pause button is intentionally unavailable. Stop/start is not substituted because that would create multiple files.
- OMR does not expose per-source levels. `meter_worker.py` opens the selected devices in shared WASAPI mode for RMS/dBFS-style metering. Meter failure is isolated and never replaces or stops OMR recording.

## Tests

Run the non-audio test suite:

```bat
"%APPDATA%\uv\tools\omni-meeting-recorder\Scripts\python.exe" -m unittest discover -s tests -v
```

## Physical audio check

These final checks require real sound and should be done once on the machine:

1. Select the Corsair headset mic and Corsair headset loopback. Speak, then play desktop audio; verify only the appropriate meter responds to each source.
2. Record 15–30 seconds with alternating speech and desktop audio, stop, and play the MP3. Confirm both sources are present and the file opens cleanly.
3. Repeat as WAV and at another MP3 bitrate.
4. Start recording, close the GUI, accept **Stop and finalise**, and verify the resulting file is playable.
5. Disconnect/reconnect a device while idle, click **Refresh**, and confirm the list/default selection updates. If practical, disconnect during a disposable recording and confirm the GUI reports OMR's termination while preserving any partial file.

## Project layout

- `Main.py` — application entry point and hidden stop-signal helper mode
- `omr_gui/app.py` — responsive Tkinter UI
- `omr_gui/backend.py` — OMR subprocess lifecycle and device bridge
- `omr_gui/meter_worker.py` — device JSON and real WASAPI levels
- `omr_gui/settings.py` — local JSON settings
- `tests/` — non-hardware unit tests

The code is separated cleanly so a later PyInstaller build can use `Main.py` as its entry point. Source execution remains the recommended first test.

## Standalone Windows executable

Run `build_windows.bat` to rebuild the windowed one-file executable. The build uses PyInstaller through uv and embeds `assets\omr_icon.png` as the application/taskbar icon.

The result is:

```text
dist\Omni Meeting Recorder.exe
```

To add it to the taskbar, right-click the executable and choose **Pin to taskbar**. Windows intentionally reserves that final pinning choice for the signed-in user.
