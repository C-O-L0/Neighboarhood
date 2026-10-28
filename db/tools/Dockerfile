# Backup and restore tools: pg_dump / pg_restore / psql 18.6 from the PostgreSQL image, plus the MongoDB Database
# Tools (mongodump, mongorestore) at one pinned version. Not started by "up"; see scripts/backup.sh and scripts/restore.sh.
#
# The archive is fetched from fastdl.mongodb.org and its SHA-256 is checked before it is unpacked; a mismatch fails
# the build. MongoDB publishes a Debian 12 build for x86_64 only, so arm64 uses the Ubuntu 22.04 arm64 build (Go
# binaries, glibc 2.35 or older, which Debian 12's glibc 2.36 runs).
ARG PG_IMAGE=postgres:18.6-bookworm

FROM ${PG_IMAGE} AS fetch
ARG TARGETARCH
ARG TOOLS_VERSION=100.20.0
ARG TOOLS_SHA256_AMD64=858d7bda37aa1ebb4c945887f639b28fc9910874693968d16979452ab807c55c
ARG TOOLS_SHA256_ARM64=cb3fc5ec3cba9513de078fa65a02b79eb670a03e28541287cf0fe9e705ea7661
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN set -eu; \
    case "${TARGETARCH}" in \
        amd64) platform=debian12-x86_64; sha256="${TOOLS_SHA256_AMD64}" ;; \
        arm64) platform=ubuntu2204-arm64; sha256="${TOOLS_SHA256_ARM64}" ;; \
        *) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    archive="mongodb-database-tools-${platform}-${TOOLS_VERSION}.tgz"; \
    curl --fail --silent --show-error --location --output "/tmp/${archive}" \
        "https://fastdl.mongodb.org/tools/db/${archive}"; \
    echo "${sha256}  /tmp/${archive}" | sha256sum --check -; \
    mkdir /opt/mongo-tools; \
    tar -xzf "/tmp/${archive}" -C /opt/mongo-tools --strip-components=2 --wildcards '*/bin/mongodump' '*/bin/mongorestore'

FROM ${PG_IMAGE}
COPY --from=fetch /opt/mongo-tools/mongodump /opt/mongo-tools/mongorestore /usr/local/bin/
COPY dbtool.sh /usr/local/bin/dbtool
RUN chmod 755 /usr/local/bin/dbtool
WORKDIR /backups
USER postgres
ENTRYPOINT ["/usr/local/bin/dbtool"]
