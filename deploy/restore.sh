#!/usr/bin/env sh
# Author: Fubuki. Restore only into a new deployment with empty DB/storage.
set -eu
umask 077
backup_dir=${1:?usage: sh deploy/restore.sh BACKUP_DIRECTORY}
test ! -e "$backup_dir/.incomplete" || { echo 'Incomplete backup refused' >&2; exit 1; }
test "$(wc -l < "$backup_dir/SHA256SUMS")" -eq 2
test "$(sed -n 's/^[0-9a-f]\{64\}  \(pcc.dump\|artifacts.tar.gz\)$/\1/p' "$backup_dir/SHA256SUMS" | sort)" = "$(printf 'artifacts.tar.gz\npcc.dump')"
(cd "$backup_dir" && sha256sum -c SHA256SUMS)
docker compose stop server
docker compose up -d --wait db
# Validate paths/links/size and empty storage before changing the database.
docker compose run --rm --no-deps -T --entrypoint sh server -ec 'python -m pcc_server.backup verify-stream; python -c "from pathlib import Path; p=Path(\"/data/artifacts\"); assert not p.exists() or not any(p.iterdir()), \"Artifact target must be empty\""' < "$backup_dir/artifacts.tar.gz"
# No --clean: existing tables cause failure and the transaction rolls back.
docker compose exec -T db pg_restore -U pcc -d pcc --single-transaction --exit-on-error --no-owner --no-acl < "$backup_dir/pcc.dump"
docker compose run --rm --no-deps -T --entrypoint python server -m pcc_server.backup unpack-stream /data/artifacts < "$backup_dir/artifacts.tar.gz"
docker compose up -d --wait
printf '%s\n' 'Restore complete. Verify login, artifact checksums and Agent reconciliation before reopening access.'
