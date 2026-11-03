"""Closed, audited templates; no eval, imports or commands from input. Fubuki."""

import base64
import json
import math
import statistics
import struct
import sys
import zlib


def run(task):
    kind = task["worker"]
    data = task["input"]
    if kind == "compute.stats":
        if set(data) != {"values"} or not 1 <= len(data["values"]) <= 10000:
            raise ValueError("Invalid input")
        values = data["values"]
        if any(
            type(v) not in {int, float} or not math.isfinite(v) or abs(v) > 1e12
            for v in values
        ):
            raise ValueError("Invalid number")
        result = {
            "count": len(values),
            "sum": math.fsum(values),
            "mean": statistics.mean(values),
            "min": min(values),
            "max": max(values),
        }
        artifact = json.dumps(result, allow_nan=False).encode()
        return {
            "result": result,
            "artifacts": [
                {
                    "name": "statistics.json",
                    "data_base64": base64.b64encode(artifact).decode(),
                }
            ],
        }
    if kind == "synthetic.chat":
        text = next(
            (m["content"] for m in reversed(data["messages"]) if m["role"] == "user"),
            "",
        )
        return {
            "result": {"content": "[协议测试 Worker，非真实模型] " + text[:4000]},
            "artifacts": [],
        }
    if kind == "synthetic.image":
        width = int(data["size"].split("x")[0])
        if width not in {256, 512}:
            raise ValueError("Invalid size")
        seed = zlib.crc32(data["prompt"].encode())
        raw = b"".join(
            b"\x00"
            + bytes(
                v
                for x in range(width)
                for v in ((x + seed) % 256, (y + seed // 256) % 256, 160)
            )
            for y in range(width)
        )

        def chunk(tag, content):
            return (
                struct.pack(">I", len(content))
                + tag
                + content
                + struct.pack(">I", zlib.crc32(tag + content))
            )

        png = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, width, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b"")
        )
        return {
            "result": {"synthetic": True, "size": data["size"]},
            "artifacts": [
                {"name": "synthetic.png", "data_base64": base64.b64encode(png).decode()}
            ],
        }
    raise ValueError("Worker is not approved")


if __name__ == "__main__":
    payload = sys.stdin.buffer.read(100001)
    if len(payload) > 100000:
        raise ValueError("Input too large")
    print(json.dumps(run(json.loads(payload)), allow_nan=False))
