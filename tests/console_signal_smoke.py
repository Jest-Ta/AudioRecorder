"""Manual non-audio smoke test for OMR's graceful console stop mechanism."""

from __future__ import annotations

import subprocess
import sys
import time

from omr_gui.backend import CREATE_NEW_CONSOLE, CREATE_NO_WINDOW, _hidden_startupinfo


def main() -> int:
    helper_executable = sys.argv[1] if len(sys.argv) > 1 else None
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import msvcrt,time\n"
            "end=time.time()+60\n"
            "while time.time()<end:\n"
            " if msvcrt.kbhit() and msvcrt.getwch().lower()=='q': raise SystemExit(0)\n"
            " time.sleep(.02)\n",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=CREATE_NEW_CONSOLE,
        startupinfo=_hidden_startupinfo(),
    )
    try:
        time.sleep(0.5)
        helper_command = (
            [helper_executable, "--send-quit-key", str(child.pid)]
            if helper_executable
            else [sys.executable, "Main.py", "--send-quit-key", str(child.pid)]
        )
        helper = subprocess.run(
            helper_command,
            creationflags=CREATE_NO_WINDOW,
            startupinfo=_hidden_startupinfo(),
            capture_output=True,
            text=True,
            check=False,
        )
        print(f"helper_exit={helper.returncode}")
        if helper.stderr:
            print(helper.stderr.strip())
        try:
            child_exit = child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print("child did not stop")
            return 1
        print(f"child_exit={child_exit}")
        return int(helper.returncode != 0 or child_exit != 0)
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=3)


if __name__ == "__main__":
    raise SystemExit(main())
