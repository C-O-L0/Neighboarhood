#!/bin/sh
# Runs inside the dbtools container (see scripts/backup.sh and scripts/restore.sh, which call it).
#
#   dbtool dump DIR       pg_dump -Fc and mongodump into DIR (a path under /backups)
#   dbtool restore DIR    pg_restore --clean --if-exists --single-transaction, ANALYZE, then mongorestore --drop
#   dbtool sha256 DIR     "<sha256>  <name>" for postgres.dump and mongo.archive.gz in DIR
#   dbtool sqlsums        "<sha256>  db/postgres/<name>" for every *.sql in /work/postgres (carriage returns ignored,
#                         as db/migrate.py ignores them)
#   dbtool versions       the versions of the four tools
#
# Passwords come from the container environment only. PostgreSQL reads PGPASSWORD. The MongoDB tools get the password
# through a --config file (mode 600) that is removed on any exit; the user name, host and database are not secret.
set -eu

MONGO_HOST=${MONGO_HOST:-mongodb}
PGHOST=${PGHOST:-postgres}
export PGHOST
PGPASSWORD=${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is not set}
export PGPASSWORD
PGUSER=${POSTGRES_USER:?POSTGRES_USER is not set}
export PGUSER

cfg=""
cleanup() {
    if [ -n "$cfg" ]; then
        rm -f "$cfg"
    fi
}
trap cleanup EXIT
trap 'exit 1' INT TERM HUP

mongo_config() {
    : "${MONGODB_INITDB_ROOT_USERNAME:?MONGODB_INITDB_ROOT_USERNAME is not set}"
    : "${MONGODB_INITDB_ROOT_PASSWORD:?MONGODB_INITDB_ROOT_PASSWORD is not set}"
    umask 077
    cfg=$(mktemp /tmp/mongo-tools.XXXXXX)
    chmod 600 "$cfg"
    # YAML single-quoted scalar: a quote is doubled. printf is a shell builtin, so the password is on no command line.
    escaped=$(printf '%s' "$MONGODB_INITDB_ROOT_PASSWORD" | sed "s/'/''/g")
    printf "password: '%s'\n" "$escaped" >"$cfg"
}

need_dir() {
    [ -n "${1:-}" ] && [ -d "$1" ] || {
        echo "dbtool: backup folder '${1:-}' not found" >&2
        exit 2
    }
}

cmd=${1:-}
[ $# -gt 0 ] && shift

case "$cmd" in
dump)
    need_dir "${1:-}"
    dir=$1
    : "${POSTGRES_DB:?POSTGRES_DB is not set}" "${MONGO_DB:?MONGO_DB is not set}"
    mongo_config
    echo "dbtool: pg_dump $POSTGRES_DB"
    pg_dump --format=custom --dbname="$POSTGRES_DB" --file="$dir/postgres.dump"
    echo "dbtool: mongodump $MONGO_DB"
    mongodump --config="$cfg" --host="$MONGO_HOST" --username="$MONGODB_INITDB_ROOT_USERNAME" \
        --authenticationDatabase=admin --db="$MONGO_DB" --archive="$dir/mongo.archive.gz" --gzip --quiet
    ;;
restore)
    need_dir "${1:-}"
    dir=$1
    : "${POSTGRES_DB:?POSTGRES_DB is not set}" "${MONGO_DB:?MONGO_DB is not set}"
    mongo_config
    echo "dbtool: pg_restore $POSTGRES_DB"
    pg_restore --clean --if-exists --single-transaction --exit-on-error --dbname="$POSTGRES_DB" "$dir/postgres.dump"
    psql --no-psqlrc --quiet --dbname="$POSTGRES_DB" --command="ANALYZE"
    echo "dbtool: mongorestore $MONGO_DB"
    mongorestore --config="$cfg" --host="$MONGO_HOST" --username="$MONGODB_INITDB_ROOT_USERNAME" \
        --authenticationDatabase=admin --archive="$dir/mongo.archive.gz" --gzip --drop \
        --nsInclude="$MONGO_DB.*" --quiet
    ;;
sha256)
    need_dir "${1:-}"
    cd "$1"
    sha256sum postgres.dump mongo.archive.gz
    ;;
sqlsums)
    for f in /work/postgres/*.sql; do
        [ -e "$f" ] || continue
        printf '%s  db/postgres/%s\n' "$(tr -d '\r' <"$f" | sha256sum | cut -d' ' -f1)" "$(basename "$f")"
    done
    ;;
versions)
    pg_dump --version
    pg_restore --version
    mongodump --version | head -n 1
    mongorestore --version | head -n 1
    ;;
*)
    echo "usage: dbtool dump DIR | restore DIR | sha256 DIR | sqlsums | versions" >&2
    exit 2
    ;;
esac
