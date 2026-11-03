#!/usr/bin/env sh
# Author: Fubuki. Execute from the project checkout on the deployment host.
set -eu
umask 077
backup_dir=${1:-tmp/backups/$(date +%Y%m%d-%H%M%S)}
mkdir -p "$(dirname "$backup_dir")"
mkdir "$backup_dir" # refuse to overwrite an earlier snapshot
touch "$backup_dir/.incomplete"
was_running=$(docker compose ps -q --status running server)
resume_server() { if [ -n "$was_running" ]; then docker compose start server; fi; }
trap resume_server EXIT
trap 'exit 130' INT
trap 'exit 143' HUP TERM
docker compose stop server
docker compose exec -T db pg_dump -U pcc -d pcc --no-owner --no-acl -Fc > "$backup_dir/pcc.dump"
docker compose run --rm --no-deps -T --entrypoint python server -m pcc_server.backup pack /data/artifacts > "$backup_dir/artifacts.tar.gz"
dump_hash=$(sha256sum "$backup_dir/pcc.dump" | cut -d ' ' -f 1)
artifact_hash=$(sha256sum "$backup_dir/artifacts.tar.gz" | cut -d ' ' -f 1)
printf '%s  pcc.dump\n%s  artifacts.tar.gz\n' "$dump_hash" "$artifact_hash" > "$backup_dir/SHA256SUMS"
printf '{"version":1,"author":"Fubuki","files":{"pcc.dump":"%s","artifacts.tar.gz":"%s"}}\n' "$dump_hash" "$artifact_hash" > "$backup_dir/manifest.json"
rm "$backup_dir/.incomplete"
printf '%s\n' 'Backup complete. Encrypt .env separately; preserve PCC_SECRET when restoring.'
