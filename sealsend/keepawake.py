"""Keep the computer from falling asleep while certificates are sending or a send is scheduled.

macOS: a `caffeinate -i` helper tied to this process. Windows: SetThreadExecutionState (called from
the scheduler thread, which lives as long as the app). Elsewhere (Linux servers) nothing is needed.
A closed laptop lid still puts the computer to sleep.
"""
import os
import subprocess
import sys
import threading

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_held = False


def set_awake(want: bool) -> None:
    global _proc, _held
    with _lock:
        if want == _held:
            return
        _held = want
        try:
            if sys.platform == "darwin":
                if want:
                    _proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif _proc is not None:
                    _proc.terminate()
                    _proc = None
            elif sys.platform == "win32":
                import ctypes
                es_continuous, es_system_required = 0x80000000, 0x00000001
                ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | (es_system_required if want else 0))
        except Exception:
            pass  # best effort: sending works the same without it


def is_held() -> bool:
    return _held
