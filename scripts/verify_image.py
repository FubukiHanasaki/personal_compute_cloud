"""Explicit real-model image/GPU lifecycle verification. Author: Fubuki."""

import argparse
import base64
import hashlib
import json
from pathlib import Path
import time
from pcc_agent.config import AgentConfig
from pcc_agent.services import ServiceManager


def main():
    parser = argparse.ArgumentParser(description="PCC real image acceptance · Fubuki")
    parser.add_argument("--config", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", default="tmp/image-evidence.json")
    parser.add_argument("--image", default="tmp/image-evidence.png")
    args = parser.parse_args()
    config = AgentConfig.load(args.config)
    if not config.comfyui_gpu_device:
        raise SystemExit(
            "Explicit GPU selection required; CPU fallback is not GPU evidence"
        )
    config.work_root.mkdir(parents=True, exist_ok=True)
    manager = ServiceManager(config)
    manager.sync(
        [
            {
                "definition_id": "image-acceptance",
                "desired_state": "auto",
                "idle_seconds": 1,
                "config": {
                    "worker": "comfyui:" + args.model,
                    "model": args.model,
                    "resources": {
                        "cpu": config.allocatable_cpu,
                        "memory_mb": config.allocatable_memory_mb,
                        "vram_mb": config.allocatable_vram_mb,
                    },
                },
            }
        ]
    )
    try:
        service = manager.acquire("image-acceptance")
        manager.ready(service)
        backend = service.backend
        result = backend.generate(
            {
                "prompt": "A small cabin by a lake, daylight, watercolor",
                "size": "256x256",
            },
            10 * 2**20,
        )
        stats = backend.client.get("/system_stats").json()
        gpu_bytes = sum(
            int(device.get("torch_vram_total", 0))
            for device in stats.get("devices", [])
            if device.get("type") == "cuda"
        )
        assert gpu_bytes > 0, "Real CUDA allocation required"
        png = base64.b64decode(result["artifacts"][0]["data_base64"], validate=True)
        assert (
            png[:8] == b"\x89PNG\r\n\x1a\n" and int.from_bytes(png[16:20], "big") == 256
        )
        image = Path(args.image)
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(png)
        manager.release(service)
        time.sleep(1.1)
        manager.tick()
        assert service.state == "stopped" and backend.process.poll() is not None
        evidence = {
            "author": "Fubuki",
            "model": args.model,
            "synthetic": False,
            "size": "256x256",
            "png_bytes": len(png),
            "png_sha256": hashlib.sha256(png).hexdigest(),
            "torch_vram_bytes": gpu_bytes,
            "idle_unloaded_and_process_stopped": True,
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps(evidence))
    finally:
        manager.stop_all()


if __name__ == "__main__":
    main()
