"""Own a Windows process tree, including children created by FFmpeg wrappers."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any

# These APIs exist only on Windows; this module can also be imported on POSIX.
win: Any = ctypes

CREATE_SUSPENDED = 0x00000004
PROCESS_ACCESS = 0x0001 | 0x0100 | 0x0800  # terminate, set quota, suspend/resume


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64),
        ("job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD),
        ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t),
        ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", BasicLimits),
        ("io_counters", ctypes.c_uint64 * 6),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class WindowsJob:
    def __init__(self) -> None:
        self.kernel = win.WinDLL("kernel32", use_last_error=True)
        self.ntdll = win.WinDLL("ntdll")
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.TerminateJobObject.restype = wintypes.BOOL
        self.kernel.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        self.kernel.QueryInformationJobObject.restype = wintypes.BOOL
        self.kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        for name in ("NtSuspendProcess", "NtResumeProcess"):
            function = getattr(self.ntdll, name)
            function.argtypes = [wintypes.HANDLE]
            function.restype = wintypes.LONG
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise win.WinError(win.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = win.get_last_error()
            self.kernel.CloseHandle(self.handle)
            self.handle = None
            raise win.WinError(error)

    def attach(self, pid: int) -> None:
        process = self.kernel.OpenProcess(PROCESS_ACCESS, False, pid)
        if not process:
            raise win.WinError(win.get_last_error())
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, process):
                raise win.WinError(win.get_last_error())
        finally:
            self.kernel.CloseHandle(process)

    def _pids(self) -> list[int]:
        capacity = 64
        while True:
            # JOBOBJECT_BASIC_PROCESS_ID_LIST: two DWORDs then ULONG_PTR IDs.
            buffer = ctypes.create_string_buffer(8 + capacity * ctypes.sizeof(ctypes.c_size_t))
            if self.kernel.QueryInformationJobObject(self.handle, 3, buffer, len(buffer), None):
                count = wintypes.DWORD.from_buffer(buffer, 4).value
                return list((ctypes.c_size_t * count).from_buffer(buffer, 8))
            error = win.get_last_error()
            if error != 234:  # ERROR_MORE_DATA
                raise win.WinError(error)
            capacity *= 2

    def suspend(self, paused: bool) -> None:
        function = self.ntdll.NtSuspendProcess if paused else self.ntdll.NtResumeProcess
        for pid in self._pids():
            process = self.kernel.OpenProcess(PROCESS_ACCESS, False, pid)
            if not process:
                # A child can exit between enumeration and OpenProcess.
                if win.get_last_error() == 87:
                    continue
                raise win.WinError(win.get_last_error())
            try:
                status = function(process)
                if status not in (0, -1073741558):  # STATUS_PROCESS_IS_TERMINATING
                    raise OSError(f"{function.__name__} failed: NTSTATUS {status & 0xFFFFFFFF:#x}")
            finally:
                self.kernel.CloseHandle(process)

    def kill(self) -> None:
        if not self.kernel.TerminateJobObject(self.handle, 1):
            raise win.WinError(win.get_last_error())

    def close(self) -> None:
        if self.handle:
            try:
                self.kill()
            finally:
                self.kernel.CloseHandle(self.handle)
                self.handle = None
