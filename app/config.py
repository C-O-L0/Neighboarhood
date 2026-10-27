"""Settings read from environment variables (see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote_plus

from psycopg.conninfo import make_conninfo
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Hosts default to the Compose service names; override for host-side tools.
    pg_host: str = "postgres"
    pg_port: int = 5432
    postgres_db: str = "neighboar"
    postgres_user: str = "postgres"
    postgres_password: str = ""
    pg_owner_password: str = ""
    pg_app_rw_password: str = ""
    pg_app_admin_password: str = ""
    pg_app_ro_password: str = ""

    mongo_host: str = "mongodb"
    mongo_port: int = 27017
    mongo_db: str = "neighboar"
    mongo_replica_set: str = "rs0"
    mongodb_initdb_root_username: str = "root"
    mongodb_initdb_root_password: str = ""
    mongo_etl_password: str = ""
    mongo_app_password: str = ""
    mongo_app_admin_password: str = ""

    secret_key: str = ""

    # Demo accounts loaded by etl/synth_buyers.py (ids 1 and 2); the ETL service only.
    demo_buyer_password: str = ""
    demo_admin_password: str = ""

    # ---- connection strings ----
    def pg_dsn(self, role: str) -> str:
        """DSN for a PostgreSQL role: postgres (superuser), neighboar_owner, app_rw, app_admin, app_ro."""
        passwords = {
            self.postgres_user: self.postgres_password,
            "neighboar_owner": self.pg_owner_password,
            "app_rw": self.pg_app_rw_password,
            "app_admin": self.pg_app_admin_password,
            "app_ro": self.pg_app_ro_password,
        }
        if role not in passwords:
            raise ValueError(f"unknown PostgreSQL role {role!r}")
        return make_conninfo(
            host=self.pg_host,
            port=self.pg_port,
            dbname=self.postgres_db,
            user=role,
            password=passwords[role],
            application_name="neighboar",
        )

    def mongo_uri(self, user: str, *, direct: bool = False) -> str:
        """URI for a MongoDB user: root, etl, app, app_admin.

        `direct=True` skips replica-set discovery; the migrate step needs it before
        the set is initiated, and host-side tools need it because the member is
        advertised as mongodb:27017.
        """
        if user == "root":
            name, password, auth_db = (
                self.mongodb_initdb_root_username,
                self.mongodb_initdb_root_password,
                "admin",
            )
        else:
            passwords = {
                "etl": self.mongo_etl_password,
                "app": self.mongo_app_password,
                "app_admin": self.mongo_app_admin_password,
            }
            if user not in passwords:
                raise ValueError(f"unknown MongoDB user {user!r}")
            name, password, auth_db = user, passwords[user], self.mongo_db
        opts = f"authSource={auth_db}"
        opts += "&directConnection=true" if direct else f"&replicaSet={self.mongo_replica_set}"
        return (
            f"mongodb://{quote_plus(name)}:{quote_plus(password)}@"
            f"{self.mongo_host}:{self.mongo_port}/{self.mongo_db}?{opts}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
