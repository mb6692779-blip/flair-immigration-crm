from __future__ import annotations
import json, os, sqlite3, csv, io, shutil
from datetime import datetime
from pathlib import Path
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DATABASE_PATH", APP_DIR / "flair_crm_server.db"))
SEED_PATH = APP_DIR / "seed_data" / "seed.json"
IMPORT_MARKER = APP_DIR / "seed_data" / ".imported"
BACKUP_DIR = APP_DIR / "backups"
BACKUP_DIR.mkdir(exist_ok=True)

# Railway automatically injects DATABASE_URL when a Postgres plugin is attached.
# If it's present we use Postgres (permanent storage); otherwise we fall back to
# the local SQLite file (handy for testing on Windows via RUN_CRM_WINDOWS.bat).
DATABASE_URL = os.environ.get("DATABASE_URL", "")
IS_POSTGRES = bool(DATABASE_URL) and psycopg2 is not None
if os.environ.get("DATABASE_URL") and psycopg2 is None:
    print("WARNING: DATABASE_URL is set but psycopg2 is not installed — falling back to SQLite. "
          "Add 'psycopg2-binary' to requirements.txt.")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "flair-crm-secret-change-in-railway")

VISA_TYPES = ["D2","D7","D8","D9","Golden Visa","Digital Nomad","Visit Visa","Case Review","Other"]
SECTION_TYPES = ["Single","Group"]
REFERENCE_TYPES = ["Self","Partner Office","Director","Walk-in","Other"]
PROCESS_STATUS = ["ok","OK","Pending","N/A","Appointment","Final Decision","Funds Transfer","Company Name","Company Formation","Company Bank Account","Personal Account","Application Submission","Complete","Not Complete"]
DELAY_ENDS = ["Client end","Vendor end","Vendor Hand","Embassy end","Lawyer end","Bank end","Management end","Other"]
PAYMENT_STAGES = ["Stage 1 - 70%","Stage 2 - 15%","Stage 3 - 15%"]
CLIENT_STAGES = PAYMENT_STAGES
SHARE_TYPES = ["FS Lisbon","Migration Lawyer","Other Partner"]

class DBConnection:
    """Wraps either a sqlite3 or psycopg2 connection so the rest of the app
    can keep writing `con.execute("...WHERE x=?", (val,))` and
    `with db() as con:` unchanged, regardless of which database is active."""

    def __init__(self):
        if IS_POSTGRES:
            self._con = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            self._con = sqlite3.connect(DB_PATH, timeout=30)
            self._con.row_factory = sqlite3.Row
            self._con.execute("PRAGMA foreign_keys = ON")
            self._con.execute("PRAGMA journal_mode = WAL")
            self._con.execute("PRAGMA synchronous = NORMAL")
            self._con.execute("PRAGMA busy_timeout = 30000")

    def execute(self, sql, params=()):
        if IS_POSTGRES:
            sql = sql.replace("?", "%s")
        cur = self._con.cursor()
        cur.execute(sql, params)
        return cur

    def executescript(self, sql):
        if IS_POSTGRES:
            cur = self._con.cursor()
            cur.execute(sql)
        else:
            self._con.executescript(sql)

    def last_id(self):
        """Equivalent of SQLite's last_insert_rowid(), works right after an INSERT."""
        if IS_POSTGRES:
            return self.execute("SELECT lastval() AS id").fetchone()["id"]
        return self.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def commit(self):
        self._con.commit()

    def rollback(self):
        self._con.rollback()

    def close(self):
        self._con.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._con.commit()
        else:
            self._con.rollback()
        self._con.close()
        return False


def db():
    return DBConnection()

def now():
    return datetime.now().isoformat(timespec="seconds")

def safe_float(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0

def init_db():
    with db() as con:
        schema_sql = """
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS clients(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            visa_type TEXT,
            section_type TEXT DEFAULT 'Single',
            group_size INTEGER DEFAULT 1,
            main_investor TEXT,
            partner_names TEXT,
            reference_type TEXT DEFAULT 'Self',
            reference_name TEXT,
            partner_office_name TEXT,
            director_name TEXT,
            issue_status TEXT DEFAULT 'No',
            issue_details TEXT,
            status TEXT DEFAULT 'Active',
            remarks TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS processing_sheet(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sr_no TEXT,
            client_name TEXT,
            program TEXT,
            processing_start_date TEXT,
            nif TEXT,
            personal_bank_account TEXT,
            company_name TEXT,
            company_formation TEXT,
            company_bank_account TEXT,
            business_plan TEXT,
            personal_funds_transferred TEXT,
            application_ready TEXT,
            funds_transfer TEXT,
            application_submission TEXT,
            decision TEXT,
            status TEXT,
            delay TEXT,
            remarks TEXT,
            updated_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS client_payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            visa TEXT,
            inv TEXT,
            client_name TEXT,
            roe_invoice REAL DEFAULT 0,
            total_eur REAL DEFAULT 0,
            invoice_pkr REAL DEFAULT 0,
            stage1_roe REAL DEFAULT 0,
            stage1_eur REAL DEFAULT 0,
            stage2_roe REAL DEFAULT 0,
            stage2_eur REAL DEFAULT 0,
            stage3_roe REAL DEFAULT 0,
            stage3_eur REAL DEFAULT 0,
            received_pkr REAL DEFAULT 0,
            received_percent REAL DEFAULT 0,
            balance_pkr REAL DEFAULT 0,
            balance_eur REAL DEFAULT 0,
            payment_stage TEXT,
            remarks TEXT,
            updated_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS payment_updates(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_payment_id INTEGER NOT NULL,
            payment_date TEXT,
            stage TEXT,
            received_eur REAL DEFAULT 0,
            roe REAL DEFAULT 0,
            received_pkr REAL DEFAULT 0,
            updated_by TEXT,
            remarks TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(client_payment_id) REFERENCES client_payments(id)
        );

        CREATE TABLE IF NOT EXISTS payment_approval_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_type TEXT DEFAULT 'add',
            client_payment_id INTEGER,
            payment_date TEXT,
            stage TEXT,
            received_eur REAL DEFAULT 0,
            roe REAL DEFAULT 0,
            received_pkr REAL DEFAULT 0,
            requested_by TEXT,
            remarks TEXT,
            status TEXT DEFAULT 'Pending',
            management_note TEXT,
            decided_by TEXT,
            decided_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS share_payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            share_type TEXT,
            date TEXT,
            visa TEXT,
            inv TEXT,
            client_name TEXT,
            roe_invoice REAL DEFAULT 0,
            total_eur REAL DEFAULT 0,
            total_pkr REAL DEFAULT 0,
            stage1_roe REAL DEFAULT 0,
            stage1_eur REAL DEFAULT 0,
            stage2_roe REAL DEFAULT 0,
            stage2_eur REAL DEFAULT 0,
            stage3_roe REAL DEFAULT 0,
            stage3_eur REAL DEFAULT 0,
            paid_pkr REAL DEFAULT 0,
            paid_percent REAL DEFAULT 0,
            balance_pkr REAL DEFAULT 0,
            balance_eur REAL DEFAULT 0,
            status TEXT,
            remarks TEXT,
            updated_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS share_updates(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            share_payment_id INTEGER NOT NULL,
            transfer_date TEXT,
            stage TEXT,
            transfer_eur REAL DEFAULT 0,
            roe REAL DEFAULT 0,
            transfer_pkr REAL DEFAULT 0,
            updated_by TEXT,
            remarks TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(share_payment_id) REFERENCES share_payments(id)
        );

        CREATE TABLE IF NOT EXISTS audit_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            action TEXT,
            details TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS leads(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_date TEXT,
            name TEXT NOT NULL,
            phone TEXT,
            city TEXT,
            source TEXT,
            country_interest TEXT,
            program_interest TEXT,
            lead_status TEXT DEFAULT 'New Lead',
            next_followup TEXT,
            remarks TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
        if IS_POSTGRES:
            schema_sql = schema_sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        con.executescript(schema_sql)
        for username, password, role, name in [
            ("server", "server2026", "server", "Server Admin"),
            ("management", "management2011", "management", "Management"),
            ("accounts", "accounts123", "accounts", "Accounts Team"),
        ]:
            exists = con.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            if not exists:
                con.execute("INSERT INTO users(username,password_hash,role,name,created_at) VALUES(?,?,?,?,?)",
                    (username, generate_password_hash(password), role, name, now()))
        con.commit()


# Initialize the schema when the module is imported (for Railway/Gunicorn) as well as when run locally.
# This is idempotent: CREATE TABLE IF NOT EXISTS and default-user checks are safe to repeat.
init_db()

def import_seed_once():
    if IMPORT_MARKER.exists() or not SEED_PATH.exists():
        return
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    with db() as con:
        if con.execute("SELECT COUNT(*) c FROM clients").fetchone()["c"] == 0:
            for r in data.get("clients", []):
                con.execute("""INSERT INTO clients(date,visa_type,section_type,group_size,main_investor,partner_names,reference_type,reference_name,issue_status,issue_details,status,remarks,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    r.get("date",""), r.get("visa_type",""), r.get("section_type","Single"), r.get("group_size",1),
                    r.get("main_investor") or r.get("client_name",""), r.get("partner_names",""), r.get("reference_type","Self"),
                    r.get("reference_name",""), r.get("issue_status","No"), r.get("issue_details",""), r.get("status","Active"),
                    r.get("remarks",""), now(), now()
                ))

        if con.execute("SELECT COUNT(*) c FROM processing_sheet").fetchone()["c"] == 0:
            for r in data.get("processing_rows", []):
                con.execute("""INSERT INTO processing_sheet(sr_no,client_name,program,processing_start_date,nif,personal_bank_account,company_name,company_formation,company_bank_account,business_plan,personal_funds_transferred,application_ready,funds_transfer,application_submission,decision,status,delay,remarks,updated_by,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    r.get("Sr no.",""), r.get("Client Name",""), r.get("Program",""), r.get("Processing Start Date",""),
                    r.get("NIF",""), r.get("Personal bank account",""), r.get("Company Name",""), r.get("Company formation",""),
                    r.get("CompanyBank account",""), r.get("Businessplan",""), r.get("Personal Funds Transferred",""),
                    r.get("Application ready",""), r.get("Funds Transfer",""), r.get("Application Submission",""),
                    r.get("Decision",""), r.get("Status",""), r.get("Delay",""), "", "seed_import", now(), now()
                ))

        if con.execute("SELECT COUNT(*) c FROM client_payments").fetchone()["c"] == 0:
            for r in data.get("client_payments", []):
                con.execute("""INSERT INTO client_payments(date,visa,inv,client_name,roe_invoice,total_eur,invoice_pkr,stage1_roe,stage1_eur,stage2_roe,stage2_eur,stage3_roe,stage3_eur,received_pkr,received_percent,balance_pkr,balance_eur,payment_stage,remarks,updated_by,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    r.get("date",""), r.get("visa",""), r.get("inv",""), r.get("client_name",""), r.get("roe_invoice",0),
                    r.get("total_eur",0), r.get("invoice_pkr",0), r.get("stage1_roe",0), r.get("stage1_eur",0),
                    r.get("stage2_roe",0), r.get("stage2_eur",0), r.get("stage3_roe",0), r.get("stage3_eur",0),
                    r.get("received_pkr",0), r.get("received_percent",0), r.get("balance_pkr",0), r.get("balance_eur",0),
                    payment_stage_from_balance(r.get("balance_eur",0)), r.get("remarks",""), "seed_import", now(), now()
                ))

        if con.execute("SELECT COUNT(*) c FROM share_payments").fetchone()["c"] == 0:
            for list_name, share_type in [("fs_shares","FS Lisbon"),("lawyer_shares","Migration Lawyer"),("other_shares","Other Partner")]:
                for r in data.get(list_name, []):
                    con.execute("""INSERT INTO share_payments(share_type,date,visa,inv,client_name,roe_invoice,total_eur,total_pkr,stage1_roe,stage1_eur,stage2_roe,stage2_eur,stage3_roe,stage3_eur,paid_pkr,paid_percent,balance_pkr,balance_eur,status,remarks,updated_by,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                        r.get("share_type") or share_type, r.get("date",""), r.get("visa",""), r.get("inv",""), r.get("client_name",""),
                        r.get("roe_invoice",0), r.get("total_eur",0), r.get("invoice_pkr",0), r.get("stage1_roe",0), r.get("stage1_eur",0),
                        r.get("stage2_roe",0), r.get("stage2_eur",0), r.get("stage3_roe",0), r.get("stage3_eur",0),
                        r.get("received_pkr",0), r.get("received_percent",0), r.get("balance_pkr",0), r.get("balance_eur",0),
                        "Complete" if (r.get("balance_eur",0) or 0) <= 0 else "Pending", r.get("remarks",""), "seed_import", now(), now()
                    ))
        con.execute("INSERT INTO audit_log(username,action,details,created_at) VALUES(?,?,?,?)", ("system","seed_import","Excel sheets imported",now()))
        con.commit()
    IMPORT_MARKER.write_text(now(), encoding="utf-8")


def parse_percent(v):
    s = str(v or "").replace("%", "").strip()
    try:
        return float(s)
    except Exception:
        return 0.0

def table_exists(con, table_name):
    if IS_POSTGRES:
        row = con.execute("SELECT table_name FROM information_schema.tables WHERE table_name=?", (table_name,)).fetchone()
        return row is not None
    return con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone() is not None

def make_search_where(column, q):
    words = [w.strip().lower() for w in str(q or "").replace("+", " ").split() if w.strip()]
    if not words:
        return "1=1", []
    return " AND ".join([f"lower({column}) LIKE ?" for _ in words]), [f"%{w}%" for w in words]


def backup_database_file(reason="manual"):
    if IS_POSTGRES:
        # Nothing to copy — there's no local .db file on Postgres.
        # The JSON export in the /backup route is the real backup for this mode.
        return ""
    try:
        BACKUP_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = BACKUP_DIR / f"db_backup_{reason}_{stamp}.sqlite"
        if DB_PATH.exists():
            shutil.copy2(DB_PATH, target)
            wal = Path(str(DB_PATH) + "-wal")
            shm = Path(str(DB_PATH) + "-shm")
            if wal.exists():
                shutil.copy2(wal, BACKUP_DIR / f"db_backup_{reason}_{stamp}.sqlite-wal")
            if shm.exists():
                shutil.copy2(shm, BACKUP_DIR / f"db_backup_{reason}_{stamp}.sqlite-shm")
            return str(target)
    except Exception as e:
        print("Backup failed:", e)
    return ""

def cleanup_old_backups(keep=30):
    try:
        backups = sorted(BACKUP_DIR.glob("db_backup_*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in backups[keep:]:
            old.unlink(missing_ok=True)
            Path(str(old) + "-wal").unlink(missing_ok=True)
            Path(str(old) + "-shm").unlink(missing_ok=True)
    except Exception as e:
        print("Backup cleanup failed:", e)

def daily_backup_once():
    marker = BACKUP_DIR / ".last_daily_backup"
    today = datetime.now().strftime("%Y-%m-%d")
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == today:
        return
    backup_database_file("daily")
    cleanup_old_backups(keep=30)
    marker.write_text(today, encoding="utf-8")


def restore_preloaded_exact_once(force=False):
    seed_file = APP_DIR / "seed_data" / "preloaded_exact.json"
    marker = APP_DIR / "seed_data" / ".preloaded_exact_restored_v3"
    if not seed_file.exists():
        return
    if marker.exists() and not force:
        return
    backup_database_file("before_exact_restore")
    data = json.loads(seed_file.read_text(encoding="utf-8"))
    with db() as con:
        for table in ["clients", "processing_sheet", "client_payments", "share_payments", "payment_updates", "share_updates"]:
            con.execute(f"DELETE FROM {table}")
        if table_exists(con, "payment_approval_requests"):
            con.execute("DELETE FROM payment_approval_requests")
        for r in data.get("clientMaster", {}).get("rows", []):
            con.execute("""INSERT INTO clients(date, main_investor, visa_type, section_type, group_size, partner_names, reference_type, reference_name, issue_status, issue_details, remarks, status, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (r[0] if len(r)>0 else "", r[1] if len(r)>1 else "", r[2] if len(r)>2 else "", r[3] if len(r)>3 else "", int(r[4] or 1) if len(r)>4 else 1, r[5] if len(r)>5 else "", r[6] if len(r)>6 else "", r[7] if len(r)>7 else "", r[8] if len(r)>8 else "", r[9] if len(r)>9 else "", r[10] if len(r)>10 else "", "Active", now(), now()))
        for r in data.get("process", {}).get("rows", []):
            con.execute("""INSERT INTO processing_sheet(client_name, program, processing_start_date, nif, personal_bank_account, company_name, company_formation, company_bank_account, business_plan, funds_transfer, application_submission, decision, status, delay, remarks, updated_by, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (r[0] if len(r)>0 else "", r[1] if len(r)>1 else "", r[2] if len(r)>2 else "", r[3] if len(r)>3 else "", r[4] if len(r)>4 else "", r[5] if len(r)>5 else "", r[6] if len(r)>6 else "", r[7] if len(r)>7 else "", r[8] if len(r)>8 else "", r[9] if len(r)>9 else "", r[10] if len(r)>10 else "", r[11] if len(r)>11 else "", r[12] if len(r)>12 else "", r[13] if len(r)>13 else "", r[14] if len(r)>14 else "", "exact_restore", now(), now()))
        for r in data.get("payments", {}).get("rows", []):
            con.execute("""INSERT INTO client_payments(date, visa, inv, client_name, total_eur, roe_invoice, invoice_pkr, stage1_eur, stage1_roe, stage2_eur, stage2_roe, stage3_eur, stage3_roe, received_pkr, received_percent, balance_pkr, balance_eur, payment_stage, remarks, updated_by, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (r[0] if len(r)>0 else "", r[1] if len(r)>1 else "", r[2] if len(r)>2 else "", r[3] if len(r)>3 else "", safe_float(r[4] if len(r)>4 else 0), safe_float(r[5] if len(r)>5 else 0), safe_float(r[6] if len(r)>6 else 0), safe_float(r[7] if len(r)>7 else 0), safe_float(r[8] if len(r)>8 else 0), safe_float(r[9] if len(r)>9 else 0), safe_float(r[10] if len(r)>10 else 0), safe_float(r[11] if len(r)>11 else 0), safe_float(r[12] if len(r)>12 else 0), safe_float(r[13] if len(r)>13 else 0), parse_percent(r[14] if len(r)>14 else 0), safe_float(r[15] if len(r)>15 else 0), safe_float(r[16] if len(r)>16 else 0), r[17] if len(r)>17 else "", r[18] if len(r)>18 else "", "exact_restore", now(), now()))
        def insert_share(section, share_type):
            for r in data.get(section, {}).get("rows", []):
                con.execute("""INSERT INTO share_payments(share_type, date, visa, inv, client_name, total_eur, total_pkr, stage1_eur, stage1_roe, stage2_eur, stage2_roe, stage3_eur, stage3_roe, paid_pkr, paid_percent, balance_pkr, balance_eur, status, remarks, updated_by, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (share_type, r[0] if len(r)>0 else "", r[1] if len(r)>1 else "", r[2] if len(r)>2 else "", r[3] if len(r)>3 else "", safe_float(r[4] if len(r)>4 else 0), safe_float(r[5] if len(r)>5 else 0), safe_float(r[6] if len(r)>6 else 0), safe_float(r[7] if len(r)>7 else 0), safe_float(r[8] if len(r)>8 else 0), safe_float(r[9] if len(r)>9 else 0), safe_float(r[10] if len(r)>10 else 0), safe_float(r[11] if len(r)>11 else 0), safe_float(r[12] if len(r)>12 else 0), parse_percent(r[13] if len(r)>13 else 0), safe_float(r[14] if len(r)>14 else 0), safe_float(r[15] if len(r)>15 else 0), r[16] if len(r)>16 else "", r[17] if len(r)>17 else "", "exact_restore", now(), now()))
        insert_share("fs", "FS Lisbon")
        insert_share("lawyer", "Migration Lawyer")
        for r in data.get("partners", {}).get("rows", []):
            con.execute("""INSERT INTO share_payments(share_type, date, visa, inv, client_name, total_eur, total_pkr, stage1_eur, stage1_roe, paid_pkr, paid_percent, balance_pkr, balance_eur, status, remarks, updated_by, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (r[0] if len(r)>0 else "Other Partner", r[1] if len(r)>1 else "", r[2] if len(r)>2 else "", r[3] if len(r)>3 else "", r[4] if len(r)>4 else "", safe_float(r[5] if len(r)>5 else 0), safe_float(r[6] if len(r)>6 else 0), safe_float(r[7] if len(r)>7 else 0), safe_float(r[8] if len(r)>8 else 0), safe_float(r[9] if len(r)>9 else 0), parse_percent(r[10] if len(r)>10 else 0), safe_float(r[11] if len(r)>11 else 0), safe_float(r[12] if len(r)>12 else 0), r[13] if len(r)>13 else "", r[14] if len(r)>14 else "", "exact_restore", now(), now()))
        con.commit()
    marker.parent.mkdir(exist_ok=True)
    marker.write_text(now(), encoding="utf-8")

def payment_stage_from_balance(balance_eur):
    try:
        return "Complete" if float(balance_eur or 0) <= 0 else "Pending"
    except:
        return "Pending"

def audit(action, details=""):
    try:
        with db() as con:
            con.execute("INSERT INTO audit_log(username,action,details,created_at) VALUES(?,?,?,?)",
                (session.get("username","system"), action, details, now()))
            con.commit()
    except Exception:
        pass

def login_required(fn):
    @wraps(fn)
    def wrap(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrap

def management_required(fn):
    @wraps(fn)
    def wrap(*args, **kwargs):
        if session.get("role") not in ["management", "server"]:
            flash("Management access required.")
            return redirect(url_for("dashboard"))
        return fn(*args, **kwargs)
    return wrap

def accounts_or_management(fn):
    @wraps(fn)
    def wrap(*args, **kwargs):
        if session.get("role") not in ["management", "accounts", "server"]:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrap

@app.context_processor
def inject():
    return dict(role=session.get("role"), name=session.get("name"), VISA_TYPES=VISA_TYPES, SECTION_TYPES=SECTION_TYPES, REFERENCE_TYPES=REFERENCE_TYPES, PROCESS_STATUS=PROCESS_STATUS, DELAY_ENDS=DELAY_ENDS, PAYMENT_STAGES=PAYMENT_STAGES, CLIENT_STAGES=CLIENT_STAGES, SHARE_TYPES=SHARE_TYPES)

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username","").strip().lower()
        password = request.form.get("password","")
        with db() as con:
            u = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if u and check_password_hash(u["password_hash"], password):
            session.clear()
            session["user_id"] = u["id"]; session["username"] = u["username"]; session["role"] = u["role"]; session["name"] = u["name"]
            audit("login", username)
            return redirect(url_for("dashboard"))
        flash("Wrong username or password.")
    return render_template("login.html")

@app.get("/logout")
def logout():
    audit("logout", session.get("username",""))
    session.clear()
    return redirect(url_for("login"))



@app.get("/")
@login_required
def dashboard():
    q = request.args.get("q", "").strip()
    summary = get_client_summary(q) if q else None
    with db() as con:
        pending_approvals = con.execute("SELECT COUNT(*) c FROM payment_approval_requests WHERE status='Pending'").fetchone()["c"] if table_exists(con, "payment_approval_requests") else 0
    cards = {"total_clients": 34, "client_payments": 25, "fs": 26, "lawyer": 26, "pending_approvals": pending_approvals}
    return render_template("dashboard.html", q=q, summary=summary, cards=cards)

def get_client_summary(q):
    client_where, client_params = make_search_where("main_investor", q)
    row_where, row_params = make_search_where("client_name", q)
    with db() as con:
        client = con.execute(f"SELECT * FROM clients WHERE {client_where} ORDER BY id DESC LIMIT 1", client_params).fetchone()
        proc = con.execute(f"SELECT * FROM processing_sheet WHERE {row_where} ORDER BY id DESC LIMIT 10", row_params).fetchall()
        raw_payments = con.execute(f"SELECT * FROM client_payments WHERE {row_where} ORDER BY id DESC", row_params).fetchall()
        raw_shares = con.execute(f"SELECT * FROM share_payments WHERE {row_where} ORDER BY share_type, id DESC", row_params).fetchall()
        updates=[]
        if raw_payments:
            ids=[str(p["id"]) for p in raw_payments]
            updates=con.execute(f"SELECT * FROM payment_updates WHERE client_payment_id IN ({','.join(['?']*len(ids))}) ORDER BY id DESC", ids).fetchall()
    payments=[]; shares=[]
    total_eur=received_eur=received_pkr=balance_eur=balance_pkr=0.0
    for p in raw_payments:
        d=dict(p)
        d["total_eur"]=safe_float(d.get("total_eur")); d["stage1_eur"]=safe_float(d.get("stage1_eur")); d["stage2_eur"]=safe_float(d.get("stage2_eur")); d["stage3_eur"]=safe_float(d.get("stage3_eur"))
        d["received_eur"]=d["stage1_eur"]+d["stage2_eur"]+d["stage3_eur"]
        d["received_pkr"]=safe_float(d.get("received_pkr")); d["received_percent"]=safe_float(d.get("received_percent")); d["balance_eur"]=safe_float(d.get("balance_eur")); d["balance_pkr"]=safe_float(d.get("balance_pkr"))
        total_eur += d["total_eur"]; received_eur += d["received_eur"]; received_pkr += d["received_pkr"]; balance_eur += d["balance_eur"]; balance_pkr += d["balance_pkr"]
        payments.append(d)
    for r in raw_shares:
        d=dict(r); d["total_eur"]=safe_float(d.get("total_eur")); d["paid_pkr"]=safe_float(d.get("paid_pkr")); d["paid_percent"]=safe_float(d.get("paid_percent")); d["balance_eur"]=safe_float(d.get("balance_eur")); d["balance_pkr"]=safe_float(d.get("balance_pkr")); shares.append(d)
    current_stage = payments[0]["payment_stage"] if payments else "No Payment Record"
    return {"client":client,"processing":proc,"payments":payments,"shares":shares,"updates":updates,"total_eur":total_eur,"received_eur":received_eur,"received_pkr":received_pkr,"balance_eur":balance_eur,"balance_pkr":balance_pkr,"current_stage":current_stage}


@app.route("/add-client", methods=["GET","POST"])
@login_required
@management_required
def add_client():
    if request.method == "POST":
        d = request.form
        group_size = int(d.get("group_size") or 1)
        partners = [d.get(f"partner_{i}","").strip() for i in range(1, group_size) if d.get(f"partner_{i}","").strip()]
        date = d.get("date") or datetime.today().strftime("%Y-%m-%d")
        with db() as con:
            con.execute("""INSERT INTO clients(date,visa_type,section_type,group_size,main_investor,partner_names,reference_type,reference_name,partner_office_name,director_name,issue_status,issue_details,status,remarks,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                date, d.get("visa_type"), d.get("section_type"), group_size, d.get("main_investor"), json.dumps(partners, ensure_ascii=False),
                d.get("reference_type"), d.get("reference_name"), d.get("partner_office_name"), d.get("director_name"), d.get("issue_status"),
                d.get("issue_details"), "Active", d.get("remarks"), now(), now()
            ))
            # Create blank processing row
            con.execute("""INSERT INTO processing_sheet(client_name,program,processing_start_date,nif,personal_bank_account,company_name,company_formation,company_bank_account,business_plan,personal_funds_transferred,application_ready,funds_transfer,application_submission,decision,status,delay,remarks,updated_by,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                d.get("main_investor"), d.get("visa_type"), date, "Pending","Pending","Pending","Pending","Pending","Pending","Pending","Pending","Pending","Pending","Pending","Pending","Client end",d.get("remarks"), session.get("username"), now(), now()
            ))
            con.commit()
        audit("client_added", d.get("main_investor",""))
        return redirect(url_for("processing_sheet"))
    return render_template("add_client.html")


def payment_stage_targets(total):
    total = safe_float(total)
    return {
        "Stage 1 - 70%": round(total * 0.70, 6),
        "Stage 2 - 15%": round(total * 0.15, 6),
        "Stage 3 - 15%": round(total * 0.15, 6),
    }

def payment_stage_received(row, stage):
    if stage == "Stage 1 - 70%":
        return safe_float(row["stage1_eur"])
    if stage == "Stage 2 - 15%":
        return safe_float(row["stage2_eur"])
    if stage == "Stage 3 - 15%":
        return safe_float(row["stage3_eur"])
    return 0.0

def next_stage_from_existing(row):
    total = safe_float(row["total_eur"])
    if total <= 0:
        return "Stage 1 - 70%"
    if safe_float(row["balance_eur"]) <= 0:
        return "Complete"
    targets = payment_stage_targets(total)
    if safe_float(row["stage1_eur"]) < targets["Stage 1 - 70%"]:
        return "Stage 1 - 70%"
    if safe_float(row["stage2_eur"]) < targets["Stage 2 - 15%"]:
        return "Stage 2 - 15%"
    if safe_float(row["stage3_eur"]) < targets["Stage 3 - 15%"]:
        return "Stage 3 - 15%"
    return "Complete"

def allowed_client_stage(total, received_eur):
    total = safe_float(total)
    rec = safe_float(received_eur)
    if total <= 0:
        return "Stage 1 - 70%"
    if rec < total * 0.70:
        return "Stage 1 - 70%"
    if rec < total * 0.85:
        return "Stage 2 - 15%"
    if rec < total:
        return "Stage 3 - 15%"
    return "Complete"

def validate_payment_request(base, stage, amount_eur):
    allowed = next_stage_from_existing(base)
    if allowed == "Complete":
        return False, "This payment account is already complete.", allowed, 0
    if stage != allowed:
        return False, f"Stage locked. Allowed stage: {allowed}", allowed, 0
    if amount_eur <= 0:
        return False, "Received EUR must be greater than 0.", allowed, 0
    targets = payment_stage_targets(base["total_eur"])
    already = payment_stage_received(base, stage)
    remaining_stage = max(targets[stage] - already, 0)
    remaining_total = max(safe_float(base["balance_eur"]), 0)
    max_allowed = min(remaining_stage, remaining_total)
    if amount_eur > max_allowed + 0.0001:
        return False, f"Amount exceeds allowed stage balance. Maximum allowed: {max_allowed:.2f} EUR", allowed, max_allowed
    return True, "OK", allowed, max_allowed

def allowed_share_stage(share_type,total,paid_eur):
    total=float(total or 0); paid=float(paid_eur or 0); st=share_type or ""
    if "Lawyer" in st or "Migration" in st:
        if paid < total*0.70: return "Stage 1 - 70%"
        if paid < total: return "Stage 2 - 30%"
        return "Complete"
    if "FS" in st:
        if paid < total*0.70: return "Stage 1 - 70%"
        if paid < total*0.85: return "Stage 2 - 15%"
        if paid < total: return "Stage 3 - 15%"
        return "Complete"
    return "Manual Transfer"




@app.route("/new-payment", methods=["GET","POST"])
@login_required
@accounts_or_management
def new_payment():
    if request.method == "POST":
        d = request.form
        cp_id = d.get("client_payment_id")
        amount_eur = safe_float(d.get("received_eur"))
        roe = safe_float(d.get("roe"))
        amount_pkr = amount_eur * roe
        updated_by = d.get("updated_by") or session.get("name") or session.get("username")
        remarks = d.get("remarks")
        stage = d.get("stage")

        with db() as con:
            base = con.execute("SELECT * FROM client_payments WHERE id=?", (cp_id,)).fetchone()
            if not base:
                flash("Existing payment account select karo.")
                return redirect(url_for("new_payment"))

            ok, msg, allowed, max_allowed = validate_payment_request(base, stage, amount_eur)
            if not ok:
                flash(msg)
                return redirect(url_for("new_payment"))

            if roe <= 0:
                flash("Payment ROE required hai.")
                return redirect(url_for("new_payment"))

            if session.get("role") == "accounts":
                con.execute("""
                    INSERT INTO payment_approval_requests(
                        request_type, client_payment_id, payment_date, stage, received_eur,
                        roe, received_pkr, requested_by, remarks, status, created_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    "add", cp_id, d.get("payment_date"), stage, amount_eur,
                    roe, amount_pkr, updated_by, remarks, "Pending", now()
                ))
                con.commit()
                audit("payment_request_sent", f"{base['client_name']} | {stage} | {amount_eur} EUR | {updated_by}")
                flash("Payment request management approval ke liye send ho gai.")
                return redirect(url_for("new_payment"))

            apply_payment_update(con, cp_id, d.get("payment_date"), stage, amount_eur, roe, amount_pkr, updated_by, remarks)
            con.commit()
            audit("payment_added_direct", f"{base['client_name']} | {stage} | {amount_eur} EUR | {updated_by}")

        return redirect(url_for("new_payment"))

    with db() as con:
        accounts = con.execute("SELECT * FROM client_payments ORDER BY id").fetchall()
        clients = con.execute("SELECT main_investor, visa_type FROM clients ORDER BY main_investor").fetchall()
        history = con.execute("""
            SELECT u.*, p.client_name
            FROM payment_updates u
            JOIN client_payments p ON p.id = u.client_payment_id
            ORDER BY u.id DESC
            LIMIT 100
        """).fetchall()
        pending_requests = con.execute("""
            SELECT r.*, p.client_name, p.visa, p.inv, p.total_eur, p.balance_eur, p.balance_pkr
            FROM payment_approval_requests r
            JOIN client_payments p ON p.id=r.client_payment_id
            WHERE r.status='Pending'
            ORDER BY r.id DESC
        """).fetchall() if session.get("role") in ["management", "server"] else []

    rows = []
    payment_data = []
    for a in accounts:
        received_eur = safe_float(a["stage1_eur"]) + safe_float(a["stage2_eur"]) + safe_float(a["stage3_eur"])
        allowed = next_stage_from_existing(a)
        targets = payment_stage_targets(a["total_eur"])
        stage_remaining = 0 if allowed == "Complete" else max(targets[allowed] - payment_stage_received(a, allowed), 0)
        rows.append({"a": a, "received_eur": received_eur, "allowed": allowed, "stage_remaining": stage_remaining})
        payment_data.append({
            "id": a["id"],
            "client_name": a["client_name"] or "",
            "visa": a["visa"] or "",
            "inv": a["inv"] or "",
            "total_eur": safe_float(a["total_eur"]),
            "invoice_pkr": safe_float(a["invoice_pkr"]),
            "stage1_eur": safe_float(a["stage1_eur"]),
            "stage2_eur": safe_float(a["stage2_eur"]),
            "stage3_eur": safe_float(a["stage3_eur"]),
            "stage1_target": targets["Stage 1 - 70%"],
            "stage2_target": targets["Stage 2 - 15%"],
            "stage3_target": targets["Stage 3 - 15%"],
            "received_eur": received_eur,
            "received_pkr": safe_float(a["received_pkr"]),
            "received_percent": safe_float(a["received_percent"]),
            "balance_eur": safe_float(a["balance_eur"]),
            "balance_pkr": safe_float(a["balance_pkr"]),
            "payment_stage": a["payment_stage"] or "Pending",
            "allowed": allowed,
            "stage_remaining": stage_remaining
        })

    return render_template(
        "new_payment.html",
        rows=rows,
        clients=clients,
        history=history,
        payment_data=payment_data,
        pending_requests=pending_requests
    )

def apply_payment_update(con, cp_id, payment_date, stage, amount_eur, roe, amount_pkr, updated_by, remarks):
    backup_database_file("before_payment_update")
    base = con.execute("SELECT * FROM client_payments WHERE id=?", (cp_id,)).fetchone()
    if not base:
        return

    ok, msg, allowed, max_allowed = validate_payment_request(base, stage, amount_eur)
    if not ok:
        raise ValueError(msg)

    s1 = safe_float(base["stage1_eur"])
    r1 = safe_float(base["stage1_roe"])
    s2 = safe_float(base["stage2_eur"])
    r2 = safe_float(base["stage2_roe"])
    s3 = safe_float(base["stage3_eur"])
    r3 = safe_float(base["stage3_roe"])

    if stage == "Stage 1 - 70%":
        s1 += amount_eur
        r1 = roe
    elif stage == "Stage 2 - 15%":
        s2 += amount_eur
        r2 = roe
    elif stage == "Stage 3 - 15%":
        s3 += amount_eur
        r3 = roe
    else:
        raise ValueError("Invalid payment stage")

    received_eur = s1 + s2 + s3
    total_eur = safe_float(base["total_eur"])
    received_pkr = safe_float(base["received_pkr"]) + amount_pkr
    balance_pkr = safe_float(base["balance_pkr"]) - amount_pkr
    balance_eur = max(total_eur - received_eur, 0)
    percent = (received_eur / total_eur * 100) if total_eur else 0

    temp_row = dict(base)
    temp_row["stage1_eur"] = s1
    temp_row["stage2_eur"] = s2
    temp_row["stage3_eur"] = s3
    temp_row["balance_eur"] = balance_eur
    next_stage = next_stage_from_existing(temp_row)
    pay_stage = "Complete" if next_stage == "Complete" else next_stage.replace(" - ", " Pending ")

    con.execute("""
        INSERT INTO payment_updates(
            client_payment_id, payment_date, stage, received_eur, roe,
            received_pkr, updated_by, remarks, created_at
        )
        VALUES(?,?,?,?,?,?,?,?,?)
    """, (cp_id, payment_date, stage, amount_eur, roe, amount_pkr, updated_by, remarks, now()))

    con.execute("""
        UPDATE client_payments
        SET stage1_eur=?, stage1_roe=?, stage2_eur=?, stage2_roe=?,
            stage3_eur=?, stage3_roe=?, received_pkr=?, received_percent=?,
            balance_pkr=?, balance_eur=?, payment_stage=?, remarks=?,
            updated_by=?, updated_at=?
        WHERE id=?
    """, (
        s1, r1, s2, r2, s3, r3, received_pkr, percent,
        balance_pkr, balance_eur, pay_stage, remarks or base["remarks"],
        updated_by, now(), cp_id
    ))

@app.post("/payment-request/<int:req_id>/<decision>")
@login_required
@management_required
def decide_payment_request(req_id, decision):
    note = request.form.get("management_note", "")
    with db() as con:
        req = con.execute("SELECT * FROM payment_approval_requests WHERE id=?", (req_id,)).fetchone()
        if not req or req["status"] != "Pending":
            flash("Request not found or already decided.")
            return redirect(url_for("new_payment"))

        base = con.execute("SELECT * FROM client_payments WHERE id=?", (req["client_payment_id"],)).fetchone()
        if not base:
            flash("Payment account not found.")
            return redirect(url_for("new_payment"))

        if decision == "approve":
            ok, msg, allowed, max_allowed = validate_payment_request(base, req["stage"], req["received_eur"])
            if not ok:
                con.execute("""
                    UPDATE payment_approval_requests
                    SET status='Rejected', management_note=?, decided_by=?, decided_at=?
                    WHERE id=?
                """, (f"Auto rejected: {msg}", session.get("username"), now(), req_id))
                con.commit()
                flash(f"Request auto rejected: {msg}")
                return redirect(url_for("new_payment"))

            apply_payment_update(
                con,
                req["client_payment_id"],
                req["payment_date"],
                req["stage"],
                req["received_eur"],
                req["roe"],
                req["received_pkr"],
                req["requested_by"],
                req["remarks"]
            )
            status = "Approved"
            audit("payment_request_approved", f"{base['client_name']} | {req['stage']} | {req['received_eur']} EUR")
        else:
            status = "Rejected"
            audit("payment_request_rejected", f"{base['client_name']} | {req['stage']} | {req['received_eur']} EUR")

        con.execute("""
            UPDATE payment_approval_requests
            SET status=?, management_note=?, decided_by=?, decided_at=?
            WHERE id=?
        """, (status, note, session.get("username"), now(), req_id))
        con.commit()

    flash(f"Payment request {status.lower()}.")
    return redirect(url_for("new_payment"))


@app.route("/transfer-shares", methods=["GET","POST"])
@login_required
@accounts_or_management
def transfer_shares():
    if request.method == "POST":
        d = request.form
        transfer_eur = float(d.get("transfer_eur") or 0)
        roe = float(d.get("roe") or 0)
        transfer_pkr = transfer_eur * roe
        sp_id = d.get("share_payment_id")
        with db() as con:
            if d.get("mode") == "create":
                total_eur = float(d.get("total_eur") or 0)
                inv_roe = float(d.get("roe_invoice") or 0)
                con.execute("""INSERT INTO share_payments(share_type,date,visa,inv,client_name,roe_invoice,total_eur,total_pkr,paid_pkr,paid_percent,balance_pkr,balance_eur,status,remarks,updated_by,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    d.get("share_type"), d.get("transfer_date"), d.get("visa"), d.get("inv"), d.get("client_name"), inv_roe, total_eur, total_eur*inv_roe, 0, 0, total_eur*inv_roe, total_eur, "Pending", d.get("remarks"), d.get("updated_by"), now(), now()
                ))
                sp_id = con.last_id()
            base_check = con.execute("SELECT * FROM share_payments WHERE id=?", (sp_id,)).fetchone()
            allowed = allowed_share_stage(base_check["share_type"], base_check["total_eur"], (base_check["total_eur"] or 0) - (base_check["balance_eur"] or 0))
            if allowed != "Complete" and d.get("stage") != allowed:
                flash(f"Stage locked. Pehle {allowed} complete karo.")
                return redirect(url_for("transfer_shares"))
            if allowed == "Complete":
                flash("Share payment already complete.")
                return redirect(url_for("transfer_shares"))
            con.execute("""INSERT INTO share_updates(share_payment_id,transfer_date,stage,transfer_eur,roe,transfer_pkr,updated_by,remarks,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)""", (sp_id, d.get("transfer_date"), d.get("stage"), transfer_eur, roe, transfer_pkr, d.get("updated_by"), d.get("remarks"), now()))
            rec = con.execute("SELECT COALESCE(SUM(transfer_eur),0) eur, COALESCE(SUM(transfer_pkr),0) pkr FROM share_updates WHERE share_payment_id=?", (sp_id,)).fetchone()
            base = con.execute("SELECT * FROM share_payments WHERE id=?", (sp_id,)).fetchone()
            total_eur = base["total_eur"] or 0
            total_pkr = base["total_pkr"] or 0
            balance_eur = max(total_eur - rec["eur"], 0)
            balance_pkr = max(total_pkr - rec["pkr"], 0)
            percent = (rec["eur"]/total_eur*100) if total_eur else 0
            con.execute("UPDATE share_payments SET paid_pkr=?, paid_percent=?, balance_pkr=?, balance_eur=?, status=?, updated_by=?, updated_at=? WHERE id=?",
                (rec["pkr"], percent, balance_pkr, balance_eur, "Complete" if balance_eur<=0 else "Pending", d.get("updated_by"), now(), sp_id))
            con.commit()
        audit("share_transfer", str(sp_id))
        return redirect(url_for("payments_sheet"))
    with db() as con:
        shares = con.execute("SELECT * FROM share_payments ORDER BY share_type, client_name").fetchall()
        clients = con.execute("SELECT main_investor, visa_type FROM clients ORDER BY main_investor").fetchall()
    return render_template("transfer_shares.html", shares=shares, clients=clients)

@app.route("/processing-sheet", methods=["GET","POST"])
@login_required
def processing_sheet():
    if request.method == "POST":
        d = request.form
        row_id = d.get("id")
        fields = ["nif","personal_bank_account","company_name","company_formation","company_bank_account","business_plan","personal_funds_transferred","application_ready","funds_transfer","application_submission","decision","status","delay","remarks"]
        sets = ",".join([f"{f}=?" for f in fields]) + ", updated_by=?, updated_at=?"
        vals = [d.get(f) for f in fields] + [session.get("username"), now(), row_id]
        with db() as con:
            con.execute(f"UPDATE processing_sheet SET {sets} WHERE id=?", vals)
            con.commit()
        audit("processing_updated", row_id)
        return redirect(url_for("processing_sheet"))
    with db() as con:
        rows = con.execute("SELECT * FROM processing_sheet ORDER BY id").fetchall()
    return render_template("processing_sheet.html", rows=rows)

@app.get("/payments-sheet")
@login_required
@accounts_or_management
def payments_sheet():
    with db() as con:
        payments = con.execute("SELECT * FROM client_payments ORDER BY id").fetchall()
        fs = con.execute("SELECT * FROM share_payments WHERE share_type LIKE '%FS%' ORDER BY id").fetchall()
        lawyer = con.execute("SELECT * FROM share_payments WHERE share_type LIKE '%Lawyer%' OR share_type LIKE '%Migration%' ORDER BY id").fetchall()
        other = con.execute("SELECT * FROM share_payments WHERE share_type NOT LIKE '%FS%' AND share_type NOT LIKE '%Lawyer%' AND share_type NOT LIKE '%Migration%' ORDER BY share_type,id").fetchall()
    return render_template("payments_sheet.html", payments=payments, fs=fs, lawyer=lawyer, other=other)


@app.route("/add-work", methods=["GET","POST"])
@login_required
@management_required
def add_work():
    return render_template("simple_table.html", title="Add New Work", rows=[])

@app.get("/client-master")
@login_required
@management_required
def client_master():
    with db() as con:
        rows=con.execute("SELECT * FROM clients ORDER BY id DESC").fetchall()
    return render_template("simple_table.html", title="Client Master", rows=rows)

@app.get("/marketing-work")
@login_required
@management_required
def marketing_work():
    return render_template("simple_table.html", title="Marketing Work", rows=[])

@app.get("/all-work-tasks")
@login_required
@management_required
def all_work_tasks():
    return render_template("simple_table.html", title="All Work Tasks", rows=[])

@app.get("/payment-history")
@login_required
@accounts_or_management
def payment_history():
    with db() as con:
        rows=con.execute("SELECT * FROM payment_updates ORDER BY id DESC").fetchall()
    return render_template("simple_table.html", title="Payment History", rows=rows)

@app.get("/export/<table>")
@login_required
def export_table(table):
    allowed = {"clients","processing_sheet","client_payments","share_payments","payment_updates","share_updates","leads"}
    if table not in allowed:
        return "Invalid", 400
    if session.get("role") == "accounts" and table not in {"client_payments","share_payments","payment_updates","share_updates"}:
        return "Access denied", 403
    with db() as con:
        rows = con.execute(f"SELECT * FROM {table}").fetchall()
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        for r in rows:
            writer.writerow(dict(r))
    mem = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name=f"{table}.csv")

@app.post("/backup")
@login_required
@management_required
def backup():
    sqlite_backup = backup_database_file("manual")
    data = {}
    with db() as con:
        for t in ["clients","processing_sheet","client_payments","share_payments","payment_updates","share_updates","payment_approval_requests","audit_log"]:
            if table_exists(con, t):
                data[t] = [dict(r) for r in con.execute(f"SELECT * FROM {t}").fetchall()]
    path = BACKUP_DIR / f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    data["_sqlite_backup"] = sqlite_backup
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    audit("manual_backup", str(path))
    return send_file(path, as_attachment=True)



@app.get("/backups")
@login_required
@management_required
def backups_page():
    files = sorted(BACKUP_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    rows = [{"name": f.name, "size": f.stat().st_size, "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")} for f in files if f.is_file()]
    return render_template("backups.html", rows=rows)

@app.get("/backups/download/<path:name>")
@login_required
@management_required
def download_backup_file(name):
    return send_from_directory(BACKUP_DIR, name, as_attachment=True)

@app.post("/backups/create")
@login_required
@management_required
def create_backup_now():
    backup_database_file("manual_button")
    audit("manual_sqlite_backup", session.get("username", ""))
    return redirect(url_for("backups_page"))


# === FLAIR LEAD MANAGEMENT V1 ===

def ensure_lead_columns():
    with db() as con:
        if IS_POSTGRES:
            rows = con.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='public' AND table_name='leads'
            """).fetchall()
            existing = {r["column_name"] for r in rows}
        else:
            rows = con.execute("PRAGMA table_info(leads)").fetchall()
            existing = {r["name"] for r in rows}

        required = {
            "email": "TEXT",
            "original_message": "TEXT",
            "assigned_to": "TEXT",
            "source_reference": "TEXT",
            "converted_client_id": "INTEGER",
            "converted_at": "TEXT",
        }
        for column, column_type in required.items():
            if column not in existing:
                con.execute(f"ALTER TABLE leads ADD COLUMN {column} {column_type}")
        con.commit()


@app.route("/leads", methods=["GET", "POST"])
@login_required
@management_required
def leads_page():
    ensure_lead_columns()
    statuses = ["New Lead", "Contacted", "Qualified", "Follow-up", "Converted", "Lost"]

    if request.method == "POST":
        d = request.form
        name_value = d.get("name", "").strip()
        phone_value = d.get("phone", "").strip()
        email_value = d.get("email", "").strip().lower()
        if not name_value:
            flash("Lead name is required.")
            return redirect(url_for("leads_page"))

        with db() as con:
            duplicate = None
            if phone_value:
                duplicate = con.execute(
                    "SELECT id,name FROM leads WHERE phone=? AND lead_status!='Converted' ORDER BY id DESC LIMIT 1",
                    (phone_value,),
                ).fetchone()
            if not duplicate and email_value:
                duplicate = con.execute(
                    "SELECT id,name FROM leads WHERE lower(email)=? AND lead_status!='Converted' ORDER BY id DESC LIMIT 1",
                    (email_value,),
                ).fetchone()
            if duplicate:
                flash(f"Possible duplicate lead exists: #{duplicate['id']} {duplicate['name']}")
                return redirect(url_for("leads_page"))

            con.execute("""
                INSERT INTO leads(
                    lead_date,name,phone,email,city,source,country_interest,
                    program_interest,lead_status,next_followup,remarks,
                    original_message,assigned_to,source_reference,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                d.get("lead_date") or datetime.today().strftime("%Y-%m-%d"),
                name_value, phone_value, email_value, d.get("city", "").strip(),
                d.get("source", "Manual"), d.get("country_interest", "").strip(),
                d.get("program_interest", "").strip(), d.get("lead_status", "New Lead"),
                d.get("next_followup", ""), d.get("remarks", ""),
                d.get("original_message", ""), d.get("assigned_to", ""),
                d.get("source_reference", ""), now(), now(),
            ))
            con.commit()
        audit("lead_added", f"{name_value} | {d.get('source','Manual')}")
        flash("Lead saved successfully.")
        return redirect(url_for("leads_page"))

    q = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip()
    clauses = ["1=1"]
    params = []
    if q:
        pattern = f"%{q.lower()}%"
        clauses.append("(lower(name) LIKE ? OR lower(COALESCE(phone,'')) LIKE ? OR lower(COALESCE(email,'')) LIKE ? OR lower(COALESCE(program_interest,'')) LIKE ?)")
        params.extend([pattern, pattern, pattern, pattern])
    if status_filter:
        clauses.append("lead_status=?")
        params.append(status_filter)

    with db() as con:
        rows = con.execute(
            f"SELECT * FROM leads WHERE {' AND '.join(clauses)} ORDER BY id DESC",
            params,
        ).fetchall()
    return render_template("leads.html", rows=rows, statuses=statuses, q=q, status_filter=status_filter)


@app.post("/leads/<int:lead_id>/update")
@login_required
@management_required
def update_lead(lead_id):
    ensure_lead_columns()
    d = request.form
    with db() as con:
        lead = con.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not lead:
            flash("Lead not found.")
            return redirect(url_for("leads_page"))
        con.execute("""
            UPDATE leads SET lead_status=?,next_followup=?,assigned_to=?,remarks=?,updated_at=?
            WHERE id=?
        """, (
            d.get("lead_status", lead["lead_status"]),
            d.get("next_followup", lead["next_followup"]),
            d.get("assigned_to", lead["assigned_to"]),
            d.get("remarks", lead["remarks"]), now(), lead_id,
        ))
        con.commit()
    audit("lead_updated", str(lead_id))
    flash("Lead updated successfully.")
    return redirect(url_for("leads_page"))


@app.post("/leads/<int:lead_id>/convert")
@login_required
@management_required
def convert_lead_to_client(lead_id):
    ensure_lead_columns()
    with db() as con:
        lead = con.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not lead:
            flash("Lead not found.")
            return redirect(url_for("leads_page"))
        if lead["converted_client_id"]:
            flash(f"Lead already converted to client #{lead['converted_client_id']}.")
            return redirect(url_for("leads_page"))

        duplicate = con.execute(
            "SELECT id FROM clients WHERE lower(main_investor)=? ORDER BY id DESC LIMIT 1",
            ((lead["name"] or "").strip().lower(),),
        ).fetchone()
        if duplicate:
            flash(f"A client with this name already exists: #{duplicate['id']}")
            return redirect(url_for("leads_page"))

        program = lead["program_interest"] or lead["country_interest"] or "Other"
        start_date = lead["lead_date"] or datetime.today().strftime("%Y-%m-%d")
        con.execute("""
            INSERT INTO clients(
                date,visa_type,section_type,group_size,main_investor,partner_names,
                reference_type,reference_name,partner_office_name,director_name,
                issue_status,issue_details,status,remarks,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            start_date, program, "Single", 1, lead["name"], "[]", "Other",
            lead["source"] or "Lead", "", "", "No", "", "Active",
            f"Converted from lead #{lead_id}. {lead['remarks'] or ''}".strip(), now(), now(),
        ))
        client_id = con.last_id()
        con.execute("""
            INSERT INTO processing_sheet(
                client_name,program,processing_start_date,nif,personal_bank_account,
                company_name,company_formation,company_bank_account,business_plan,
                personal_funds_transferred,application_ready,funds_transfer,
                application_submission,decision,status,delay,remarks,updated_by,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            lead["name"], program, start_date,
            "Pending","Pending","Pending","Pending","Pending","Pending","Pending",
            "Pending","Pending","Pending","Pending","Pending","Client end",
            f"Converted from lead #{lead_id}", session.get("username"), now(), now(),
        ))
        con.execute("""
            UPDATE leads SET lead_status='Converted',converted_client_id=?,converted_at=?,updated_at=?
            WHERE id=?
        """, (client_id, now(), now(), lead_id))
        con.commit()
    audit("lead_converted", f"lead={lead_id} client={client_id}")
    flash(f"Lead converted successfully to client #{client_id}.")
    return redirect(url_for("leads_page"))

# === END FLAIR LEAD MANAGEMENT V1 ===


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(APP_DIR/"static"/"images", "flair-logo.png")

@app.get("/health")
def health():
    try:
        with db() as con:
            con.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception:
        db_ok = False
    return jsonify({
        "ok": db_ok,
        "database_backend": "postgres" if IS_POSTGRES else "sqlite",
        "database": "postgres (Railway)" if IS_POSTGRES else str(DB_PATH),
        "backups": len(list(BACKUP_DIR.glob("db_backup_*.sqlite")))
    })

if __name__ == "__main__":
    init_db()
    import_seed_once()
    restore_preloaded_exact_once()
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
