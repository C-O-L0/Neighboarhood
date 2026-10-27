"""etl/synth_buyers.py without a database: the plan, the password rules and the collision rule."""

import pytest

from etl import pipeline
from etl.synth_buyers import SynthCollision, check_demo_passwords, find_collision, plan, synthetic_email

BUILDINGS = [(i, str(i), f"STREET {i % 7}", f"TOWN {i % 5}") for i in range(1, 301)]


def test_plan_is_deterministic_and_has_the_documented_shape():
    first, second = plan(BUILDINGS), plan(BUILDINGS)
    assert first == second
    assert [p["buyer_id"] for p in first] == list(range(1, 201))
    assert first[0]["email"] == "demo@neighboar.local" and first[0]["display_name"] == "Demo Buyer"
    assert first[1]["email"] == "admin@neighboar.local" and first[1]["is_admin"] is True
    assert first[2]["email"] == "buyer003@example.com" and first[199]["email"] == "buyer200@example.com"
    assert sum(p["is_admin"] for p in first) == 1
    assert len({p["email"] for p in first}) == 200


def test_plan_saves_and_timestamps():
    buyers = plan(BUILDINGS)
    counts = [len(p["saved"]) for p in buyers]
    assert min(counts) == 0 and max(counts) <= 10
    assert 700 <= sum(counts) <= 1300
    notes = [s.note for p in buyers for s in p["saved"]]
    assert 0.35 < sum(n is not None for n in notes) / len(notes) < 0.65
    for p in buyers:
        ids = [s.building_id for s in p["saved"]]
        assert ids == sorted(set(ids))  # no repeats, ordered
        assert p["created_at"].utcoffset().total_seconds() == 0 and p["created_at"].microsecond == 0
        for s in p["saved"]:
            assert s.saved_at.microsecond == 0 and s.saved_at.year == 2026 and s.saved_at.month == 10
            assert (s.block, s.street, s.town) == BUILDINGS[s.building_id - 1][1:]


def test_plan_does_not_depend_on_passwords():
    # plan() takes no password, so the draws cannot depend on password generation.
    assert plan.__code__.co_varnames[: plan.__code__.co_argcount] == ("building_rows",)


@pytest.mark.parametrize(
    "buyer, admin, var",
    [
        ("", "x" * 12, "DEMO_BUYER_PASSWORD"),
        ("x" * 12, "", "DEMO_ADMIN_PASSWORD"),
        ("x" * 9, "x" * 12, "DEMO_BUYER_PASSWORD"),
        ("x" * 12, "x" * 129, "DEMO_ADMIN_PASSWORD"),
        ("change-me-demo-buyer", "x" * 12, "DEMO_BUYER_PASSWORD"),
        ("x" * 12, "change-me-demo-admin", "DEMO_ADMIN_PASSWORD"),
    ],
)
def test_bad_demo_passwords_are_rejected_without_echoing_them(buyer, admin, var):
    with pytest.raises(ValueError, match=var) as err:
        check_demo_passwords(buyer, admin)
    for secret in (buyer, admin):
        assert not secret or secret not in str(err.value)


def test_good_demo_passwords_pass():
    check_demo_passwords("x" * 10, "y" * 128)


def test_collision_rule():
    find_collision([(1, "demo@neighboar.local"), (150, synthetic_email(150)), (201, "real@example.com")], "T")
    with pytest.raises(SynthCollision, match=r"150.*other@example\.org"):
        find_collision([(150, "other@example.org")], "T")
    with pytest.raises(SynthCollision, match=r"buyer003@example.com.*buyer 300"):
        find_collision([(300, "buyer003@example.com")], "T")
    with pytest.raises(SynthCollision):
        find_collision([(300, "Buyer003@Example.com")], "T")  # case-insensitive email


@pytest.mark.parametrize("buyer, admin", [("", ""), ("short", "x" * 12), ("x" * 12, "change-me-demo-admin")])
def test_pipeline_stops_before_cleaning(monkeypatch, capsys, buyer, admin):
    class Settings:
        demo_buyer_password, demo_admin_password = buyer, admin

    def boom(*a, **k):
        raise AssertionError("pipeline went on to clean")

    monkeypatch.setattr(pipeline, "get_settings", lambda: Settings)
    monkeypatch.setattr(pipeline.clean, "main", boom)
    monkeypatch.setattr(pipeline.download, "verify", boom)
    assert pipeline.main(["--sample"]) == 1
    assert "Nothing was cleaned or loaded" in capsys.readouterr().err
