"""BUILD_PLAN section 9 checks that read the repository's own files (task 3.5b).

Published ports, ignore files, placeholder secrets, non-root images, the CI non-root step and the SECRET_KEY rule.
The running stack is checked by the CI host step (and by tests/integration/test_runtime_identities.py).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from app.config import get_settings
from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


# ----------------------------------------------------------------------------------------------- compose.yaml


def services() -> dict:
    return yaml.safe_load(read("compose.yaml"))["services"]


def test_only_web_publishes_a_port_and_only_on_loopback():
    published = {name: svc.get("ports", []) for name, svc in services().items() if svc.get("ports")}
    assert set(published) == {"web"}
    for entry in published["web"]:
        assert str(entry).startswith("127.0.0.1:"), f"web must publish on 127.0.0.1 only, not {entry!r}"


@pytest.mark.parametrize("name", ["postgres", "mongodb"])
def test_the_databases_publish_no_ports(name):
    svc = services()[name]
    assert not svc.get("ports"), f"{name} must not publish a port (use compose.override.yaml.example for host tools)"


def test_the_override_example_is_not_loaded_by_default():
    assert "compose.override.yaml" in read(".gitignore").split()
    assert (ROOT / "compose.override.yaml.example").exists()


# ------------------------------------------------------------------------------------ secrets and ignore files


def lines(name: str) -> list[str]:
    return [line.strip() for line in read(name).splitlines()]


def test_env_is_ignored_by_git_and_by_docker():
    assert ".env" in lines(".gitignore")
    assert ".env" in lines(".dockerignore")


def test_env_example_holds_only_placeholders():
    values = {}
    for line in read(".env.example").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    secrets_ = {k: v for k, v in values.items() if k.endswith("_PASSWORD") or k == "SECRET_KEY"}
    assert "SECRET_KEY" in secrets_ and len(secrets_) >= 10, "the secrets were not found"
    bad = {k: v for k, v in secrets_.items() if not v.startswith("change-me")}
    assert not bad, f"these .env.example values must start with change-me: {sorted(bad)}"


# --------------------------------------------------------------------------------------------------- non-root


@pytest.mark.parametrize("dockerfile", ["Dockerfile", "db/mongo/Dockerfile", "db/tools/Dockerfile"])
def test_images_end_with_a_non_root_user(dockerfile):
    users = re.findall(r"^\s*USER\s+(\S+)", read(dockerfile), flags=re.M | re.I)
    assert users, f"{dockerfile} has no USER instruction"
    last = users[-1].split(":")[0]
    assert last.lower() not in {"root", "0"}, f"{dockerfile} ends as {users[-1]}"
    body = [line.split()[0].upper() for line in read(dockerfile).splitlines() if line.strip() and line[0] != "#"]
    after = body[len(body) - body[::-1].index("USER") :]  # instructions after the last USER
    assert not {"RUN", "COPY", "ADD"} & set(after), f"{dockerfile}: build steps after the final USER"


def test_the_ci_host_step_checks_pid_1_of_every_service_after_the_stack_is_up():
    steps = yaml.safe_load(read(".github/workflows/ci.yml"))["jobs"]["stack"]["steps"]
    names = [s.get("name", "") for s in steps]
    started = names.index("Start the web app")
    checks = [i for i, s in enumerate(steps) if "cat /proc/1/status | grep Uid" in s.get("run", "")]
    assert len(checks) == 1 and checks[0] > started
    run = steps[checks[0]]["run"]
    for service in ("postgres", "mongodb", "web"):
        assert service in run
    assert "docker compose exec -T" in run
    assert "grep Uid" in run  # the MongoDB image has no grep, so it is the host that filters
    assert "id -u" not in run  # runs as the image's default user (root for postgres), not as the server
    assert "exit 1" in run


# ------------------------------------------------------------------------------------------------- SECRET_KEY


@pytest.fixture
def secret(monkeypatch):
    def set_key(value: str) -> None:
        monkeypatch.setenv("SECRET_KEY", value)
        get_settings.cache_clear()

    yield set_key
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "value",
    ["", "short", "x" * 31, "change-me-long-random-string", "change-me-" + "x" * 40],
    ids=["empty", "short", "31 characters", "the .env.example value", "change-me prefix, long"],
)
def test_create_app_refuses_a_weak_secret_key(secret, value):
    secret(value)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app()


def test_create_app_accepts_a_32_character_key(secret):
    secret("k" * 32)
    assert create_app().title == "Neighboar"
