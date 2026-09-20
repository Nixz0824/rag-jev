"""Own and supervise this project's three local processes.

The supervisor is deliberately boring: start llama-server twice, start the app
server once, wait until each answers, then tear everything down on exit.

Two Windows-specific details matter and are easy to get wrong:

* llama-server reads ``--api-key-file`` with a narrow (non-UTF-8) file API, so an
  absolute path containing non-ASCII characters fails to open. The key file is
  therefore always passed relative to the child's working directory (ROOT).
* children are placed in a job object with KILL_ON_JOB_CLOSE, so closing or
  killing this supervisor cannot leave model servers orphaned on 18891/18892.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from config import APP_PORT, CHAT_PORT, EMBED_PORT, KEY_FILE, KEY_FILE_ARG, MODELS, PORTS, ROOT, RUNTIME, SERVICE

HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class StartupError(RuntimeError):
    """A startup failure with an operator-readable explanation attached."""


def check(port: int, path: str = "/health"):
    """Return the JSON body a local service serves, or None when it is down."""
    try:
        with HTTP.open(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
            return json.load(response)
    except Exception:
        return None


def tail(path: Path, lines: int = 20) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.strip().splitlines()[-lines:]


def diagnosis(logs: list[tuple[str, Path]]) -> str:
    """The reason a child died is almost always in its own log, so print it."""
    blocks = []
    for name, path in logs:
        lines = tail(path)
        if lines:
            blocks.append(f"--- {name} ({path.name}) ---\n" + "\n".join(lines))
    return "\n".join(blocks)


def windows_job():
    """Create a job object that kills its members when this process dies."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimit),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            return None
        return job
    except Exception:
        return None


def join_job(job, process: subprocess.Popen) -> None:
    if job is None:
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(int(process._handle)))
    except Exception:
        pass


def model_path(*parts: str) -> str:
    """Relative when the models live inside the project, absolute otherwise."""
    path = MODELS.joinpath(*parts)
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG Jev 本地服务启动器")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--stop", action="store_true", help="请求已运行的启动器停止全部服务")
    args = parser.parse_args()

    RUNTIME.mkdir(exist_ok=True)
    stop_file = RUNTIME / "stop.request"
    if args.stop:
        stop_file.write_text("stop", encoding="utf-8")
        print("已请求停止；若服务未在运行，可删除 runtime/stop.request。")
        return 0

    existing = check(APP_PORT, "/api/health")
    if existing and existing.get("service") == SERVICE:
        if not args.no_open:
            webbrowser.open(f"http://127.0.0.1:{APP_PORT}")
        return 0
    stop_file.unlink(missing_ok=True)
    (RUNTIME / "processes.json").unlink(missing_ok=True)

    exe = MODELS / "llama" / "llama-server.exe"
    children: list[subprocess.Popen] = []
    logs: list[tuple[str, Path]] = []
    handles: list = []
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    job = windows_job()

    def spawn(name: str, command: list[str]) -> subprocess.Popen:
        log_path = RUNTIME / f"{name}.log"
        handle = log_path.open("w", encoding="utf-8")
        handles.append(handle)
        logs.append((name, log_path))
        process = subprocess.Popen(command, cwd=ROOT, stdout=handle, stderr=handle, creationflags=flags)
        children.append(process)
        join_job(job, process)
        return process

    try:
        busy = []
        for port in PORTS:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    busy.append(port)
        if busy:
            raise StartupError(
                f"端口 {busy} 已被占用，启动器不会终止任何非本项目进程。请先运行停止脚本，或手动确认占用进程。"
            )
        missing = [path for path in (exe, MODELS / "chat.gguf", MODELS / "embedding.gguf") if not path.exists()]
        if missing:
            raise StartupError(
                "缺少运行文件："
                + "、".join(str(path) for path in missing)
                + "；请先运行 scripts/download_models.py 与 scripts/download_runtime.py，或用 RAGJEV_MODELS 指向已有模型目录。"
            )
        KEY_FILE.write_text(secrets.token_hex(32), encoding="utf-8")

        common = ["--api-key-file", KEY_FILE_ARG, "--cors-origins", f"http://127.0.0.1:{APP_PORT}", "--no-webui"]
        spawn(
            "embedding",
            [
                str(exe),
                "-m",
                model_path("embedding.gguf"),
                "--embedding",
                "--pooling",
                "last",
                "--host",
                "127.0.0.1",
                "--port",
                str(EMBED_PORT),
                "-c",
                "2048",
                "-b",
                "2048",
                "-ub",
                "2048",
                "-t",
                "4",
                "--parallel",
                "1",
                *common,
            ],
        )
        spawn(
            "chat",
            [
                str(exe),
                "-m",
                model_path("chat.gguf"),
                "--host",
                "127.0.0.1",
                "--port",
                str(CHAT_PORT),
                "-c",
                "4096",
                "-t",
                "6",
                "--parallel",
                "1",
                *common,
            ],
        )

        deadline = time.time() + 120
        while time.time() < deadline:
            if any(process.poll() is not None for process in children):
                raise StartupError("模型服务启动后立即退出。")
            if check(CHAT_PORT) and check(EMBED_PORT):
                break
            time.sleep(0.5)
        else:
            raise StartupError("模型服务启动超时（120 秒）。")

        spawn("server", [sys.executable, str(ROOT / "server.py")])
        deadline = time.time() + 300
        while time.time() < deadline:
            health = check(APP_PORT, "/api/health")
            if health and health.get("ready"):
                break
            if health and health.get("error"):
                raise StartupError("服务启动失败：" + str(health["error"]))
            if children[-1].poll() is not None:
                raise StartupError("应用服务启动后立即退出。")
            time.sleep(0.8)
        else:
            raise StartupError("向量索引构建超时（300 秒）。")

        (RUNTIME / "processes.json").write_text(
            json.dumps(
                {"supervisor": os.getpid(), "children": [p.pid for p in children], "root": str(ROOT)},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"READY http://127.0.0.1:{APP_PORT}", flush=True)
        if not args.no_open:
            webbrowser.open(f"http://127.0.0.1:{APP_PORT}")
        while not stop_file.exists():
            if any(process.poll() is not None for process in children):
                raise StartupError("已启动的服务意外退出。")
            time.sleep(1)
        return 0
    except StartupError as error:
        print(str(error), file=sys.stderr)
        detail = diagnosis(logs)
        if detail:
            print(detail, file=sys.stderr)
        return 1
    finally:
        for process in reversed(children):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
        for handle in handles:
            handle.close()
        stop_file.unlink(missing_ok=True)
        (RUNTIME / "processes.json").unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
