"""
One-time migration script: copies all existing data from the local
SQLite database (flair_crm_server.db) into Postgres.

HOW TO USE (run this on your own computer, from the project folder):

1. Make sure the app has been deployed at least once with Postgres attached
   on Railway (so DATABASE_URL exists and the tables have been created by
   init_db() on startup).

2. Get your Postgres connection string from Railway:
   Railway dashboard -> Postgres service -> "Connect" tab -> copy the
   "Postgres Connection URL" (starts with postgresql://...).

3. Install psycopg2 locally if you don't have it:
       pip install psycopg2-binary

4. Run this script, pasting the connection string when asked (or set it
   as an environment variable DATABASE_URL beforehand):
       python migrate_to_postgres.py

5. It will copy every row from your local flair_crm_server.db into
   Postgres, table by table, and fix the auto-increment counters so new
   records created afterwards continue from the right ID.

Safe to re-run: it only inserts rows whose ID doesn't already exist in
Postgres, so running it twice will not create duplicates.
"""
import os
import sqlite3
import sys

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("psycopg2 is not installed. Run: pip install psycopg2-binary")
    sys.exit(1)

SQLITE_PATH = "flair_crm_server.db"

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

def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        database_url = input("Paste your Railway Postgres connection URL: ").strip()

    if not os.path.exists(SQLITE_PATH):
        print(f"Could not find {SQLITE_PATH} in the current folder.")
        print("Run this script from inside the flair_jarvis_crm_production folder.")
        sys.exit(1)

    sconn = sqlite3.connect(SQLITE_PATH)
    sconn.row_factory = sqlite3.Row

    pconn = psycopg2.connect(database_url)
    pconn.autocommit = False
    pcur = pconn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print(f"Connected. Migrating from {SQLITE_PATH} -> Postgres\n")

    for table in TABLES:
        try:
            rows = sconn.execute(f"SELECT * FROM {table}").fetchall()
        except sqlite3.OperationalError:
            print(f"  {table}: not found locally, skipping")
            continue

        if not rows:
            print(f"  {table}: 0 rows, nothing to migrate")
            continue

        columns = rows[0].keys()
        col_list = ",".join(columns)
        placeholders = ",".join(["%s"] * len(columns))

        inserted = 0
        for r in rows:
            values = [r[c] for c in columns]
            try:
                pcur.execute(
                    f"INSERT INTO {table}({col_list}) VALUES({placeholders}) "
                    f"ON CONFLICT (id) DO NOTHING",
                    values,
                )
                inserted += pcur.rowcount
            except Exception as e:
                pconn.rollback()
                print(f"  {table}: FAILED on row id={r['id']} -> {e}")
                continue

        # Make sure future SERIAL inserts continue after the highest migrated id
        pcur.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
        )
        pconn.commit()
        print(f"  {table}: migrated {inserted}/{len(rows)} rows")

    pcur.close()
    pconn.close()
    sconn.close()
    print("\nDone. Check the CRM's /health page and a few records to confirm.")

if __name__ == "__main__":
    main()
