#!/bin/sh
# Restore both databases from a backup made by scripts/backup.sh. Run from the repo root or anywhere (Git Bash on
# Windows, or any POSIX shell):
#
#   sh scripts/restore.sh backups/<timestamp> [--yes]
#
# It replaces ALL data in both databases. Before it touches anything it checks that every dump file matches its
# SHA-256 in manifest.json, and that the db/postgres/*.sql files in the working tree are the ones the backup was
# taken with (a different set means a different schema version, so the restore is refused). Then it asks for
# confirmation (unless --yes), stops web, runs the migrate step (roles and users exist), runs pg_restore --clean
# --if-exists --single-transaction and ANALYZE as the superuser, runs mongorestore --drop, settles, compares the
# state fingerprint with the manifest and only then starts web. Any difference fails loudly and leaves web stopped.
# No password is read or passed here: Compose takes them from .env, and the tools get them from their container.
set -eu

MSYS_NO_PATHCONV=1
export MSYS_NO_PATHCONV

cd "$(dirname "$0")/.."

usage="usage: sh scripts/restore.sh backups/<timestamp> [--yes]"
folder=""
assume_yes=0
for arg in "$@"; do
    case "$arg" in
    --yes) assume_yes=1 ;;
    -*)
        echo "$usage" >&2
        exit 2
        ;;
    *)
        if [ -n "$folder" ]; then
            echo "$usage" >&2
            exit 2
        fi
        folder=$arg
        ;;
    esac
done
if [ -z "$folder" ]; then
    echo "$usage" >&2
    exit 2
fi

fail() {
    echo "restore.sh: $*" >&2
    exit 1
}

# The folder must be a direct child of ./backups, which is what the dbtools service mounts.
folder=${folder%/}
[ -d "$folder" ] || fail "$folder is not a folder"
name=$(basename "$folder")
here=$(cd "$folder" && pwd)
[ "$here" = "$(cd backups 2>/dev/null && pwd)/$name" ] || fail "$folder is not a folder directly under backups/"
manifest="$folder/manifest.json"
[ -f "$manifest" ] || fail "$manifest not found"
for f in postgres.dump mongo.archive.gz; do
    [ -f "$folder/$f" ] || fail "$folder/$f not found"
done

HOST_UID=$(id -u)
HOST_GID=$(id -g)
export HOST_UID HOST_GID

for service in postgres mongodb; do
    if [ -z "$(docker compose ps -q --status running "$service")" ]; then
        fail "$service is not running (docker compose up -d --wait postgres mongodb)"
    fi
done

scratch=$(mktemp -d "${TMPDIR:-/tmp}/neighboar-restore.XXXXXX")
cleanup() {
    rm -rf "$scratch"
}
trap cleanup EXIT
trap 'exit 1' INT TERM HUP

# ---- checks that change nothing ----
# The manifest has one "name": "hash" per line (scripts/backup.sh). This reads a block back in the form the
# container prints: "<hash>  <name>", sorted.
block() {
    awk -v key="$1" '
        $0 == "  \"" key "\": {" { inside = 1; next }
        inside && $0 ~ /^  }/ { inside = 0 }
        inside {
            line = $0
            sub(/^ *"/, "", line)
            name = line
            sub(/".*$/, "", name)
            hash = line
            sub(/^[^"]*": "/, "", hash)
            sub(/".*$/, "", hash)
            print hash "  " name
        }' "$manifest" | sort
}

echo "restore.sh: checking the dump files against the manifest"
block dump_files >"$scratch/want_dumps"
[ -s "$scratch/want_dumps" ] || fail "the manifest lists no dump files"
docker compose run --rm -T dbtools sha256 "/backups/$name" 2>/dev/null </dev/null | tr -d '\r' | sort >"$scratch/have_dumps" ||
    fail "could not read the dump files"
if ! cmp -s "$scratch/want_dumps" "$scratch/have_dumps"; then
    echo "restore.sh: a dump file does not match its checksum in the manifest:" >&2
    comm -23 "$scratch/want_dumps" "$scratch/have_dumps" | cut -d' ' -f3 | sed 's/^/  /' >&2
    echo "restore.sh: backup $name is damaged or was edited. Nothing was changed." >&2
    exit 1
fi

echo "restore.sh: checking the schema files against the manifest"
block postgres_sql >"$scratch/want_sql"
[ -s "$scratch/want_sql" ] || fail "the manifest lists no db/postgres/*.sql files"
docker compose run --rm -T dbtools sqlsums </dev/null | tr -d '\r' | sort >"$scratch/have_sql"
if ! cmp -s "$scratch/want_sql" "$scratch/have_sql"; then
    echo "restore.sh: the db/postgres/*.sql files differ from the ones this backup was taken with (schema version mismatch):" >&2
    comm -23 "$scratch/want_sql" "$scratch/have_sql" | cut -d' ' -f3 | sed 's/^/  missing or changed in the working tree: /' >&2
    comm -13 "$scratch/want_sql" "$scratch/have_sql" | cut -d' ' -f3 | sed 's/^/  new or changed in the working tree: /' >&2
    echo "restore.sh: nothing was changed" >&2
    exit 1
fi

created=$(sed -n 's/^  "created_utc": "\(.*\)",$/\1/p' "$manifest")
commit=$(sed -n 's/^  "git_commit": "\(.*\)",$/\1/p' "$manifest")
echo "restore.sh: backup $name, taken $created at commit $commit; checksums and schema files match"

if [ "$assume_yes" -ne 1 ]; then
    echo "This replaces ALL data in both databases with the backup above."
    printf 'Type yes to continue: '
    answer=""
    read -r answer || true
    if [ "$answer" != "yes" ]; then
        echo "restore.sh: cancelled. Nothing was changed." >&2
        exit 1
    fi
fi

# ---- from here the databases change ----
on_exit() {
    status=$?
    rm -rf "$scratch"
    if [ "$status" -ne 0 ]; then
        echo "restore.sh: FAILED. web was left stopped; the databases may be partly restored." >&2
        echo "restore.sh: fix the cause, then run this script again with the same backup." >&2
    fi
    exit "$status"
}
trap on_exit EXIT

echo "restore.sh: stopping web"
docker compose stop web >/dev/null 2>&1

echo "restore.sh: running migrate (roles and users)"
docker compose run --rm -T migrate </dev/null

echo "restore.sh: restoring PostgreSQL and MongoDB"
docker compose run --rm -T dbtools restore "/backups/$name" </dev/null

echo "restore.sh: settling and comparing the state fingerprint with the manifest"
docker compose run --rm -T etl python -m db.state --settle --compare - <"$manifest"

echo "restore.sh: starting web"
docker compose up -d --wait web >/dev/null 2>&1
echo "restore.sh: done, the databases match $name"
