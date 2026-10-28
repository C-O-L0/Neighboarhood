#!/bin/sh
# Back up both databases to backups/<UTC timestamp>/. Run from the repo root or anywhere (Git Bash on Windows, or
# any POSIX shell):
#
#   sh scripts/backup.sh [--stop-web]
#
# The stack must be up (docker compose up -d --wait web). The web app must not be writing during a backup, so the
# script refuses while it runs unless --stop-web is given; it then stops web and starts it again at the end.
#
# Steps: settle (refresh_town_stats() as the owner), take the state fingerprint of both databases (python -m db.state
# in the etl service), pg_dump -Fc and mongodump in the dbtools service, then write manifest.json: UTC time, git commit,
# the db/postgres/*.sql files and their SHA-256, the fingerprint and the SHA-256 of each dump file.
# No password is read or passed here: Compose takes them from .env, and the tools get them from their container.
# A backup holds a full copy of the data (including account hashes): backups/ is git-ignored and not for sharing.
set -eu

MSYS_NO_PATHCONV=1
export MSYS_NO_PATHCONV

cd "$(dirname "$0")/.."

stop_web=0
for arg in "$@"; do
    case "$arg" in
    --stop-web) stop_web=1 ;;
    *)
        echo "usage: sh scripts/backup.sh [--stop-web]" >&2
        exit 2
        ;;
    esac
done

HOST_UID=$(id -u)
HOST_GID=$(id -g)
export HOST_UID HOST_GID

for service in postgres mongodb; do
    if [ -z "$(docker compose ps -q --status running "$service")" ]; then
        echo "backup.sh: $service is not running (docker compose up -d --wait web)" >&2
        exit 1
    fi
done

web_stopped=0
complete=0
dir=""
finish() {
    status=$?
    if [ "$complete" -eq 0 ] && [ -n "$dir" ] && [ -d "$dir" ]; then
        rm -rf "$dir"
        echo "backup.sh: the incomplete backup was removed" >&2
    fi
    if [ "$web_stopped" -eq 1 ]; then
        echo "backup.sh: starting web again"
        docker compose up -d --wait web >/dev/null 2>&1 || {
            echo "backup.sh: web did not start; run: docker compose up -d --wait web" >&2
            [ "$status" -ne 0 ] || status=1
        }
    fi
    exit "$status"
}
trap finish EXIT
trap 'exit 1' INT TERM HUP

if [ -n "$(docker compose ps -q --status running web)" ]; then
    if [ "$stop_web" -ne 1 ]; then
        echo "backup.sh: web is running, so it could write during the backup. Stop it first (docker compose stop web)" >&2
        echo "backup.sh: or run this script with --stop-web, which stops it and starts it again at the end." >&2
        exit 1
    fi
    echo "backup.sh: stopping web"
    docker compose stop web >/dev/null 2>&1
    web_stopped=1
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p backups
dir="backups/$stamp"
mkdir "$dir"
created=$(date -u +%Y-%m-%dT%H:%M:%SZ)

echo "backup.sh: settling and taking the state fingerprint"
fingerprint=$(docker compose run --rm -T etl python -m db.state --settle </dev/null)

echo "backup.sh: dumping both databases"
docker compose run --rm -T dbtools dump "/backups/$stamp" </dev/null

echo "backup.sh: checksums"
dump_sums=$(docker compose run --rm -T dbtools sha256 "/backups/$stamp" </dev/null | tr -d '\r')
sql_sums=$(docker compose run --rm -T dbtools sqlsums </dev/null | tr -d '\r')

commit=$(git rev-parse HEAD 2>/dev/null || echo unknown)
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    dirty=true
else
    dirty=false
fi

# One "name": "hash" line per file, in the layout restore.sh reads back.
entries() {
    awk '{ printf "%s    \"%s\": \"%s\"", sep, $2, $1; sep = ",\n" } END { print "" }'
}

{
    printf '{\n'
    printf '  "created_utc": "%s",\n' "$created"
    printf '  "git_commit": "%s",\n' "$commit"
    printf '  "git_dirty": %s,\n' "$dirty"
    printf '  "postgres_sql": {\n'
    printf '%s\n' "$sql_sums" | entries
    printf '  },\n'
    printf '  "dump_files": {\n'
    printf '%s\n' "$dump_sums" | entries
    printf '  },\n'
    printf '  "fingerprint": %s\n' "$fingerprint"
    printf '}\n'
} >"$dir/manifest.json"

complete=1
echo "backup.sh: done"
echo "backup written: $dir"
