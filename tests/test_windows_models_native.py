"""Native Job and model protocol checks; real models require opt-in. Fubuki."""

import ctypes as C
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import psutil
import pytest
from pcc_agent.config import AgentConfig
from pcc_agent.windows_managed import WindowsManagedProcess, active_job, probe

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Native Windows model Jobs required"
)


def environment():
    return {
        "SystemRoot": os.environ["SystemRoot"],
        "PATH": os.environ["SystemRoot"] + "\\System32",
    }


def test_native_managed_job_probe_limits_and_orphan_cleanup(tmp_path):
    assert probe()
    pid_file = tmp_path / "child.json"
    command = [
        sys.executable,
        "-I",
        "-B",
        "-c",
        (
            "import json,pathlib,subprocess,sys; "
            "child=subprocess.Popen([sys.executable,'-I','-B','-c','import time; time.sleep(60)']); "
            f"pathlib.Path({str(pid_file)!r}).write_text(json.dumps(child.pid))"
        ),
    ]
    process = WindowsManagedProcess({"cpu": 1, "memory_mb": 512})
    with (tmp_path / "job.log").open("ab") as output:
        try:
            process.start(command, environment(), output)
            assert process.wait(timeout=10) == 0
            child = json.loads(pid_file.read_text())
            assert child in process.process_ids() and active_job(process.name)
            rate = (C.c_uint32 * 2)()
            assert process.kernel.QueryInformationJobObject(
                process.job, 15, rate, C.sizeof(rate), None
            )
            assert rate[0] == 5 and 0 < rate[1] <= 10000
        finally:
            process.close()
    assert not active_job(process.name)
    assert not psutil.pid_exists(child)


def test_native_managed_job_rejects_memory_overrun(tmp_path):
    process = WindowsManagedProcess({"cpu": 1, "memory_mb": 128})
    with (tmp_path / "job.log").open("ab") as output:
        try:
            process.start(
                [sys.executable, "-I", "-B", "-c", "x=bytearray(512*1024*1024)"],
                environment(),
                output,
            )
            assert process.wait(timeout=10) != 0
        finally:
            process.close()


def test_native_agent_exit_kills_owned_model_job(tmp_path):
    record = tmp_path / "spawn.json"
    root = str(Path(__file__).resolve().parents[1] / "agent")
    script = (
        "import json,os,sys; from pathlib import Path; "
        f"sys.path.insert(0,{root!r}); "
        "from pcc_agent.windows_managed import WindowsManagedProcess; "
        "p=WindowsManagedProcess({'cpu':1,'memory_mb':256}); "
        "log=open(os.devnull,'ab'); "
        "p.start([sys.executable,'-I','-B','-c','import time;time.sleep(60)'],"
        "{'SystemRoot':os.environ['SystemRoot']},log); "
        f"Path({str(record)!r}).write_text(json.dumps({{'pid':p.pid,'job':p.name}})); "
        "os._exit(0)"
    )
    subprocess.run([sys.executable, "-I", "-B", "-c", script], check=True, timeout=20)
    identity = json.loads(record.read_text())
    until = time.monotonic() + 5
    while psutil.pid_exists(identity["pid"]) and time.monotonic() < until:
        time.sleep(0.05)
    assert not psutil.pid_exists(identity["pid"])
    assert not active_job(identity["job"])


FAKE_SERVER = r"""
import argparse,base64,json,os
from http.server import BaseHTTPRequestHandler,HTTPServer
p=argparse.ArgumentParser()
p.add_argument('--port',type=int,default=int(os.environ.get('OLLAMA_HOST','127.0.0.1:11435').rsplit(':',1)[1]))
a,_=p.parse_known_args()
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def reply(self,data,kind='application/json'):
  raw=json.dumps(data).encode() if kind=='application/json' else data
  self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 def do_GET(self):
  if self.path.startswith('/view'):
   return self.reply(b'\x89PNG\r\n\x1a\nprotocol-only'.decode('unicode_escape').encode('latin1'),'image/png')
  if self.path.startswith('/history/'):
   return self.reply({'fixture':{'status':{'completed':True},'outputs':{'save':{'images':[{'filename':'fixture.png','subfolder':'','type':'output'}]}}}})
  self.reply({'version':'protocol-fixture','models':[],'devices':[]})
 def do_POST(self):
  self.rfile.read(int(self.headers.get('Content-Length','0')))
  self.reply({'prompt_id':'fixture','message':{'content':'native protocol fixture'}})
HTTPServer(('127.0.0.1',a.port),Handler).serve_forever()
"""


def port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_native_ollama_protocol_under_owned_job(monkeypatch, tmp_path):
    from pcc_agent.services import OllamaBackend

    # Python "serve" replaces Ollama only in this protocol fixture; no LLM.
    (tmp_path / "serve").write_text(FAKE_SERVER)
    monkeypatch.chdir(tmp_path)
    config = AgentConfig(
        server="https://example.com",
        name="native",
        work_root=tmp_path,
        ollama_executable=sys.executable,
        ollama_models=["fixture"],
        ollama_model_root=tmp_path / "models",
        ollama_port=port(),
    )
    backend = OllamaBackend(
        config, "fixture", {"cpu": 1, "memory_mb": 512, "vram_mb": 0}
    )
    try:
        backend.start()
        assert backend.recovery_record().get("managed_job")
        assert (
            backend.chat([{"role": "user", "content": "test"}])["result"]["content"]
            == "native protocol fixture"
        )
        backend.unload()
    finally:
        backend.stop()
    assert not active_job(backend.controller.job_name)


def test_native_comfyui_protocol_under_owned_job(tmp_path):
    from pcc_agent.comfyui import ComfyUIBackend
    from test_comfyui import config

    cfg = config(tmp_path)
    script = tmp_path / "main.py"
    script.write_text(FAKE_SERVER)
    cfg.comfyui_python, cfg.comfyui_script, cfg.comfyui_port = (
        sys.executable,
        str(script),
        port(),
    )
    backend = ComfyUIBackend(
        cfg, "local-image", {"cpu": 1, "memory_mb": 512, "vram_mb": 0}
    )
    try:
        backend.start()
        assert backend.generate({"prompt": "test", "size": "256x256"}, 4096)[
            "artifacts"
        ]
        backend.unload()
    finally:
        backend.stop()
    assert not active_job(backend.controller.job_name)


@pytest.mark.parametrize("kind", ["ollama", "comfyui"])
def test_opt_in_real_windows_model(kind):
    filename = os.environ.get("PCC_WINDOWS_MODEL_CONFIG")
    if not filename:
        pytest.skip("Set PCC_WINDOWS_MODEL_CONFIG to an approved local model config")
    from pcc_agent.services import OllamaBackend
    from pcc_agent.comfyui import ComfyUIBackend

    config = AgentConfig.load(filename)
    config.work_root.mkdir(parents=True, exist_ok=True)
    resources = {
        "cpu": config.allocatable_cpu,
        "memory_mb": config.allocatable_memory_mb,
        "vram_mb": config.allocatable_vram_mb,
    }
    if kind == "ollama":
        if not config.ollama_models:
            pytest.skip("No local Ollama model configured")
        backend = OllamaBackend(config, config.ollama_models[0], resources)
    else:
        if not config.comfyui_workflows:
            pytest.skip("No local ComfyUI workflow configured")
        backend = ComfyUIBackend(
            config, next(iter(config.comfyui_workflows)), resources
        )
    try:
        backend.start()
        if kind == "ollama":
            assert backend.chat(
                [{"role": "user", "content": "Reply with one word: hello"}]
            )["result"]["content"]
        else:
            assert backend.generate(
                {"prompt": "a cabin beside a lake", "size": "256x256"}, 10 * 2**20
            )["artifacts"]
        backend.unload()
    finally:
        backend.stop()
    assert not active_job(backend.controller.job_name)
