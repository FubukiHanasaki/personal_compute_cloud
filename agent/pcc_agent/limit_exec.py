"""Apply OS limits before exec; only invoked by the local Agent. FubukiHanasaki."""

import math
import os
import resource
import sys

cpu, memory_mb, seconds, output_bytes = map(float, sys.argv[1:5])
if cpu < 1 or memory_mb < 128:
    raise SystemExit("Insufficient enforceable resources")
allowed = sorted(os.sched_getaffinity(0))
os.sched_setaffinity(0, allowed[: max(1, math.floor(cpu))])
resource.setrlimit(resource.RLIMIT_AS, (int(memory_mb) * 2**20, int(memory_mb) * 2**20))
resource.setrlimit(
    resource.RLIMIT_CPU, (math.ceil(seconds * cpu), math.ceil(seconds * cpu) + 1)
)
resource.setrlimit(resource.RLIMIT_FSIZE, (int(output_bytes), int(output_bytes)))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
os.execv(sys.argv[5], sys.argv[5:])
