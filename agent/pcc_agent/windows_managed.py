"""Owned Windows model processes, atomically attached to bounded Jobs. Fubuki.

Approved model runtimes run under the standard Agent account. This is resource
and process-tree containment, not the AppContainer used for compute templates.
"""

import ctypes as C
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid
import psutil
from .errors import WorkerStopUnconfirmed
from .windows_sandbox import (
    Accounting,
    DWORD,
    ExtendedLimits,
    HANDLE,
    ProcessInfo,
    SIZE,
    StartupEx,
    api,
    checked,
)

JOB_PREFIX = "Local\\PCC.Managed."
MAX_PROCESSES = 256


def limits(resources, cpus):
    """Job memory is the aggregate committed memory of the process tree."""
    cpu, memory = float(resources["cpu"]), int(resources["memory_mb"])
    if not 0 < cpu <= 65536 or memory < 128:
        raise ValueError("Insufficient enforceable model resources")
    info = ExtendedLimits()
    info.basic.flags = 0x2000 | 0x200 | 0x100 | 0x8
    info.basic.processes = MAX_PROCESSES
    info.job_memory = info.process_memory = memory * 2**20
    rate = (DWORD * 2)(0x1 | 0x4, min(10000, max(1, int(cpu / max(1, cpus) * 10000))))
    return info, rate


class WindowsManagedProcess:
    def __init__(self, resources, name=None):
        self.kernel, _, _ = api()
        self.resources = resources
        self.name = name or JOB_PREFIX + uuid.uuid4().hex
        if not re.fullmatch(r"Local\\PCC\.Managed\.[a-f0-9]{32}", self.name):
            raise ValueError("Invalid managed Job identity")
        self.lock = threading.RLock()
        self.job = self.handle = None
        self.pid = None
        self.returncode = None
        self.started = False

    def start(self, command, env, stdout, cwd=None):
        import msvcrt

        with self.lock:
            if self.started:
                raise RuntimeError("A managed Job can only be started once")
            self.started = True
            fds = []
            attributes = None
            attributes_ready = False
            process = ProcessInfo()
            try:
                C.set_last_error(0)
                self.job = checked(self.kernel.CreateJobObjectW(None, self.name))
                if C.get_last_error() == 183:
                    # Never attach to an existing object, even on a name collision.
                    foreign = self.job
                    self.job = None
                    checked(self.kernel.CloseHandle(foreign))
                    raise RuntimeError("Managed Job identity already exists")
                info, rate = limits(self.resources, os.cpu_count() or 1)
                checked(
                    self.kernel.SetInformationJobObject(
                        self.job, 9, C.byref(info), C.sizeof(info)
                    )
                )
                checked(
                    self.kernel.SetInformationJobObject(
                        self.job, 15, rate, C.sizeof(rate)
                    )
                )
                fds.append(os.open(os.devnull, os.O_RDONLY))
                fds.append(os.dup(stdout.fileno()))
                for fd in fds:
                    os.set_inheritable(fd, True)
                handles = (HANDLE * 2)(*(msvcrt.get_osfhandle(fd) for fd in fds))
                size = SIZE()
                self.kernel.InitializeProcThreadAttributeList(None, 2, 0, C.byref(size))
                attributes = C.create_string_buffer(size.value)
                checked(
                    self.kernel.InitializeProcThreadAttributeList(
                        attributes, 2, 0, C.byref(size)
                    )
                )
                attributes_ready = True
                jobs = (HANDLE * 1)(self.job)
                checked(
                    self.kernel.UpdateProcThreadAttribute(
                        attributes, 0, 0x2000D, jobs, C.sizeof(jobs), None, None
                    )
                )
                checked(
                    self.kernel.UpdateProcThreadAttribute(
                        attributes, 0, 0x20002, handles, C.sizeof(handles), None, None
                    )
                )
                startup = StartupEx()
                startup.startup.cb = C.sizeof(startup)
                startup.startup.flags = 0x100
                startup.startup.stdin = handles[0]
                startup.startup.stdout = startup.startup.stderr = handles[1]
                startup.attributes = C.cast(attributes, HANDLE)
                environment = C.create_unicode_buffer(
                    "\0".join(
                        f"{key}={value}"
                        for key, value in sorted(
                            env.items(), key=lambda item: item[0].upper()
                        )
                    )
                    + "\0\0"
                )
                checked(
                    self.kernel.CreateProcessW(
                        str(command[0]),
                        C.create_unicode_buffer(
                            subprocess.list2cmdline(list(map(str, command)))
                        ),
                        None,
                        None,
                        True,
                        0x80000 | 0x4 | 0x400 | 0x08000000,
                        environment,
                        str(cwd) if cwd else None,
                        C.byref(startup),
                        C.byref(process),
                    )
                )
                self.handle, self.pid = process.process, process.pid
                if self.kernel.ResumeThread(process.thread) == 0xFFFFFFFF:
                    raise C.WinError(C.get_last_error())
            except BaseException:
                try:
                    self.close()
                except Exception as exc:
                    raise WorkerStopUnconfirmed(
                        "Managed Windows process cleanup requires reconciliation"
                    ) from exc
                raise
            finally:
                if process.thread:
                    self.kernel.CloseHandle(process.thread)
                if attributes_ready:
                    self.kernel.DeleteProcThreadAttributeList(attributes)
                for fd in fds:
                    os.close(fd)
        return self

    def poll(self):
        with self.lock:
            if self.returncode is None and self.handle:
                state = self.kernel.WaitForSingleObject(self.handle, 0)
                if state == 0xFFFFFFFF:
                    raise C.WinError(C.get_last_error())
                if state == 0:
                    code = DWORD()
                    checked(self.kernel.GetExitCodeProcess(self.handle, C.byref(code)))
                    self.returncode = code.value
            return self.returncode

    def wait(self, timeout=10):
        until = time.monotonic() + timeout
        while self.poll() is None:
            if time.monotonic() >= until:
                raise subprocess.TimeoutExpired("PCC managed Windows process", timeout)
            time.sleep(0.02)
        return self.returncode

    def process_ids(self):
        with self.lock:
            if not self.job:
                return []
            data = C.create_string_buffer(8 + MAX_PROCESSES * C.sizeof(SIZE))
            checked(
                self.kernel.QueryInformationJobObject(
                    self.job, 3, data, C.sizeof(data), None
                )
            )
            listed = DWORD.from_buffer(data, 4).value
            if listed > MAX_PROCESSES:
                raise RuntimeError("Invalid managed process count")
            return list((SIZE * listed).from_buffer(data, 8))

    def owns_listener(self, port):
        for pid in self.process_ids():
            try:
                if any(
                    c.status == psutil.CONN_LISTEN
                    and c.laddr.ip == "127.0.0.1"
                    and c.laddr.port == port
                    for c in psutil.Process(pid).net_connections(kind="tcp")
                ):
                    return True
            except psutil.NoSuchProcess:
                continue
        return False

    def stop(self):
        with self.lock:
            if self.job:
                checked(self.kernel.TerminateJobObject(self.job, 1))
                until = time.monotonic() + 10
                while True:
                    count = Accounting()
                    checked(
                        self.kernel.QueryInformationJobObject(
                            self.job, 1, C.byref(count), C.sizeof(count), None
                        )
                    )
                    if count.active_processes == 0:
                        break
                    if time.monotonic() >= until:
                        raise RuntimeError(
                            "Cannot confirm managed Windows process tree stopped"
                        )
                    time.sleep(0.02)
            if self.handle:
                self.wait(timeout=10)

    def close(self):
        with self.lock:
            self.stop()
            for attribute in ("handle", "job"):
                handle = getattr(self, attribute)
                if handle:
                    checked(self.kernel.CloseHandle(handle))
                    setattr(self, attribute, None)


def active_job(name):
    """Read-only operator check; never terminate a Job found by name."""
    if not re.fullmatch(r"Local\\PCC\.Managed\.[a-f0-9]{32}", name):
        raise ValueError("Invalid managed Job recovery identity")
    kernel, _, _ = api()
    kernel.OpenJobObjectW.argtypes = [DWORD, C.c_int32, C.c_wchar_p]
    kernel.OpenJobObjectW.restype = HANDLE
    job = kernel.OpenJobObjectW(0x4, False, name)  # JOB_OBJECT_QUERY
    if not job:
        code = C.get_last_error()
        if code == 2:
            return False
        raise C.WinError(code)
    try:
        count = Accounting()
        checked(
            kernel.QueryInformationJobObject(
                job, 1, C.byref(count), C.sizeof(count), None
            )
        )
        return count.active_processes > 0
    finally:
        kernel.CloseHandle(job)


def probe():
    process = None
    try:
        process = WindowsManagedProcess({"cpu": 1, "memory_mb": 256})
        system = Path(os.environ["SystemRoot"]) / "System32"
        with open(os.devnull, "ab") as output:
            process.start(
                [sys.executable, "-I", "-B", "-c", "pass"],
                {
                    "SystemRoot": os.environ["SystemRoot"],
                    "PATH": str(system),
                },
                output,
            )
            return process.wait(timeout=10) == 0
    except WorkerStopUnconfirmed:
        raise
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return False
    finally:
        if process:
            try:
                process.close()
            except Exception as exc:
                raise WorkerStopUnconfirmed(
                    "Managed Windows probe stop is unconfirmed"
                ) from exc
