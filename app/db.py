"""Connection pools for both databases, opened at start-up and closed at shutdown.

The web app only ever connects as least-privilege identities:
  PostgreSQL  app_rw (all user-facing routes), app_admin (admin routes only)
  MongoDB     app, app_admin
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg_pool import ConnectionPool
from pymongo import MongoClient
from pymongo.database import Database

from app.config import Settings


@dataclass
class Databases:
    pg_rw: ConnectionPool
    pg_admin: ConnectionPool
    mongo_app: MongoClient
    mongo_admin: MongoClient
    mongo_db_name: str

    @classmethod
    def open(cls, s: Settings) -> Databases:
        def pool(role: str) -> ConnectionPool:
            p = ConnectionPool(s.pg_dsn(role), min_size=1, max_size=10, open=False, name=role)
            p.open(wait=False)
            return p

        def mongo(user: str) -> MongoClient:
            return MongoClient(s.mongo_uri(user), serverSelectionTimeoutMS=3000, tz_aware=True)

        return cls(
            pg_rw=pool("app_rw"),
            pg_admin=pool("app_admin"),
            mongo_app=mongo("app"),
            mongo_admin=mongo("app_admin"),
            mongo_db_name=s.mongo_db,
        )

    def mongo(self, *, admin: bool = False) -> Database:
        client = self.mongo_admin if admin else self.mongo_app
        return client[self.mongo_db_name]

    def close(self) -> None:
        self.pg_rw.close()
        self.pg_admin.close()
        self.mongo_app.close()
        self.mongo_admin.close()
