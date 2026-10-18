# MongoDB with a replica-set keyfile baked in.
# A single-node replica set is needed for multi-document transactions, and a
# replica set with authentication needs a keyfile. Generating it at build time
# keeps it out of git and avoids Windows bind-mount permission problems
# (mongod refuses a keyfile readable by anyone but its owner).
FROM mongodb/mongodb-community-server:8.3.11-ubi8-slim

USER root
RUN head -c 756 /dev/urandom | base64 > /etc/mongo-keyfile \
    && chown mongod:mongod /etc/mongo-keyfile \
    && chmod 400 /etc/mongo-keyfile
USER mongod
