"""Loopback-only configuration UI; never starts an Agent. Author: FubukiHanasaki."""

from contextlib import contextmanager
import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import secrets
import ssl
import tempfile
import threading
import time
import tomllib
import webbrowser

import httpx
import psutil
from pydantic import ValidationError
from .config import AgentConfig
from .platform import gpu_metrics, save_credential, secure_windows_path

ASSETS = Path(__file__).with_name("gui_assets")
MAX_BODY = 128 * 1024


class GUIError(Exception):
    def __init__(self, message, status=400):
        self.status = status
        super().__init__(message)


def revision(path):
    return hashlib.sha256(path.read_bytes() if path.exists() else b"").hexdigest()


def toml_config(values):
    """Serialize the strictly validated AgentConfig without another dependency."""
    scalar = {
        key: value
        for key, value in values.items()
        if not isinstance(value, dict) and value is not None
    }
    lines = ["# Author: FubukiHanasaki"]
    for key, value in scalar.items():
        lines.append(f"{json.dumps(key)} = {json.dumps(value, ensure_ascii=False)}")
    if values.get("managed_available_memory_mb"):
        lines.append("\n[managed_available_memory_mb]")
        for name, amount in values["managed_available_memory_mb"].items():
            lines.append(f"{json.dumps(name)} = {amount}")
    if values.get("ninfer_models"):
        lines.append("\n[ninfer_models]")
        for name, path in values["ninfer_models"].items():
            lines.append(f"{json.dumps(name)} = {json.dumps(path)}")
    for name, workflow in values.get("comfyui_workflows", {}).items():
        lines.append(f"\n[comfyui_workflows.{json.dumps(name, ensure_ascii=False)}]")
        for key, value in workflow.items():
            lines.append(f"{json.dumps(key)} = {json.dumps(value, ensure_ascii=False)}")
    text = "\n".join(lines) + "\n"
    # JSON booleans/strings/numbers/arrays used here are also valid TOML values.
    AgentConfig(**tomllib.loads(text))
    text.encode("utf-8")
    return text


class ConfigStore:
    def __init__(self, path):
        self.path = Path(path).absolute()
        self.lock = threading.RLock()

    def read(self):
        with self.lock:
            if self.path.is_symlink():
                raise GUIError("配置文件不能是符号链接", 409)
            if self.path.exists():
                if self.path.stat().st_size > MAX_BODY:
                    raise GUIError("配置文件过大", 422)
                values = tomllib.loads(self.path.read_text(encoding="utf-8"))
                config = AgentConfig(**values)
            else:
                config = AgentConfig(
                    server="https://compute.example.com",
                    name=platform.node()[:80] or "my-node",
                )
            return {
                "config": config.model_dump(mode="json"),
                "revision": revision(self.path),
                "path": str(self.path),
            }

    def save(self, data):
        with self.lock:
            if not isinstance(data, dict) or set(data) != {"config", "revision"}:
                raise GUIError("保存参数无效", 422)
            if self.path.is_symlink() or data["revision"] != revision(self.path):
                raise GUIError("配置已被其他窗口或程序修改，请重新加载后保存", 409)
            config = AgentConfig(**data["config"])
            text = toml_config(config.model_dump(mode="json"))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Staging stays in ignored tmp; same filesystem for atomic replace.
            staging = self.path.parent / "tmp" / "gui-config"
            staging.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix="config-", suffix=".toml", dir=staging)
            temporary = Path(name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    secure_windows_path(temporary)
                    stream.write(text)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
            return self.read()

    def config(self):
        if not self.path.is_file():
            raise GUIError("请先保存配置", 409)
        return AgentConfig.load(self.path)


def transport(config):
    return httpx.Client(
        base_url=config.server.rstrip("/"),
        timeout=20,
        trust_env=False,
        follow_redirects=False,
        verify=ssl.create_default_context(cafile=str(config.ca_file))
        if config.ca_file
        else True,
    )


def register(store, data):
    if (
        not isinstance(data, dict)
        or set(data) != {"token"}
        or not isinstance(data["token"], str)
        or not 1 <= len(data["token"]) <= 256
    ):
        raise GUIError("请输入有效的一次性令牌", 422)
    config = store.config()
    if config.credential_file.exists():
        raise GUIError(
            "节点已有凭证；如需重新注册，请先在服务端吊销并妥善处理旧凭证", 409
        )
    if platform.system() not in {"Windows", "Linux"}:
        raise GUIError("当前仅支持 Windows / Linux 节点注册", 422)
    with transport(config) as client:
        response = client.post(
            "/internal/v1/nodes/register",
            json={"token": data["token"], "name": config.name, "os": platform.system()},
        )
        response.raise_for_status()
        identity = response.json()
    if (
        not isinstance(identity, dict)
        or not isinstance(identity.get("credential"), str)
        or not identity.get("node_id")
    ):
        raise GUIError("服务端返回了无效凭证", 502)
    save_credential(config.credential_file, identity)
    return {"message": "节点已注册。GUI 不会自动启动 Agent。"}


def metrics():
    memory = psutil.virtual_memory()
    return {
        # Each HTTP request runs in a new thread. Nonblocking psutil sampling
        # caches per thread and can otherwise report a misleading initial zero.
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "cpu_count": psutil.cpu_count() or 1,
        "memory_percent": memory.percent,
        "memory_total_mb": memory.total // 2**20,
        "memory_available_mb": memory.available // 2**20,
        "gpu": gpu_metrics(),
        "sampled_at": time.time(),
    }


class GUIServer(ThreadingHTTPServer):
    daemon_threads = True

    # No hostname resolution for logging; no access or token logging.
    def __init__(self, path, port=0):
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.cookie_name = f"pcc_gui_{self.server_port}"
        self.store = ConfigStore(path)
        self.csrf = secrets.token_urlsafe(32)
        self.session = secrets.token_urlsafe(32)
        self.api_key = secrets.token_urlsafe(32)
        self.bootstrap = secrets.token_urlsafe(32)
        self.url = self.origin + "/#token=" + self.bootstrap
        self.action_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server: GUIServer

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass

    def reply(
        self, status, body, content_type="application/json; charset=utf-8", cookie=False
    ):
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        if cookie:
            self.send_header(
                "Set-Cookie",
                f"{self.server.cookie_name}={self.server.session}; Path=/; HttpOnly; SameSite=Strict",
            )
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def guard(self, write=False):
        if self.client_address[0] != "127.0.0.1" or self.headers.get(
            "Host"
        ) != self.server.origin.removeprefix("http://"):
            raise GUIError("仅允许本机访问", 403)
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            raise GUIError("来源校验失败", 403)
        if self.headers.get("Sec-Fetch-Site") not in {None, "same-origin", "none"}:
            raise GUIError("拒绝跨站请求", 403)
        if self.path.startswith("/api/") and self.path != "/api/session":
            cookie = SimpleCookie()
            cookie.load(self.headers.get("Cookie", ""))
            value = cookie.get(self.server.cookie_name)
            if not value or not hmac.compare_digest(value.value, self.server.session):
                raise GUIError("请从本机 GUI 首页打开", 403)
            # Cookies are not isolated by TCP port. A second local web service
            # must not gain GUI access merely by receiving this browser cookie.
            if not hmac.compare_digest(
                self.headers.get("X-PCC-GUI", ""), self.server.api_key
            ):
                raise GUIError("请使用终端生成的完整配置页链接", 403)
        if (
            write
            and self.path != "/api/session"
            and (
                origin != self.server.origin
                or not hmac.compare_digest(
                    self.headers.get("X-CSRF-Token", ""), self.server.csrf
                )
            )
        ):
            raise GUIError("配置写入校验失败", 403)

    def dispatch(self, write=False):
        try:
            self.guard(write)
            if not write:
                if self.path == "/api/state":
                    self.reply(
                        200, {**self.server.store.read(), "csrf": self.server.csrf}
                    )
                elif self.path == "/api/metrics":
                    self.reply(200, metrics())
                elif self.path in {
                    "/",
                    "/index.html",
                    "/gui.js",
                    "/gui.css",
                    "/theme.css",
                }:
                    name = (
                        "index.html"
                        if self.path in {"/", "/index.html"}
                        else self.path[1:]
                    )
                    types = {
                        ".html": "text/html",
                        ".js": "text/javascript",
                        ".css": "text/css",
                    }
                    self.reply(
                        200,
                        (ASSETS / name).read_bytes(),
                        types[Path(name).suffix] + "; charset=utf-8",
                        cookie=False,
                    )
                else:
                    raise GUIError("页面不存在", 404)
                return
            if self.headers.get("Content-Type", "").split(";")[
                0
            ] != "application/json" or self.headers.get("Transfer-Encoding"):
                raise GUIError("仅接受 JSON", 415)
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                raise GUIError("请求体超出限制", 413)
            data = json.loads(self.rfile.read(length))
            with self.server.action_lock:
                if self.path == "/api/session":
                    if (
                        self.headers.get("Origin") != self.server.origin
                        or not isinstance(data, dict)
                        or not isinstance(data.get("token"), str)
                        or not hmac.compare_digest(data["token"], self.server.bootstrap)
                    ):
                        raise GUIError("请使用终端生成的完整配置页链接", 403)
                    self.reply(200, {"key": self.server.api_key}, cookie=True)
                elif self.path == "/api/config":
                    self.reply(200, self.server.store.save(data))
                elif self.path == "/api/connect":
                    with transport(self.server.store.config()) as client:
                        response = client.get("/healthz")
                        response.raise_for_status()
                        if response.json().get("status") != "ok":
                            raise GUIError("服务端健康检查未通过", 502)
                    self.reply(200, {"message": "HTTPS 连接与服务端健康检查通过"})
                elif self.path == "/api/register":
                    self.reply(200, register(self.server.store, data))
                else:
                    raise GUIError("操作不存在", 404)
        except GUIError as exc:
            self.reply(exc.status, {"error": str(exc)})
        except ValidationError as exc:
            errors = [
                ".".join(map(str, item["loc"])) + ": " + item["msg"]
                for item in exc.errors(include_input=False, include_url=False)
            ]
            self.reply(422, {"error": "；".join(errors)})
        except CookieError:
            self.reply(403, {"error": "本机会话无效，请重新打开配置页链接"})
        except (ValueError, UnicodeError, TypeError, RecursionError):
            self.reply(422, {"error": "配置或请求格式不合法，请检查字段"})
        except httpx.HTTPStatusError as exc:
            self.reply(
                502,
                {
                    "error": f"服务端拒绝请求（HTTP {exc.response.status_code}），请检查地址或令牌"
                },
            )
        except (httpx.HTTPError, ssl.SSLError):
            self.reply(
                502,
                {
                    "error": "连接失败，请检查服务端地址、网络和 CA 证书；TLS 校验保持开启"
                },
            )
        except OSError:
            self.reply(
                500, {"error": "本机文件或权限操作失败，请检查配置路径与账号权限"}
            )

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch(write=True)


@contextmanager
def gui_server(path, port=0):
    server = GUIServer(path, port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def serve(path, port=0, open_browser=True):
    with gui_server(path, port) as server:
        print(
            f"Agent 配置页：{server.url} （本机会话链接；Ctrl+C 关闭；不会自动启动任务）",
            flush=True,
        )
        if open_browser:
            webbrowser.open(server.url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
