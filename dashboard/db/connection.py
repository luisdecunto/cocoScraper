from __future__ import annotations

import os
from urllib.parse import quote, urlencode

import psycopg2
import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def _read_streamlit_secret(key: str) -> str | None:
    try:
        value = st.secrets[key]
    except Exception:
        return None
    return str(value) if value not in (None, "") else None


def _read_setting(key: str, default: str | None = None) -> str | None:
    """Read setting from Streamlit secrets, then environment, then default."""
    secret_value = _read_streamlit_secret(key)
    if secret_value is not None:
        return secret_value

    env_value = os.getenv(key)
    if env_value not in (None, ""):
        return env_value

    return default


def has_database_config() -> bool:
    if _read_setting("DATABASE_URL"):
        return True
    return bool(_read_setting("DB_USER") and _read_setting("DB_NAME", "prices"))


def get_database_url() -> str | None:
    database_url = _read_setting("DATABASE_URL")
    if database_url:
        return database_url

    if not has_database_config():
        return None

    host = _read_setting("DB_HOST", "localhost")
    port = _read_setting("DB_PORT", "5432")
    db_name = _read_setting("DB_NAME", "prices")
    user = _read_setting("DB_USER")
    password = _read_setting("DB_PASS", "")
    sslmode = _read_setting("DB_SSLMODE")

    if not user or not db_name:
        return None

    credentials = quote(user, safe="")
    if password is not None:
        credentials = f"{credentials}:{quote(password, safe='')}"

    query_params: dict[str, str] = {}
    if sslmode:
        query_params["sslmode"] = sslmode

    query_string = f"?{urlencode(query_params)}" if query_params else ""
    return f"postgresql://{credentials}@{host}:{port}/{db_name}{query_string}"


def get_psycopg2_connection_kwargs() -> dict[str, str]:
    database_url = get_database_url()
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not configured. Set it in Streamlit secrets or your local environment."
        )
    return {"dsn": database_url}


def test_database_connection():
    conn = psycopg2.connect(**get_psycopg2_connection_kwargs())
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT NOW();")
            row = cursor.fetchone()
    finally:
        conn.close()

    return row[0] if row else None
