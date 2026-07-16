"""
Production-safe one-time migration from local SQLite to Railway PostgreSQL.

Safety controls:
- validates and normalizes the Railway public URL;
- detects accidental duplicate URL pastes before connecting;
- requires a public proxy.rlwy.net host for local Windows migration;
- creates the PostgreSQL schema before copying data;
- copies all rows in one transaction;
- refuses to overwrite existing PostgreSQL business data;
- preserves IDs and repairs serial sequences;
- verifies row counts before commit;
- rolls back the complete copy on any failure.
"""
from __future__ import annotations

import getpass
import os
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("psycopg2 is not installed. Run: python -m pip install psycopg2-binary")
    sys.exit(1)

ROOT = Path(__file__).resolve().parent
SQLITE_PATH = ROOT / "flair_crm_server.db"

TABLES = [
    "users",
    "clients",
    "processing_sheet",
    "client_payments",
    "payment_updates",
    "payment_approval_requests",
    "share_payments",
    "share_updates",
    "audit_log",
    "leads",
]
BUSINESS_TABLES = [table for table in TABLES if table != "users"]


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sqlite_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def postgres_table_exists(cur, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s) AS name", (f"public.{table}",))
    row = cur.fetchone()
    return bool(row and row["name"])


def normalize_database_url(raw_value: str) -> str:
    value = "".join(str(raw_value or "").strip().split())

    # A duplicate paste produces:
    # .../railwaypostgresql://postgres:...
    scheme_matches = list(re.finditer(r"postgres(?:ql)?://", value, flags=re.IGNORECASE))
    if len(scheme_matches) != 1:
        raise ValueError(
            "The PostgreSQL URL appears to be missing or pasted more than once. "
            "Copy DATABASE_PUBLIC_URL again and paste it only one time."
        )

    if not value.startswith(("postgresql://", "postgres://")):
        raise ValueError("The supplied value is not a PostgreSQL connection URL.")

    parsed = urlsplit(value)

    if parsed.scheme not in {"postgresql", "postgres"}:
        raise ValueError("Unsupported database URL scheme.")
    if not parsed.hostname:
        raise ValueError("The database hostname is missing.")
    if "railway.internal" in parsed.hostname:
        raise ValueError(
            "This is Railway's private URL. From Windows, use DATABASE_PUBLIC_URL "
            "whose hostname ends in proxy.rlwy.net."
        )
    if not parsed.hostname.endswith("proxy.rlwy.net"):
        raise ValueError(
            f"Unexpected public database host: {parsed.hostname}. "
            "Use Railway Postgres DATABASE_PUBLIC_URL."
        )
    if not parsed.port:
        raise ValueError("The database port is missing.")
    if not parsed.username:
        raise ValueError("The database username is missing.")
    if parsed.password is None:
        raise ValueError("The database password is missing.")

    database_name = parsed.path.lstrip("/")
    if not database_name:
        raise ValueError("The database name is missing.")
    if "/" in database_name:
        raise ValueError("The database path is invalid.")

    # Rebuild a clean URL without fragments or accidental surrounding whitespace.
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database_name}", parsed.query, ""))


def prompt_database_url() -> str:
    environment_value = os.environ.get("DATABASE_PUBLIC_URL", "").strip()

    if environment_value:
        raw_value = environment_value
        print("Using DATABASE_PUBLIC_URL from the current environment.")
    else:
        raw_value = getpass.getpass(
            "Paste Railway DATABASE_PUBLIC_URL once (input hidden), then press Enter: "
        )

    try:
        value = normalize_database_url(raw_value)
    except ValueError as exc:
        raise SystemExit(f"Invalid DATABASE_PUBLIC_URL: {exc}") from exc

    parsed = urlsplit(value)
    print(
        "Validated Railway public connection: "
        f"{parsed.hostname}:{parsed.port}/{parsed.path.lstrip('/')}"
    )
    return value


def test_postgres_connection(database_url: str) -> None:
    conn = psycopg2.connect(database_url, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user")
            database_name, database_user = cur.fetchone()
        print(f"Connection test passed: database={database_name}, user={database_user}")
    finally:
        conn.close()


def create_schema(database_url: str) -> None:
    # app.py chooses SQLite/Postgres when imported, so set DATABASE_URL first.
    os.environ["DATABASE_URL"] = database_url

    if "app" in sys.modules:
        del sys.modules["app"]

    import app as crm_app

    if not crm_app.IS_POSTGRES:
        raise RuntimeError("app.py did not activate the PostgreSQL backend.")

    crm_app.init_db()

    with crm_app.db() as conn:
        missing = [
            table
            for table in TABLES
            if not crm_app.table_exists(conn, table)
        ]

    if missing:
        raise RuntimeError(
            "PostgreSQL schema creation did not create these tables: "
            + ", ".join(missing)
        )


def main() -> None:
    if not SQLITE_PATH.exists():
        raise SystemExit(
            f"Could not find {SQLITE_PATH.name}. "
            "Run this script from the CRM project folder."
        )

    database_url = prompt_database_url()
    print("Testing Railway PostgreSQL connection...")
    test_postgres_connection(database_url)

    print("Creating/verifying PostgreSQL CRM schema...")
    create_schema(database_url)
    print("PostgreSQL schema is ready.")

    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    source_tables = sqlite_tables(sqlite_conn)

    postgres_conn = psycopg2.connect(database_url, connect_timeout=15)
    postgres_conn.autocommit = False
    postgres_cur = postgres_conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    )

    try:
        existing_business: dict[str, int] = {}

        for table in BUSINESS_TABLES:
            if postgres_table_exists(postgres_cur, table):
                postgres_cur.execute(
                    f"SELECT COUNT(*) AS c FROM {quote_ident(table)}"
                )
                count = int(postgres_cur.fetchone()["c"])
                if count:
                    existing_business[table] = count

        if existing_business:
            details = ", ".join(
                f"{table}={count}"
                for table, count in existing_business.items()
            )
            raise RuntimeError(
                "Migration stopped because PostgreSQL already contains CRM "
                f"business data: {details}"
            )

        print(
            f"Migrating {SQLITE_PATH.name} -> PostgreSQL "
            "(single transaction)\n"
        )
        source_counts: dict[str, int] = {}

        for table in TABLES:
            if table not in source_tables:
                print(f"  {table}: not found in SQLite, skipping")
                continue

            rows = sqlite_conn.execute(
                f"SELECT * FROM {quote_ident(table)} ORDER BY id"
            ).fetchall()
            source_counts[table] = len(rows)

            if not rows:
                print(f"  {table}: 0 rows")
                continue

            columns = list(rows[0].keys())
            column_sql = ",".join(quote_ident(column) for column in columns)
            placeholders = ",".join(["%s"] * len(columns))
            update_columns = [
                column for column in columns if column != "id"
            ]
            update_sql = ",".join(
                f"{quote_ident(column)}=EXCLUDED.{quote_ident(column)}"
                for column in update_columns
            )

            query = (
                f"INSERT INTO {quote_ident(table)} ({column_sql}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT ({quote_ident('id')}) "
                f"DO UPDATE SET {update_sql}"
            )

            values = [
                [row[column] for column in columns]
                for row in rows
            ]
            psycopg2.extras.execute_batch(
                postgres_cur,
                query,
                values,
                page_size=200,
            )

            postgres_cur.execute(
                "SELECT pg_get_serial_sequence(%s, 'id') AS seq",
                (f"public.{table}",),
            )
            sequence_name = postgres_cur.fetchone()["seq"]

            if sequence_name:
                postgres_cur.execute(
                    f"SELECT COALESCE(MAX(id), 0) AS max_id "
                    f"FROM {quote_ident(table)}"
                )
                max_id = int(postgres_cur.fetchone()["max_id"])

                if max_id > 0:
                    postgres_cur.execute(
                        "SELECT setval(%s, %s, true)",
                        (sequence_name, max_id),
                    )
                else:
                    postgres_cur.execute(
                        "SELECT setval(%s, 1, false)",
                        (sequence_name,),
                    )

            print(f"  {table}: copied {len(rows)} rows")

        print("\nVerifying row counts before commit...")
        mismatches: list[str] = []

        for table, source_count in source_counts.items():
            postgres_cur.execute(
                f"SELECT COUNT(*) AS c FROM {quote_ident(table)}"
            )
            destination_count = int(postgres_cur.fetchone()["c"])

            if destination_count != source_count:
                mismatches.append(
                    f"{table}: SQLite={source_count}, "
                    f"PostgreSQL={destination_count}"
                )
            else:
                print(f"  {table}: {destination_count} OK")

        if mismatches:
            raise RuntimeError(
                "Count verification failed: " + "; ".join(mismatches)
            )

        postgres_conn.commit()
        print("\nSUCCESS: PostgreSQL migration committed and verified.")
        print(
            "Next: commit the updated files, push to GitHub, "
            "wait for Railway deployment, then open /health."
        )

    except Exception as exc:
        postgres_conn.rollback()
        print(f"\nMIGRATION CANCELLED: {exc}")
        print("No migration data was committed to PostgreSQL.")
        sys.exit(1)

    finally:
        postgres_cur.close()
        postgres_conn.close()
        sqlite_conn.close()


if __name__ == "__main__":
    main()
