"""Explicit real-GPU lifecycle check; never part of ordinary CI. Author: Fubuki."""

import argparse
import json
from pathlib import Path
import time
from pcc_agent.config import AgentConfig
from pcc_agent.services import ServiceManager


def main():
    parser = argparse.ArgumentParser(description="PCC real GPU acceptance · Fubuki")
    parser.add_argument("--config", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", default="tmp/gpu-evidence.json")
    args = parser.parse_args()
    config = AgentConfig.load(args.config)
    if not config.ollama_gpu_device:
        raise SystemExit(
            "Select a local GPU UUID explicitly; CPU fallback is not GPU acceptance"
        )
    config.work_root.mkdir(parents=True, exist_ok=True)
    manager = ServiceManager(config)
    manager.sync(
        [
            {
                "definition_id": "gpu-acceptance",
                "desired_state": "auto",
                "idle_seconds": 1,
                "config": {
                    "worker": "ollama:" + args.model,
                    "model": args.model,
                    "resources": {
                        "cpu": config.allocatable_cpu,
                        "memory_mb": config.allocatable_memory_mb,
                        "vram_mb": config.ollama_vram_mb,
                    },
                },
            }
        ]
    )
    try:
        service = manager.acquire("gpu-acceptance")
        manager.ready(service)
        backend = service.backend
        native = backend.client.get("/api/ps").json()
        gpu_bytes = sum(
            int(model.get("size_vram", 0)) for model in native.get("models", [])
        )
        if gpu_bytes <= 0:
            raise AssertionError("Model is not using GPU memory")
        result = backend.chat(
            [{"role": "user", "content": "What is two plus two? Answer briefly."}]
        )
        assert result["result"]["content"]
        manager.release(service)
        time.sleep(1.1)
        manager.tick()
        assert service.state == "stopped" and backend.process.poll() is not None
        evidence = {
            "author": "Fubuki",
            "model": args.model,
            "size_vram_bytes": gpu_bytes,
            "content": result["result"]["content"],
            "idle_unloaded_and_process_stopped": True,
            "synthetic": False,
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(evidence, ensure_ascii=False))
    finally:
        manager.stop_all()


if __name__ == "__main__":
    main()
