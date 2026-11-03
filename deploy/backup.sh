#!/usr/bin/env sh
# Author: Fubuki. Execute from the project checkout on the deployment host.
set -eu
umask 077
backup_dir=${1:-tmp/backups}
mkdir -p "$backup_dir"
docker compose stop server
trap 'docker compose start server' EXIT HUP INT TERM
docker compose exec -T db pg_dump -U pcc -d pcc -Fc > "$backup_dir/pcc.dump"
docker compose run --rm --no-deps -T --entrypoint tar server -C /data -czf - artifacts > "$backup_dir/artifacts.tar.gz"
sha256sum "$backup_dir/pcc.dump" "$backup_dir/artifacts.tar.gz" > "$backup_dir/SHA256SUMS"
printf '%s\n' 'Backup complete. Store .env separately using encrypted backup; PCC_SECRET is required on restore.'
