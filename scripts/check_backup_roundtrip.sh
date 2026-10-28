#!/bin/sh
# Round-trip check for scripts/backup.sh and scripts/restore.sh (task 4.3). Run from the repo root or anywhere,
# with the stack up and a snapshot loaded (the sample is enough and quicker):
#
#   sh scripts/check_backup_roundtrip.sh [--keep]
#
#   1. take the state fingerprint, then back up
#   2. change both databases (db.roundtrip_mutate: delete a saved block, add an admin sale, alter a profile's stats)
#      and check that the fingerprint really changed
#   3. restore, then check that the fingerprint equals the one from step 1
#   4. check that a restore is refused, with the databases untouched, when one byte of a dump file is changed,
#      and when the manifest's db/postgres/*.sql set differs from the working tree
#
# It removes every backup folder it created (and only those) when it ends; with --keep it leaves them and prints
# where. Exit 0 only if every check passed. No password is read or printed. The full test suite is run separately.
set -eu

MSYS_NO_PATHCONV=1
export MSYS_NO_PATHCONV

cd "$(dirname "$0")/.."

keep=0
for arg in "$@"; do
    case "$arg" in
    --keep) keep=1 ;;
    *)
        echo "usage: sh scripts/check_backup_roundtrip.sh [--keep]" >&2
        exit 2
        ;;
    esac
done

scratch=$(mktemp -d "${TMPDIR:-/tmp}/neighboar-roundtrip.XXXXXX")
created=""
cleanup() {
    status=$?
    rm -rf "$scratch"
    if [ -n "$created" ]; then
        if [ "$keep" -eq 1 ]; then
            echo "check: --keep, so these backup folders were left in place: $created"
        else
            for d in $created; do
                rm -rf "$d"
            done
            echo "check: removed the backup folders this check created: $created"
        fi
    fi
    if [ "$status" -ne 0 ]; then
        echo "check: FAILED" >&2
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 1' INT TERM HUP

HOST_UID=$(id -u)
HOST_GID=$(id -g)
export HOST_UID HOST_GID

fail() {
    echo "check: FAIL: $*" >&2
    exit 1
}

fingerprint() {
    docker compose run --rm -T etl python -m db.state --settle </dev/null >"$1"
}

echo "check: 1. fingerprint and backup"
fingerprint "$scratch/before.json"
sh scripts/backup.sh --stop-web | tee "$scratch/backup.out"
backup=$(sed -n 's/^backup written: //p' "$scratch/backup.out")
[ -n "$backup" ] && [ -f "$backup/manifest.json" ] || fail "backup.sh did not report a backup folder"
created="$backup"

echo "check: 2. change both databases"
docker compose run --rm -T etl python -m db.roundtrip_mutate </dev/null
fingerprint "$scratch/mutated.json"
if cmp -s "$scratch/before.json" "$scratch/mutated.json"; then
    fail "the changes did not alter the fingerprint, so the check would prove nothing"
fi
echo "check: the fingerprint changed"

echo "check: 3. restore"
sh scripts/restore.sh "$backup" --yes
fingerprint "$scratch/after.json"
cmp -s "$scratch/before.json" "$scratch/after.json" || fail "the fingerprint after the restore differs from the one before the backup"
echo "check: the fingerprint after the restore equals the one before the backup"

echo "check: 4a. one changed byte in a dump file"
name=$(basename "$backup")
corrupt="backups/$name-corrupt"
created="$created $corrupt"
mkdir "$corrupt"
cp "$backup/manifest.json" "$backup/postgres.dump" "$backup/mongo.archive.gz" "$corrupt/"
old=$(od -An -tu1 -j100 -N1 "$corrupt/postgres.dump" | tr -d ' ')
new=$(((old + 1) % 256))
printf "$(printf '\%03o' "$new")" | dd of="$corrupt/postgres.dump" bs=1 seek=100 conv=notrunc 2>/dev/null
if sh scripts/restore.sh "$corrupt" --yes >"$scratch/corrupt.out" 2>&1; then
    fail "restore.sh accepted a dump file with a changed byte"
fi
grep -q "does not match its checksum" "$scratch/corrupt.out" || fail "restore.sh did not refuse for the checksum"
grep 'restore.sh:' "$scratch/corrupt.out" | sed 's/^/    /'
fingerprint "$scratch/after_corrupt.json"
cmp -s "$scratch/before.json" "$scratch/after_corrupt.json" || fail "the refused restore changed a database"

echo "check: 4b. a db/postgres/*.sql set that differs from the working tree"
other="backups/$name-schema"
created="$created $other"
mkdir "$other"
cp "$backup/manifest.json" "$backup/postgres.dump" "$backup/mongo.archive.gz" "$other/"
# Replace the first character of the recorded hash of 001_schema.sql; the dump files stay valid.
sed '/"db\/postgres\/001_schema.sql"/s/\(: "\)[0-9a-f]/\1z/' "$backup/manifest.json" >"$other/manifest.json"
cmp -s "$backup/manifest.json" "$other/manifest.json" && fail "the manifest edit changed nothing"
if sh scripts/restore.sh "$other" --yes >"$scratch/schema.out" 2>&1; then
    fail "restore.sh accepted a backup whose db/postgres/*.sql set differs"
fi
grep -q "schema version mismatch" "$scratch/schema.out" || fail "restore.sh did not refuse for the schema files"
grep 'restore.sh:' "$scratch/schema.out" | sed 's/^/    /'
fingerprint "$scratch/after_schema.json"
cmp -s "$scratch/before.json" "$scratch/after_schema.json" || fail "the refused restore changed a database"

echo "check: all checks passed"
