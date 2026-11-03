#!/usr/bin/env sh
# Author: FubukiHanasaki. Bootstrap a non-superuser application role, only on a new volume.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=app_password="$PCC_APP_DB_PASSWORD" <<'SQL'
CREATE ROLE pcc LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER DATABASE pcc OWNER TO pcc;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO pcc;
SQL
