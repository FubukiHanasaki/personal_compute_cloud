#!/usr/bin/env sh
# Author: FubukiHanasaki. Source/GUI maintenance only; native execution is not supported.
set -eu
pcc_root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
exec python3 "$pcc_root/scripts/update.py" "$@"
