"""Storage dispatcher.

Routes db.run(...) to either the local SQLite backend or the Supabase/Postgres
backend, based on environment:

  - DATABASE_URL set (real) -> PostgreSQL/Supabase
  - otherwise               -> local SQLite (default / dev)

The rest of the app calls db.run(...) as before and neither knows nor cares which
backend is active.
"""
from lib import storage_pg, storage_sqlite


def _active():
    if storage_pg.enabled():
        return storage_pg
    return storage_sqlite


def backend_name():
    return "postgres" if storage_pg.enabled() else "sqlite"


def run(sql, params=(), fetch="all"):
    return _active().run(sql, params, fetch)


def execute_script(sql):
    return _active().execute_script(sql)
