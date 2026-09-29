"""Entry point for the Omni Meeting Recorder desktop GUI."""

from __future__ import annotations

import sys


def main() -> int:
    # This private mode is used by a short-lived helper process to deliver
    # OMR's normal quit key to its hidden console. It must run before Tk is
    # imported so packaged builds can use the same entry point.
    if len(sys.argv) == 3 and sys.argv[1] == "--send-quit-key":
        from omr_gui.process_control import send_quit_key

        return 0 if send_quit_key(int(sys.argv[2])) else 1

    if len(sys.argv) == 2 and sys.argv[1] == "--self-check":
        import queue

        from omr_gui.backend import OMRBackend

        try:
            backend = OMRBackend(queue.Queue())
            devices = backend.enumerate_devices()
            kinds = {device.kind for device in devices}
            backend.shutdown()
            return 0 if {"input", "loopback"}.issubset(kinds) else 2
        except Exception:
            return 1

    if sys.platform == "win32":
        # Gives Windows a stable identity for taskbar grouping and pinned-item
        # icon handling, independent of PyInstaller's temporary extraction dir.
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "OmniMeetingRecorder.Desktop"
        )

    from omr_gui.app import RecorderApp

    app = RecorderApp()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
