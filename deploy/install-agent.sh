#!/usr/bin/env sh
# Author: FubukiHanasaki. Run as the restricted runtime user, from the source checkout.
set -eu
if [ "$(id -u)" = 0 ]; then echo 'Use a dedicated non-root account.' >&2; exit 1; fi
mkdir -p tmp
python3 -m venv tmp/agent-venv
TMPDIR="$PWD/tmp" tmp/agent-venv/bin/pip install --cache-dir tmp/pip-cache .
tmp/agent-venv/bin/pcc-agent init --config agent.toml
printf '%s\n' 'Edit agent.toml, register, then run. Optional systemd unit: deploy/pcc-agent.service'
