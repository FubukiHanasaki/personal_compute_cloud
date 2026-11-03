"""Apply a Torch allocator budget before approved ComfyUI startup. Fubuki."""

import os
import runpy
import sys


def gpu_budget(vram_mb):
    if not os.environ.get("CUDA_VISIBLE_DEVICES"):
        return
    import torch

    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise RuntimeError("Exactly one approved CUDA device is required")
    total = torch.cuda.get_device_properties(0).total_memory
    # Leave room for the CUDA context and allocations outside the Torch pool.
    usable = (int(vram_mb) - 256) * 2**20
    if not 0 < usable <= total:
        raise RuntimeError("Invalid GPU allocator budget")
    torch.cuda.set_per_process_memory_fraction(usable / total, device=0)


if __name__ == "__main__":
    gpu_budget(int(sys.argv[1]))
    script = sys.argv[2]
    sys.argv = sys.argv[2:]
    sys.path.insert(0, os.path.dirname(script))
    runpy.run_path(script, run_name="__main__")
