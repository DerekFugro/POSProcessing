"""Standard input/output for the installed (windowless) exe.

A windowless PyInstaller exe may leave sys.stdin / sys.stdout as None even when the program
that started it passed pipes. These helpers open the Windows standard handles directly, so the
Surveyor link (--surveyor) and command-line processing (--cli) still work. No Qt here.
"""
from __future__ import annotations

import io
import os
import sys

STD_INPUT_HANDLE, STD_OUTPUT_HANDLE, STD_ERROR_HANDLE = -10, -11, -12
INVALID_HANDLES = (None, 0, -1, 0xFFFFFFFFFFFFFFFF)


def windows_std(which: int, mode: str):
    """Text stream on a Windows standard handle the parent gave us, or None."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        import msvcrt

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.restype = ctypes.c_void_p
        handle = kernel32.GetStdHandle(which)
        if handle in INVALID_HANDLES:
            return None
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY if mode == "r" else os.O_WRONLY)
        raw = io.open(fd, mode + "b", buffering=0)
        return io.TextIOWrapper(io.BufferedReader(raw) if mode == "r" else io.BufferedWriter(raw),
                                encoding="utf-8", newline=None if mode == "r" else "\n",
                                line_buffering=mode == "w")
    except (OSError, ValueError, AttributeError):
        return None


def std_streams():
    """(stdin, stdout): Python's own if it has them, else the Windows handles, else None."""
    stdin = sys.stdin if sys.stdin is not None else windows_std(STD_INPUT_HANDLE, "r")
    stdout = sys.stdout if sys.stdout is not None else windows_std(STD_OUTPUT_HANDLE, "w")
    return stdin, stdout


def attach_std_output():
    """For --cli in the windowless exe: make print() reach the caller's pipe or file."""
    if sys.stdout is None:
        sys.stdout = windows_std(STD_OUTPUT_HANDLE, "w")
    if sys.stderr is None:
        sys.stderr = windows_std(STD_ERROR_HANDLE, "w") or sys.stdout
