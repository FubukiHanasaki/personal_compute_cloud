"""Windows AppContainer + Job Object for approved stdlib workers. Author: Fubuki.

No network capabilities, no inherited application handles, one process, bounded
memory/CPU/output and kill-on-close. A dedicated CPython embeddable runtime is
required; the Agent's interpreter and credentials are never granted to workers.
"""

import ctypes as C
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import re
from .errors import WorkerStopUnconfirmed

DWORD = C.c_uint32
HANDLE = C.c_void_p
SIZE = C.c_size_t
BOOL = C.c_int32


class BasicLimits(C.Structure):
    _fields_ = [
        ("process_time", C.c_int64),
        ("job_time", C.c_int64),
        ("flags", DWORD),
        ("min_working_set", SIZE),
        ("max_working_set", SIZE),
        ("processes", DWORD),
        ("affinity", SIZE),
        ("priority", DWORD),
        ("scheduling", DWORD),
    ]


class IoCounters(C.Structure):
    _fields_ = [
        (name, C.c_uint64)
        for name in (
            "read_ops",
            "write_ops",
            "other_ops",
            "read_bytes",
            "write_bytes",
            "other_bytes",
        )
    ]


class ExtendedLimits(C.Structure):
    _fields_ = [
        ("basic", BasicLimits),
        ("io", IoCounters),
        ("process_memory", SIZE),
        ("job_memory", SIZE),
        ("peak_process_memory", SIZE),
        ("peak_job_memory", SIZE),
    ]


class Accounting(C.Structure):
    _fields_ = [
        (name, C.c_int64) for name in ("user", "kernel", "period_user", "period_kernel")
    ] + [
        (name, DWORD)
        for name in (
            "faults",
            "total_processes",
            "active_processes",
            "terminated_processes",
        )
    ]


class Startup(C.Structure):
    _fields_ = (
        [
            ("cb", DWORD),
            ("reserved", C.c_wchar_p),
            ("desktop", C.c_wchar_p),
            ("title", C.c_wchar_p),
        ]
        + [
            (name, DWORD)
            for name in (
                "x",
                "y",
                "width",
                "height",
                "chars_x",
                "chars_y",
                "fill",
                "flags",
            )
        ]
        + [
            ("show", C.c_uint16),
            ("reserved_size", C.c_uint16),
            ("reserved_data", HANDLE),
            ("stdin", HANDLE),
            ("stdout", HANDLE),
            ("stderr", HANDLE),
        ]
    )


class StartupEx(C.Structure):
    _fields_ = [("startup", Startup), ("attributes", HANDLE)]


class ProcessInfo(C.Structure):
    _fields_ = [("process", HANDLE), ("thread", HANDLE), ("pid", DWORD), ("tid", DWORD)]


class Capabilities(C.Structure):
    _fields_ = [
        ("sid", HANDLE),
        ("capabilities", HANDLE),
        ("count", DWORD),
        ("reserved", DWORD),
    ]


def runtime_root(config):
    root = config.windows_worker_runtime
    if root is None:
        raise RuntimeError("Windows requires a dedicated embeddable Python runtime")
    root = Path(root).resolve(strict=True)
    if not (root / "python.exe").is_file() or not list(root.glob("python3*.zip")):
        raise RuntimeError("Not a CPython embeddable runtime")
    for private in (config.credential_file, config.work_root, Path(__file__)):
        if Path(private).resolve().is_relative_to(root):
            raise RuntimeError(
                "Worker runtime must not contain Agent code, work or credentials"
            )
    if root.is_relative_to(config.work_root.resolve()):
        raise RuntimeError("Worker runtime must be separate from the work directory")
    # No junctions/symlinks that could grant package access outside this runtime.
    if any(
        path.is_symlink() or getattr(path, "is_junction", lambda: False)()
        for path in root.rglob("*")
    ):
        raise RuntimeError("Worker runtime must not contain reparse points")
    for path in root.iterdir():
        if not path.is_file() or not (
            path.name in {"python.exe", "pythonw.exe", "python.cat", "LICENSE.txt"}
            or path.suffix in {".dll", ".pyd"}
            or re.fullmatch(r"python3\d+\.(zip|_pth)", path.name)
        ):
            raise RuntimeError(
                "Worker runtime may contain only embeddable vendor files"
            )
    return root


def api():
    if os.name != "nt":
        raise RuntimeError("Windows sandbox is available only on Windows")
    if C.sizeof(HANDLE) != 8:
        raise RuntimeError("Windows workers require a 64-bit Agent and runtime")
    kernel = C.WinDLL("kernel32", use_last_error=True)
    userenv = C.WinDLL("userenv", use_last_error=True)
    advapi = C.WinDLL("advapi32", use_last_error=True)
    definitions = {
        "CreateJobObjectW": ([HANDLE, C.c_wchar_p], HANDLE),
        "SetInformationJobObject": ([HANDLE, C.c_int, HANDLE, DWORD], BOOL),
        "QueryInformationJobObject": ([HANDLE, C.c_int, HANDLE, DWORD, HANDLE], BOOL),
        "AssignProcessToJobObject": ([HANDLE, HANDLE], BOOL),
        "TerminateJobObject": ([HANDLE, DWORD], BOOL),
        "CloseHandle": ([HANDLE], BOOL),
        "LocalFree": ([HANDLE], HANDLE),
        "CreateProcessW": (
            [
                C.c_wchar_p,
                C.c_wchar_p,
                HANDLE,
                HANDLE,
                BOOL,
                DWORD,
                HANDLE,
                C.c_wchar_p,
                C.POINTER(StartupEx),
                C.POINTER(ProcessInfo),
            ],
            BOOL,
        ),
        "InitializeProcThreadAttributeList": (
            [HANDLE, DWORD, DWORD, C.POINTER(SIZE)],
            BOOL,
        ),
        "UpdateProcThreadAttribute": (
            [HANDLE, DWORD, SIZE, HANDLE, SIZE, HANDLE, HANDLE],
            BOOL,
        ),
        "DeleteProcThreadAttributeList": ([HANDLE], None),
        "ResumeThread": ([HANDLE], DWORD),
        "WaitForSingleObject": ([HANDLE, DWORD], DWORD),
        "GetExitCodeProcess": ([HANDLE, C.POINTER(DWORD)], BOOL),
        "TerminateProcess": ([HANDLE, DWORD], BOOL),
    }
    for name, (args, result) in definitions.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = args, result
    userenv.CreateAppContainerProfile.argtypes = [
        C.c_wchar_p,
        C.c_wchar_p,
        C.c_wchar_p,
        HANDLE,
        DWORD,
        C.POINTER(HANDLE),
    ]
    userenv.CreateAppContainerProfile.restype = C.c_int32
    userenv.DeleteAppContainerProfile.argtypes = [C.c_wchar_p]
    userenv.DeleteAppContainerProfile.restype = C.c_int32
    advapi.ConvertSidToStringSidW.argtypes = [HANDLE, C.POINTER(C.c_wchar_p)]
    advapi.ConvertSidToStringSidW.restype = BOOL
    advapi.FreeSid.argtypes, advapi.FreeSid.restype = [HANDLE], HANDLE
    return kernel, userenv, advapi


def checked(result):
    if not result:
        raise C.WinError(C.get_last_error())
    return result


class WindowsWorker:
    def __init__(self, config, payload):
        self.kernel, self.userenv, self.advapi = api()
        self.lock = threading.RLock()
        self.close_lock = threading.Lock()
        self.job = self.handle = None
        self.pid = None
        self.profile = None
        self.sid = HANDLE()
        self.runtime = runtime_root(config)
        self.directory = tempfile.TemporaryDirectory(
            prefix="win-job-", dir=config.work_root
        )
        self.root = Path(self.directory.name)
        self.threads = []
        self.timer = None
        self.output, self.stderr = io.BytesIO(), io.BytesIO()
        self.overflow = False
        self.returncode = None
        self.process = self
        self.pipes = []
        self.sid_text = None
        self.closed = False
        self.marker = None
        try:
            self._start(payload)
        except BaseException:
            try:
                self.close()
            except Exception as exc:
                raise WorkerStopUnconfirmed(
                    "Windows worker cleanup requires reconciliation"
                ) from exc
            raise

    def acl(self, *args):
        subprocess.run(
            [
                str(Path(os.environ["SystemRoot"]) / "System32" / "icacls.exe"),
                *map(str, args),
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )

    def _start(self, payload):
        import msvcrt

        worker = payload["template"]["worker"]
        if worker not in {"compute.stats", "synthetic.chat", "synthetic.image"}:
            raise RuntimeError("Unapproved Windows worker")
        limits = payload["limits"]
        wall_deadline = min(
            time.monotonic() + limits["timeout_seconds"],
            payload.get("_local_deadline", float("inf")),
        )
        self.output_limit = min(int(limits["output_bytes"]) * 2 + 65536, 20 * 2**20)
        self.profile = "PCC.Worker." + uuid.uuid4().hex
        hr = self.userenv.CreateAppContainerProfile(
            self.profile,
            "PCC Worker",
            "PCC approved worker",
            None,
            0,
            C.byref(self.sid),
        )
        if hr < 0:
            self.profile = None
            raise OSError(f"CreateAppContainerProfile failed: {hr:#x}")
        sid_string = C.c_wchar_p()
        checked(self.advapi.ConvertSidToStringSidW(self.sid, C.byref(sid_string)))
        self.sid_text = sid_string.value
        self.kernel.LocalFree(C.cast(sid_string, HANDLE))
        import psutil

        self.marker = config_root = self.root.parent / (
            "windows-profile-" + self.profile.rsplit(".", 1)[1] + ".json"
        )
        with config_root.open("x", encoding="utf-8") as stream:
            json.dump(
                {
                    "version": 1,
                    "profile": self.profile,
                    "sid": self.sid_text,
                    "runtime": str(self.runtime),
                    "controller_pid": os.getpid(),
                    "controller_created_at": psutil.Process().create_time(),
                },
                stream,
            )
        self.acl(self.runtime, "/grant", f"*{self.sid_text}:(OI)(CI)RX", "/T", "/Q")
        self.acl(self.root, "/grant", f"*{self.sid_text}:(OI)(CI)M", "/Q")
        self.acl(self.root, "/setintegritylevel", "(OI)(CI)L", "/Q")
        shutil.copyfile(Path(__file__).with_name("worker.py"), self.root / "worker.py")
        self.job = checked(self.kernel.CreateJobObjectW(None, None))
        info = ExtendedLimits()
        info.basic.flags = (
            0x2000 | 0x200 | 0x100 | 0x8 | 0x4
        )  # kill on close, job/process memory, active process, CPU time
        info.basic.processes = 1
        info.basic.job_time = max(
            1, int(limits["timeout_seconds"] * limits["cpu"] * 10**7)
        )
        info.job_memory = info.process_memory = int(limits["memory_mb"]) * 2**20
        checked(
            self.kernel.SetInformationJobObject(
                self.job, 9, C.byref(info), C.sizeof(info)
            )
        )
        rate = (DWORD * 2)(
            0x1 | 0x4,
            min(10000, max(1, int(limits["cpu"] / (os.cpu_count() or 1) * 10000))),
        )
        checked(self.kernel.SetInformationJobObject(self.job, 15, rate, C.sizeof(rate)))
        ui = DWORD(0xFF)
        checked(
            self.kernel.SetInformationJobObject(self.job, 4, C.byref(ui), C.sizeof(ui))
        )

        def pipe():
            pair = os.pipe()
            self.pipes.extend(pair)
            return pair

        stdin_read, stdin_write = pipe()
        stdout_read, stdout_write = pipe()
        stderr_read, stderr_write = pipe()
        child_fds = [stdin_read, stdout_write, stderr_write]
        for fd in child_fds:
            os.set_inheritable(fd, True)
        handles = (HANDLE * 3)(*(msvcrt.get_osfhandle(fd) for fd in child_fds))
        caps = Capabilities(self.sid, None, 0, 0)
        size = SIZE()
        self.kernel.InitializeProcThreadAttributeList(None, 3, 0, C.byref(size))
        attributes = C.create_string_buffer(size.value)
        checked(
            self.kernel.InitializeProcThreadAttributeList(
                attributes, 3, 0, C.byref(size)
            )
        )
        process = ProcessInfo()
        try:
            checked(
                self.kernel.UpdateProcThreadAttribute(
                    attributes, 0, 0x20009, C.byref(caps), C.sizeof(caps), None, None
                )
            )
            checked(
                self.kernel.UpdateProcThreadAttribute(
                    attributes, 0, 0x20002, handles, C.sizeof(handles), None, None
                )
            )
            # Attach at creation so an Agent crash cannot leave a process
            # between CreateProcess and AssignProcessToJobObject.
            jobs = (HANDLE * 1)(self.job)
            checked(
                self.kernel.UpdateProcThreadAttribute(
                    attributes, 0, 0x2000D, jobs, C.sizeof(jobs), None, None
                )
            )
            startup = StartupEx()
            startup.startup.cb = C.sizeof(startup)
            startup.startup.flags = 0x100  # STARTF_USESTDHANDLES
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = (
                handles
            )
            startup.attributes = C.cast(attributes, HANDLE)
            executable = str(self.runtime / "python.exe")
            command = C.create_unicode_buffer(
                subprocess.list2cmdline(
                    [executable, "-I", "-B", str(self.root / "worker.py")]
                )
            )
            env = {
                "SystemRoot": os.environ["SystemRoot"],
                "TEMP": str(self.root),
                "TMP": str(self.root),
                "PYTHONUTF8": "1",
            }
            environment = C.create_unicode_buffer(
                "\0".join(f"{key}={value}" for key, value in sorted(env.items()))
                + "\0\0"
            )
            checked(
                self.kernel.CreateProcessW(
                    executable,
                    command,
                    None,
                    None,
                    True,
                    0x80000 | 0x4 | 0x400 | 0x08000000,
                    environment,
                    str(self.root),
                    C.byref(startup),
                    C.byref(process),
                )
            )
            self.handle, self.pid = process.process, process.pid
            for fd in child_fds:
                os.close(fd)
                self.pipes.remove(fd)
            for fd, buffer in ((stdout_read, self.output), (stderr_read, self.stderr)):
                thread = threading.Thread(
                    target=self._read, args=(fd, buffer), daemon=True
                )
                thread.start()
                self.threads.append(thread)
            # Arm before ResumeThread/stdin writes, which can block on a pipe.
            startup_deadline = min(
                wall_deadline, payload.get("_local_start_deadline", float("inf"))
            )
            self._arm_timer(startup_deadline)
            if self.kernel.ResumeThread(process.thread) == 0xFFFFFFFF:
                raise C.WinError(C.get_last_error())
        finally:
            if process.thread:
                self.kernel.CloseHandle(process.thread)
            self.kernel.DeleteProcThreadAttributeList(attributes)
        data = json.dumps({"worker": worker, "input": payload["input"]}).encode("utf-8")
        with os.fdopen(stdin_write, "wb") as stream:
            self.pipes.remove(stdin_write)
            stream.write(data)
        if time.monotonic() >= startup_deadline:
            raise RuntimeError("Lease expired during Windows worker startup")
        # The Agent's lease guard takes over after construction; renewed leases
        # must not be cut short by the initial startup lease timer.
        self._arm_timer(wall_deadline)

    def _arm_timer(self, deadline):
        if self.timer:
            self.timer.cancel()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Deadline expired before Windows worker startup")
        self.timer = threading.Timer(remaining, self.stop)
        self.timer.daemon = True
        self.timer.start()

    def _read(self, fd, output):
        try:
            while chunk := os.read(fd, 8192):
                if output.tell() + len(chunk) > self.output_limit:
                    self.overflow = True
                    self.stop()
                    return
                output.write(chunk)
        except OSError:
            return

    def poll(self):
        with self.lock:
            if self.returncode is not None:
                return self.returncode
            if self.handle and self.kernel.WaitForSingleObject(self.handle, 0) == 0:
                code = DWORD()
                checked(self.kernel.GetExitCodeProcess(self.handle, C.byref(code)))
                self.returncode = code.value
            return self.returncode

    def wait(self, timeout=None):
        until = time.monotonic() + (timeout if timeout is not None else 86400)
        while self.poll() is None:
            if time.monotonic() >= until:
                raise subprocess.TimeoutExpired("PCC Windows Worker", timeout)
            time.sleep(0.02)
        return self.returncode

    def stop(self):
        with self.lock:
            if self.job:
                checked(self.kernel.TerminateJobObject(self.job, 1))
            if self.handle and self.poll() is None:
                # Also covers failure before assignment, while still suspended.
                terminated = self.kernel.TerminateProcess(self.handle, 1)
                if not terminated and self.poll() is None:
                    checked(terminated)
            if self.handle:
                self.wait(timeout=10)
            if self.job:
                until = time.monotonic() + 10
                while True:
                    accounting = Accounting()
                    checked(
                        self.kernel.QueryInformationJobObject(
                            self.job, 1, C.byref(accounting), C.sizeof(accounting), None
                        )
                    )
                    if accounting.active_processes == 0:
                        break
                    if time.monotonic() >= until:
                        raise RuntimeError("Cannot confirm Windows Job Object stopped")
                    time.sleep(0.02)

    def result(self):
        if self.wait() != 0:
            raise RuntimeError("Windows worker failed or exceeded a resource limit")
        for thread in self.threads:
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("Worker output did not close")
        if self.overflow:
            raise RuntimeError("Worker exceeded output limit")
        return json.loads(self.output.getvalue())

    def close(self):
        # Readers may call stop(), which takes self.lock. Never hold that lock
        # while joining them, or output overflow races with cleanup.
        with self.close_lock:
            if self.closed:
                return
            if self.timer:
                self.timer.cancel()
            self.stop()
            for thread in self.threads:
                if thread is not threading.current_thread():
                    thread.join(timeout=10)
                    if thread.is_alive():
                        raise RuntimeError(
                            "Cannot confirm Windows output reader stopped"
                        )
            while self.pipes:
                fd = self.pipes[0]
                os.close(fd)
                self.pipes.remove(fd)
            with self.lock:
                for handle in (self.handle, self.job):
                    if handle:
                        self.kernel.CloseHandle(handle)
                self.handle = self.job = None
            if self.sid_text:
                self.acl(self.runtime, "/remove:g", f"*{self.sid_text}", "/T", "/Q")
                self.sid_text = None
            if self.profile:
                result = self.userenv.DeleteAppContainerProfile(self.profile)
                if result < 0:
                    raise OSError(f"DeleteAppContainerProfile failed: {result:#x}")
                self.profile = None
            if self.sid:
                self.advapi.FreeSid(self.sid)
                self.sid = HANDLE()
            self.directory.cleanup()
            if self.marker:
                self.marker.unlink(missing_ok=True)
            self.closed = True


def cleanup_stale_profiles(config):
    """Explicit operator recovery after the owning Agent has exited."""
    import psutil

    kernel, userenv, advapi = api()
    runtime = runtime_root(config)
    count = 0
    for marker in config.work_root.glob("windows-profile-*.json"):
        if marker.is_symlink() or marker.stat().st_size > 10000:
            raise RuntimeError("Invalid Windows recovery marker")
        record = json.loads(marker.read_text(encoding="utf-8"))
        profile = record.get("profile", "")
        sid = record.get("sid", "")
        if (
            record.get("version") != 1
            or not re.fullmatch(r"PCC\.Worker\.[a-f0-9]{32}", profile)
            or not re.fullmatch(r"S-1-15-2-(?:\d+-)*\d+", sid)
            or Path(record.get("runtime", "")).resolve() != runtime
        ):
            raise RuntimeError("Invalid Windows recovery identity")
        pid = record.get("controller_pid")
        if (
            pid
            and psutil.pid_exists(pid)
            and psutil.Process(pid).create_time() == record.get("controller_created_at")
        ):
            raise RuntimeError("Owning Agent is still alive; stop it before cleanup")
        # The exact package SID is derived independently from its moniker.
        userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [
            C.c_wchar_p,
            C.POINTER(HANDLE),
        ]
        userenv.DeriveAppContainerSidFromAppContainerName.restype = C.c_int32
        derived = HANDLE()
        hr = userenv.DeriveAppContainerSidFromAppContainerName(
            profile, C.byref(derived)
        )
        if hr < 0:
            raise OSError("Cannot derive AppContainer recovery SID")
        sid_string = C.c_wchar_p()
        try:
            checked(advapi.ConvertSidToStringSidW(derived, C.byref(sid_string)))
            if sid_string.value != sid:
                raise RuntimeError("Windows recovery SID mismatch")
        finally:
            if sid_string:
                kernel.LocalFree(C.cast(sid_string, HANDLE))
            advapi.FreeSid(derived)
        executable = Path(os.environ["SystemRoot"]) / "System32/icacls.exe"
        subprocess.run(
            [str(executable), str(runtime), "/remove:g", f"*{sid}", "/T", "/Q"],
            check=True,
            capture_output=True,
            timeout=30,
        )
        hr = userenv.DeleteAppContainerProfile(profile)
        if hr < 0 and hr not in {-2147024894, -2147024893, -2147023728}:
            raise OSError(f"Cannot remove AppContainer profile: {hr:#x}")
        marker.unlink()
        count += 1
    return count


def probe(config):
    if os.name != "nt" or config.windows_worker_runtime is None:
        return False
    try:
        worker = WindowsWorker(
            config,
            {
                "template": {"worker": "compute.stats"},
                "input": {"values": [1]},
                "limits": {
                    "cpu": 1,
                    "memory_mb": 256,
                    "timeout_seconds": 10,
                    "output_bytes": 65536,
                },
            },
        )
        try:
            return worker.result()["result"]["sum"] == 1
        finally:
            try:
                worker.close()
            except Exception as exc:
                raise WorkerStopUnconfirmed(
                    "Windows probe cleanup requires reconciliation"
                ) from exc
    except WorkerStopUnconfirmed:
        # An unknown surviving probe is not an unavailable capability. Abort
        # startup so the OS closes this Agent's kill-on-close Job handles.
        raise
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        return False
