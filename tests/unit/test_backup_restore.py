"""Backup and restore tooling that can be checked from the repository's own files (task 4.3).

The tools image, the dbtools service, the scripts' safety settings, the ignore files, and the pure part of the state
fingerprint. The round trip itself needs the running stack and Docker on the host: sh scripts/check_backup_roundtrip.sh.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from db.state import differences

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ["scripts/backup.sh", "scripts/restore.sh", "scripts/check_backup_roundtrip.sh", "db/tools/dbtool.sh"]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def dbtools() -> dict:
    return yaml.safe_load(read("compose.yaml"))["services"]["dbtools"]


# ------------------------------------------------------------------------------------------------ tools image


def test_the_tools_image_is_built_on_the_pinned_postgres_image():
    assert re.search(r"^ARG PG_IMAGE=postgres:18\.6-bookworm$", read("db/tools/Dockerfile"), flags=re.M)


def test_each_architecture_has_a_pinned_sha256_that_is_checked_during_the_build():
    text = read("db/tools/Dockerfile")
    for arch in ("AMD64", "ARM64"):
        assert re.search(rf"^ARG TOOLS_SHA256_{arch}=[0-9a-f]{{64}}$", text, flags=re.M), arch
    assert re.search(r"^ARG TOOLS_VERSION=\d+\.\d+\.\d+$", text, flags=re.M)
    assert 'sha256sum --check -' in text
    assert "fastdl.mongodb.org/tools/db/" in text


# --------------------------------------------------------------------------------------------- compose service


def test_dbtools_is_a_tools_profile_service_run_as_the_host_user():
    svc = dbtools()
    assert svc["profiles"] == ["tools"]
    assert svc["build"] == "./db/tools"
    assert svc["user"] == "${HOST_UID:-1000}:${HOST_GID:-1000}"
    assert not svc.get("ports")
    assert "./backups:/backups" in svc["volumes"]


def test_dbtools_holds_only_the_superuser_and_root_credentials():
    assert set(dbtools()["environment"]) == {
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "MONGODB_INITDB_ROOT_USERNAME",
        "MONGODB_INITDB_ROOT_PASSWORD",
        "MONGO_DB",
    }


@pytest.mark.parametrize("name", [".gitignore", ".dockerignore"])
def test_backups_are_ignored(name):
    assert "backups/" in [line.strip() for line in read(name).splitlines()], name


# ------------------------------------------------------------------------------------------------- the scripts


@pytest.mark.parametrize("script", SCRIPTS)
def test_scripts_use_lf_line_endings_and_stop_on_errors(script):
    raw = (ROOT / script).read_bytes()
    assert b"\r" not in raw, f"{script} has carriage returns"
    text = raw.decode("utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert re.search(r"^set -eu$", text, flags=re.M)
    assert re.search(r"^trap ", text, flags=re.M)


@pytest.mark.parametrize("script", SCRIPTS[:3])
def test_host_scripts_set_msys_no_pathconv_and_pass_no_secret(script):
    text = read(script)
    assert re.search(r"^MSYS_NO_PATHCONV=1$", text, flags=re.M)
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"PASSWORD|\.env\b|--password|--uri", code), f"{script} handles a secret"


def test_the_container_script_keeps_passwords_off_command_lines():
    text = read("db/tools/dbtool.sh")
    assert "--password" not in text and "--uri" not in text
    assert text.count("--config=") >= 2  # mongodump and mongorestore
    assert "chmod 600" in text and "rm -f" in text
    assert 'PGPASSWORD=${POSTGRES_PASSWORD' in text
    # the YAML file is written by the printf builtin; the only other command that sees the password reads it on stdin
    assert """printf '%s' "$MONGODB_INITDB_ROOT_PASSWORD" | sed""" in text


@pytest.mark.parametrize("script", ["scripts/backup.sh", "scripts/restore.sh"])
def test_a_script_with_bad_arguments_exits_2_before_touching_docker(script):
    out = subprocess.run(["sh", str(ROOT / script), "--nonsense"], capture_output=True, text=True, timeout=30)
    assert out.returncode == 2
    assert "usage" in out.stderr


def test_restore_checks_before_it_changes_anything():
    text = read("scripts/restore.sh")
    stop = text.index("docker compose stop web")
    for needle in ("dbtools sha256", "dbtools sqlsums", "Type yes to continue"):
        assert text.index(needle) < stop, needle
    for needle in ("run --rm -T migrate", "dbtools restore", "db.state --settle --compare", "up -d --wait web"):
        assert text.index(needle) > stop, needle
    assert text.index("db.state --settle --compare") < text.index("up -d --wait web")


def test_backup_refuses_while_web_runs_unless_told_to_stop_it():
    text = read("scripts/backup.sh")
    assert "--stop-web" in text
    assert "docker compose stop web" in text and "up -d --wait web" in text


# --------------------------------------------------------------------------------- the pure part of db.state


def test_differences_lists_each_path_that_differs():
    expected = {"a": {"rows": 3, "md5": "x"}, "b": 1, "gone": 1}
    actual = {"a": {"rows": 4, "md5": "x"}, "b": 1, "new": 2}
    found = differences(expected, actual)
    assert len(found) == 3
    assert any(line.startswith("/a/rows") for line in found)
    assert "/gone: missing now" in found
    assert "/new: not in the backup" in found


def test_differences_of_equal_values_is_empty():
    value = {"a": [1, 2, {"b": None}], "c": "x"}
    assert differences(value, {"a": [1, 2, {"b": None}], "c": "x"}) == []
