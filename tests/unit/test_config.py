import pytest

from app.config import Settings


def make() -> Settings:
    return Settings(
        _env_file=None,
        pg_app_rw_password="p w'x",
        mongo_app_password="a@b:c/d",
        mongodb_initdb_root_password="r",
    )


def test_pg_dsn_quotes_passwords():
    dsn = make().pg_dsn("app_rw")
    assert "user=app_rw" in dsn
    assert "password='p w\\'x'" in dsn


def test_pg_dsn_rejects_unknown_role():
    with pytest.raises(ValueError):
        make().pg_dsn("hacker")


def test_mongo_uri_escapes_and_uses_replica_set():
    uri = make().mongo_uri("app")
    assert "a%40b%3Ac%2Fd" in uri
    assert "replicaSet=rs0" in uri and "authSource=neighboar" in uri


def test_mongo_uri_direct_for_root():
    uri = make().mongo_uri("root", direct=True)
    assert "directConnection=true" in uri and "authSource=admin" in uri
