"""Windows console control used for graceful OMR shutdown."""

from __future__ import annotations

import ctypes
import os
import sys


def send_quit_key(process_id: int) -> bool:
    """Attach to *process_id*'s console and write OMR's normal ``q`` key.

    OMR 0.7.2 polls its console with ``msvcrt.kbhit/getch`` and handles ``q``
    by setting its normal stop event, joining its capture thread, and closing
    its WAV/MP3 writer. A separate helper process calls this function so the
    GUI never detaches from its own console when started from a terminal.
    """
    if os.name != "nt":
        return False

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    attach_console = kernel32.AttachConsole
    attach_console.argtypes = [ctypes.c_uint]
    attach_console.restype = ctypes.c_int
    free_console = kernel32.FreeConsole
    free_console.restype = ctypes.c_int
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    write_console_input = kernel32.WriteConsoleInputW

    from ctypes import wintypes

    class CharUnion(ctypes.Union):
        _fields_ = [("UnicodeChar", wintypes.WCHAR), ("AsciiChar", wintypes.CHAR)]

    class KeyEventRecord(ctypes.Structure):
        _fields_ = [
            ("bKeyDown", wintypes.BOOL),
            ("wRepeatCount", wintypes.WORD),
            ("wVirtualKeyCode", wintypes.WORD),
            ("wVirtualScanCode", wintypes.WORD),
            ("uChar", CharUnion),
            ("dwControlKeyState", wintypes.DWORD),
        ]

    class EventUnion(ctypes.Union):
        _fields_ = [("KeyEvent", KeyEventRecord)]

    class InputRecord(ctypes.Structure):
        _fields_ = [("EventType", wintypes.WORD), ("Event", EventUnion)]

    write_console_input.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(InputRecord),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    write_console_input.restype = wintypes.BOOL

    # Depending on the launcher and Python build, CREATE_NO_WINDOW may still
    # leave the short-lived helper attached to its parent's console. Detach
    # first; this never affects the GUI because this function runs only in the
    # dedicated helper process.
    free_console()
    if not attach_console(process_id):
        print(
            f"AttachConsole({process_id}) failed with Windows error {ctypes.get_last_error()}",
            file=sys.stderr,
        )
        return False
    try:
        input_handle = create_file(
            "CONIN$",
            0x80000000 | 0x40000000,  # GENERIC_READ | GENERIC_WRITE
            0x00000001 | 0x00000002,  # FILE_SHARE_READ | FILE_SHARE_WRITE
            None,
            3,  # OPEN_EXISTING
            0,
            None,
        )
        if not input_handle or input_handle == ctypes.c_void_p(-1).value:
            print("Opening CONIN$ failed", file=sys.stderr)
            return False
        try:
            records = (InputRecord * 2)()
            for position, key_down in enumerate((True, False)):
                records[position].EventType = 0x0001  # KEY_EVENT
                records[position].Event.KeyEvent.bKeyDown = key_down
                records[position].Event.KeyEvent.wRepeatCount = 1
                records[position].Event.KeyEvent.wVirtualKeyCode = 0x51  # Q
                records[position].Event.KeyEvent.wVirtualScanCode = 0x10
                records[position].Event.KeyEvent.uChar.UnicodeChar = "q"
                records[position].Event.KeyEvent.dwControlKeyState = 0

            written = wintypes.DWORD()
            sent = bool(write_console_input(input_handle, records, 2, ctypes.byref(written)))
            sent = sent and written.value == 2
            if not sent:
                print(
                    f"WriteConsoleInputW failed with Windows error {ctypes.get_last_error()}",
                    file=sys.stderr,
                )
            return sent
        finally:
            close_handle(input_handle)
    finally:
        free_console()
