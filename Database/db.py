"""Shared PostgreSQL connection helpers for the database layer."""

import importlib


def get_connection(dsn=None):
    """Return a psycopg2 connection using the project database settings."""
    try:
        psycopg2 = importlib.import_module("psycopg2")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "psycopg2 is not installed. Install it with 'pip install psycopg2-binary' or add it to your environment."
        ) from exc

    if dsn is None:
        dsn = "postgresql://Manas@localhost:5432/manas_ufc_pred_machine"
    return psycopg2.connect(dsn)
