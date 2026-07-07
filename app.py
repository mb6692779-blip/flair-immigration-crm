from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, jsonify, send_file, flash
)
from werkzeug.security import generate_password_hash, check_password_hash

APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DATABASE_PATH", APP_DIR / "flair_crm_v2.db"))
BACKUP_DIR = APP_DIR / "backups"
BACKUP_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key-in-railway")

PAYMENT_STATUS = ["Pending", "Partial", "Received", "Refunded", "Cancelled"]
PAYMENT_STAGES_NEW = ["Stage 1 - 70%", "Stage 2 - 15%", "Stage 3 - 15%"]
REFERENCE_TYPES = ["Self", "Partner Office", "Director", "Walk-in", "Other"]
CLIENT_STATUS = ["Active", "Inactive"]
PROCESS_STATUS = [
    "Pending", "In Progress", "Waiting Documents", "Appointment Booked",
    "Biometrics Done", "Under Review", "Visa Approved", "Visa Refused", "Completed"
]
INACTIVE_REASONS = [
    "No Response", "Client Cancelled", "Financial Issue", "Visa Refused",
    "Moved to Another Consultant", "Duplicate", "Other"
]

VISA_TYPES = ["D2", "D7", "D8", "D9", "Golden Visa", "Digital Nomad", "Visit Visa", "Other"]
SECTION_TYPES = ["Single", "Group"]
YES_NO = ["No", "Yes"]
UPDATE_SOURCES = ["My Self", "Manager", "Director", "Team Member", "Accounts", "Other"]
ISSUE_ENDS = ["Client", "Vendor", "Bank", "Lawyer", "Management", "Other"]
SHARE_TYPES = ["FS Lisbon", "Lawyer", "Other Partner / Referral"]
WORK_ROLES = ["GM", "Managing Director", "Director", "Manager", "Team Member", "Other"]
SENT_METHODS = ["TCS", "Leopard", "Email", "Hand", "Other"]
WORK_STATUS = ["Pending", "Complete", "Not Complete", "Missing"]
MARKETING_TOPICS = ["Social Media", "Website", "Paid Promotion", "Content", "Other"]
CONTENT_STATUS = ["Working on it", "Done from my end", "Given to IT", "Pending"]

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_name TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            city TEXT,
            country TEXT,
            program TEXT,
            status TEXT DEFAULT 'Active',
            inactive_reason TEXT,
            remarks TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS processing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            stage TEXT,
            process_status TEXT DEFAULT 'Pending',
            remarks TEXT,
            next_followup TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(client_id) REFERENCES clients(id)
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            payment_date TEXT,
            description TEXT,
            total_amount REAL DEFAULT 0,
            received_amount REAL DEFAULT 0,
            pending_amount REAL DEFAULT 0,
            currency TEXT DEFAULT 'PKR',
            payment_status TEXT DEFAULT 'Pending',
            remarks TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(client_id) REFERENCES clients(id)
        );

        CREATE TABLE IF NOT EXISTS leads (
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

        CREATE TABLE IF NOT EXISTS client_intake (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visa_type TEXT,
            section_type TEXT,
            group_size INTEGER DEFAULT 1,
            main_investor TEXT,
            partner_names TEXT,
            reference_status TEXT,
            reference_type TEXT,
            partner_office_name TEXT,
            director_name TEXT,
            other_reference TEXT,
            reference_name TEXT,
            issue_status TEXT,
            issue_details TEXT,
            final_remarks TEXT,
            status TEXT DEFAULT 'Active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS payment_stages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_name TEXT,
            visa_type TEXT,
            total_payment REAL DEFAULT 0,
            stage1_required REAL DEFAULT 0,
            stage1_received REAL DEFAULT 0,
            stage1_status TEXT DEFAULT 'Pending',
            stage1_remarks TEXT,
            stage2_required REAL DEFAULT 0,
            stage2_received REAL DEFAULT 0,
            stage2_status TEXT DEFAULT 'Pending',
            stage2_remarks TEXT,
            stage3_required REAL DEFAULT 0,
            stage3_received REAL DEFAULT 0,
            stage3_status TEXT DEFAULT 'Pending',
            stage3_remarks TEXT,
            process_remarks TEXT,
            sheet_updated TEXT DEFAULT 'No',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS share_stages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            share_type TEXT,
            client_name TEXT,
            total_share REAL DEFAULT 0,
            stage1_required REAL DEFAULT 0,
            stage1_paid REAL DEFAULT 0,
            stage1_status TEXT DEFAULT 'Pending',
            stage1_remarks TEXT,
            stage2_required REAL DEFAULT 0,
            stage2_paid REAL DEFAULT 0,
            stage2_status TEXT DEFAULT 'Pending',
            stage2_remarks TEXT,
            stage3_required REAL DEFAULT 0,
            stage3_paid REAL DEFAULT 0,
            stage3_status TEXT DEFAULT 'Pending',
            stage3_remarks TEXT,
            other_department TEXT,
            other_person TEXT,
            manual_remarks TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS visa_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_name TEXT,
            visa_program TEXT,
            update_source TEXT,
            source_name TEXT,
            current_step TEXT,
            moved_forward TEXT,
            issue_end TEXT,
            update_details TEXT,
            sheet_updated TEXT DEFAULT 'No',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS backoffice_work (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_name TEXT,
            document_type TEXT,
            work_given_by_role TEXT,
            work_given_by_name TEXT,
            work_details TEXT,
            internal_status TEXT DEFAULT 'Pending',
            checked_by_role TEXT,
            checked_by_name TEXT,
            sent_method TEXT,
            sent_status TEXT DEFAULT 'Pending',
            external_response TEXT DEFAULT 'Pending',
            external_remarks TEXT,
            final_remarks TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS marketing_work (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_date TEXT,
            meeting_topic TEXT,
            discussion_remarks TEXT,
            content_required TEXT,
            content_status TEXT DEFAULT 'Working on it',
            post_date TEXT,
            program_name TEXT,
            post_status TEXT DEFAULT 'Pending',
            post_remarks TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS payment_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_name TEXT NOT NULL,
            visa_type TEXT,
            inv TEXT,
            total_eur REAL DEFAULT 0,
            invoice_roe REAL DEFAULT 0,
            invoice_pkr REAL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS payment_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_account_id INTEGER NOT NULL,
            payment_date TEXT,
            payment_stage TEXT,
            received_eur REAL DEFAULT 0,
            roe REAL DEFAULT 0,
            received_pkr REAL DEFAULT 0,
            updated_by TEXT,
            remarks TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(payment_account_id) REFERENCES payment_accounts(id)
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            action TEXT NOT NULL,
            details TEXT,
            created_at TEXT NOT NULL
        );
        """)
        now = datetime.now().isoformat(timespec="seconds")
        users = [
            ("management", "management2011", "management", "Management"),
            ("accounts", "accounts123", "accounts", "Accounts Team"),
        ]
        for username, password, role, name in users:
            exists = con.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            if not exists:
                con.execute(
                    "INSERT INTO users(username,password_hash,role,name,created_at) VALUES(?,?,?,?,?)",
                    (username, generate_password_hash(password), role, name, now)
                )
        con.commit()

def audit(action, details=""):
    try:
        with db() as con:
            con.execute(
                "INSERT INTO audit_log(username,action,details,created_at) VALUES(?,?,?,?)",
                (session.get("username", "system"), action, details, datetime.now().isoformat(timespec="seconds"))
            )
            con.commit()
    except Exception:
        pass


def import_seed_backup_once():
    """Import old CRM JSON backup once into the V2 SQLite database."""
    seed_path = APP_DIR / "seed_data" / "flair_backup.json"
    marker_path = APP_DIR / "seed_data" / ".imported"
    if marker_path.exists() or not seed_path.exists():
        return

    try:
        data = json.loads(seed_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print("Seed import failed:", exc)
        return

    now = datetime.now().isoformat(timespec="seconds")

    def safe(row, index, default=""):
        try:
            value = row[index]
            return "" if value is None else str(value)
        except Exception:
            return default

    with db() as con:
        existing_clients = con.execute("SELECT COUNT(*) c FROM clients").fetchone()["c"]
        if existing_clients == 0:
            client_rows = data.get("clientMaster", {}).get("rows", [])
            seen = set()
            for row in client_rows:
                client_name = safe(row, 1).strip()
                if not client_name or client_name.lower() in seen:
                    continue
                seen.add(client_name.lower())
                con.execute("""
                    INSERT INTO clients(client_name, phone, email, city, country, program, status, inactive_reason, remarks, created_at, updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    client_name, "", "", "", "", safe(row, 2), "Active", "", safe(row, 10), now, now
                ))

        existing_payments = con.execute("SELECT COUNT(*) c FROM payments").fetchone()["c"]
        if existing_payments == 0:
            payment_rows = data.get("payments", {}).get("rows", [])
            for row in payment_rows:
                client_name = safe(row, 3).strip()
                client_id = None
                if client_name:
                    found = con.execute("SELECT id FROM clients WHERE lower(client_name)=lower(?)", (client_name,)).fetchone()
                    if not found:
                        con.execute("""
                            INSERT INTO clients(client_name, phone, email, city, country, program, status, inactive_reason, remarks, created_at, updated_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        """, (client_name, "", "", "", "", safe(row, 1), "Active", "", "", now, now))
                        client_id = con.execute("SELECT last_insert_rowid() id").fetchone()["id"]
                    else:
                        client_id = found["id"]

                def num(v):
                    try:
                        if v in ("", None):
                            return 0.0
                        return float(str(v).replace(",", "").replace("%", ""))
                    except Exception:
                        return 0.0

                con.execute("""
                    INSERT INTO payments(client_id,payment_date,description,total_amount,received_amount,pending_amount,currency,payment_status,remarks,created_by,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    client_id,
                    safe(row, 0),
                    f"{safe(row, 2)} - {client_name}".strip(" -"),
                    num(safe(row, 6)),
                    num(safe(row, 13)),
                    num(safe(row, 15)),
                    "PKR",
                    safe(row, 17) or "Pending",
                    safe(row, 18),
                    "seed_import",
                    now,
                    now
                ))

        existing_process = con.execute("SELECT COUNT(*) c FROM processing").fetchone()["c"]
        if existing_process == 0:
            process_rows = data.get("process", {}).get("rows", [])
            for row in process_rows:
                client_name = safe(row, 0).strip()
                client_id = None
                if client_name:
                    found = con.execute("SELECT id FROM clients WHERE lower(client_name)=lower(?)", (client_name,)).fetchone()
                    if found:
                        client_id = found["id"]
                    else:
                        con.execute("""
                            INSERT INTO clients(client_name, phone, email, city, country, program, status, inactive_reason, remarks, created_at, updated_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        """, (client_name, "", "", "", "", safe(row, 1), "Active", "", "", now, now))
                        client_id = con.execute("SELECT last_insert_rowid() id").fetchone()["id"]

                status = safe(row, 12) or "Pending"
                if status.lower() == "ok":
                    status = "Completed"
                elif status.lower() == "pending":
                    status = "Pending"

                con.execute("""
                    INSERT INTO processing(client_id,stage,process_status,remarks,next_followup,updated_at)
                    VALUES(?,?,?,?,?,?)
                """, (
                    client_id,
                    safe(row, 12) or safe(row, 10) or "Processing",
                    status,
                    f"Delay: {safe(row,13)} | Remarks: {safe(row,14)}",
                    "",
                    now
                ))

        # Preserve old raw sheets in audit log as reference.
        con.execute(
            "INSERT INTO audit_log(username,action,details,created_at) VALUES(?,?,?,?)",
            ("system", "seed_backup_imported", "Old CRM backup imported into V2 database", now)
        )
        con.commit()

    marker_path.write_text(now, encoding="utf-8")


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def management_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get("role") != "management":
            flash("Management access required.")
            return redirect(url_for("dashboard"))
        return fn(*args, **kwargs)
    return wrapper

def accounts_or_management(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get("role") not in ["management", "accounts"]:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

@app.context_processor
def inject_globals():
    return dict(
        role=session.get("role"),
        name=session.get("name"),
        CLIENT_STATUS=CLIENT_STATUS,
        PROCESS_STATUS=PROCESS_STATUS,
        PAYMENT_STATUS=PAYMENT_STATUS,
        INACTIVE_REASONS=INACTIVE_REASONS,
        VISA_TYPES=VISA_TYPES,
        SECTION_TYPES=SECTION_TYPES,
        YES_NO=YES_NO,
        UPDATE_SOURCES=UPDATE_SOURCES,
        ISSUE_ENDS=ISSUE_ENDS,
        SHARE_TYPES=SHARE_TYPES,
        WORK_ROLES=WORK_ROLES,
        SENT_METHODS=SENT_METHODS,
        WORK_STATUS=WORK_STATUS,
        MARKETING_TOPICS=MARKETING_TOPICS,
        CONTENT_STATUS=CONTENT_STATUS,
        PAYMENT_STAGES_NEW=PAYMENT_STAGES_NEW,
        REFERENCE_TYPES=REFERENCE_TYPES,
    )

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        with db() as con:
            user = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["name"] = user["name"]
            audit("login", username)
            return redirect(url_for("dashboard"))
        flash("Wrong username or password.")
    return render_template("login.html")

@app.get("/logout")
def logout():
    audit("logout", session.get("username", ""))
    session.clear()
    return redirect(url_for("login"))

@app.get("/")
@login_required
def dashboard():
    if session.get("role") == "accounts":
        return redirect(url_for("payments"))
    with db() as con:
        total_clients = con.execute("SELECT COUNT(*) c FROM clients").fetchone()["c"]
        active_clients = con.execute("SELECT COUNT(*) c FROM clients WHERE status='Active'").fetchone()["c"]
        total_leads = con.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]
        pending_process = con.execute("SELECT COUNT(*) c FROM processing WHERE process_status NOT IN ('Completed','Visa Approved')").fetchone()["c"]
        money = con.execute("SELECT COALESCE(SUM(total_amount),0) total, COALESCE(SUM(received_amount),0) received, COALESCE(SUM(pending_amount),0) pending FROM payments").fetchone()
        recent_clients = con.execute("SELECT * FROM clients ORDER BY id DESC LIMIT 8").fetchall()
        recent_payments = con.execute("""SELECT p.*, c.client_name FROM payments p LEFT JOIN clients c ON c.id=p.client_id ORDER BY p.id DESC LIMIT 8""").fetchall()
    return render_template("dashboard.html", total_clients=total_clients, active_clients=active_clients, total_leads=total_leads, pending_process=pending_process, money=money, recent_clients=recent_clients, recent_payments=recent_payments)

@app.route("/clients", methods=["GET", "POST"])
@login_required
@management_required
def clients():
    if request.method == "POST":
        now = datetime.now().isoformat(timespec="seconds")
        data = request.form
        with db() as con:
            con.execute("""
                INSERT INTO clients(client_name,phone,email,city,country,program,status,inactive_reason,remarks,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (
                data.get("client_name"), data.get("phone"), data.get("email"), data.get("city"),
                data.get("country"), data.get("program"), data.get("status","Active"),
                data.get("inactive_reason"), data.get("remarks"), now, now
            ))
            con.commit()
        audit("client_added", data.get("client_name", ""))
        return redirect(url_for("clients"))
    with db() as con:
        rows = con.execute("SELECT * FROM clients ORDER BY id DESC").fetchall()
    return render_template("clients.html", rows=rows)

@app.route("/clients/<int:id>/edit", methods=["POST"])
@login_required
@management_required
def edit_client(id):
    data = request.form
    now = datetime.now().isoformat(timespec="seconds")
    with db() as con:
        con.execute("""
            UPDATE clients SET client_name=?, phone=?, email=?, city=?, country=?, program=?, status=?, inactive_reason=?, remarks=?, updated_at=?
            WHERE id=?
        """, (
            data.get("client_name"), data.get("phone"), data.get("email"), data.get("city"),
            data.get("country"), data.get("program"), data.get("status"), data.get("inactive_reason"),
            data.get("remarks"), now, id
        ))
        con.commit()
    audit("client_updated", str(id))
    return redirect(url_for("clients"))

@app.route("/processing", methods=["GET", "POST"])
@login_required
@management_required
def processing():
    if request.method == "POST":
        now = datetime.now().isoformat(timespec="seconds")
        data = request.form
        with db() as con:
            con.execute("""
                INSERT INTO processing(client_id,stage,process_status,remarks,next_followup,updated_at)
                VALUES(?,?,?,?,?,?)
            """, (data.get("client_id"), data.get("stage"), data.get("process_status"), data.get("remarks"), data.get("next_followup"), now))
            con.commit()
        audit("process_added", data.get("stage",""))
        return redirect(url_for("processing"))
    with db() as con:
        clients = con.execute("SELECT id, client_name FROM clients ORDER BY client_name").fetchall()
        rows = con.execute("""SELECT pr.*, c.client_name FROM processing pr LEFT JOIN clients c ON c.id=pr.client_id ORDER BY pr.id DESC""").fetchall()
    return render_template("processing.html", rows=rows, clients=clients)

@app.route("/processing/<int:id>/edit", methods=["POST"])
@login_required
@management_required
def edit_processing(id):
    data = request.form
    now = datetime.now().isoformat(timespec="seconds")
    with db() as con:
        con.execute("""
            UPDATE processing SET stage=?, process_status=?, remarks=?, next_followup=?, updated_at=? WHERE id=?
        """, (data.get("stage"), data.get("process_status"), data.get("remarks"), data.get("next_followup"), now, id))
        con.commit()
    audit("process_updated", str(id))
    return redirect(url_for("processing"))

@app.route("/payments", methods=["GET", "POST"])
@login_required
@accounts_or_management
def payments():
    if request.method == "POST":
        now = datetime.now().isoformat(timespec="seconds")
        data = request.form
        total = float(data.get("total_amount") or 0)
        received = float(data.get("received_amount") or 0)
        pending = max(total - received, 0)
        with db() as con:
            con.execute("""
                INSERT INTO payments(client_id,payment_date,description,total_amount,received_amount,pending_amount,currency,payment_status,remarks,created_by,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                data.get("client_id"), data.get("payment_date"), data.get("description"),
                total, received, pending, data.get("currency","PKR"), data.get("payment_status","Pending"),
                data.get("remarks"), session.get("username"), now, now
            ))
            con.commit()
        audit("payment_added", data.get("description",""))
        return redirect(url_for("payments"))
    with db() as con:
        clients = con.execute("SELECT id, client_name FROM clients ORDER BY client_name").fetchall()
        rows = con.execute("""SELECT p.*, c.client_name FROM payments p LEFT JOIN clients c ON c.id=p.client_id ORDER BY p.id DESC""").fetchall()
        totals = con.execute("SELECT COALESCE(SUM(total_amount),0) total, COALESCE(SUM(received_amount),0) received, COALESCE(SUM(pending_amount),0) pending FROM payments").fetchone()
    return render_template("payments.html", rows=rows, clients=clients, totals=totals)

@app.route("/payments/<int:id>/edit", methods=["POST"])
@login_required
@accounts_or_management
def edit_payment(id):
    data = request.form
    total = float(data.get("total_amount") or 0)
    received = float(data.get("received_amount") or 0)
    pending = max(total - received, 0)
    now = datetime.now().isoformat(timespec="seconds")
    with db() as con:
        con.execute("""
            UPDATE payments SET payment_date=?, description=?, total_amount=?, received_amount=?, pending_amount=?, currency=?, payment_status=?, remarks=?, updated_at=?
            WHERE id=?
        """, (data.get("payment_date"), data.get("description"), total, received, pending, data.get("currency"), data.get("payment_status"), data.get("remarks"), now, id))
        con.commit()
    audit("payment_updated", str(id))
    return redirect(url_for("payments"))

@app.route("/leads", methods=["GET", "POST"])
@login_required
@management_required
def leads():
    if request.method == "POST":
        now = datetime.now().isoformat(timespec="seconds")
        data = request.form
        with db() as con:
            con.execute("""
                INSERT INTO leads(lead_date,name,phone,city,source,country_interest,program_interest,lead_status,next_followup,remarks,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                data.get("lead_date"), data.get("name"), data.get("phone"), data.get("city"), data.get("source"),
                data.get("country_interest"), data.get("program_interest"), data.get("lead_status"), data.get("next_followup"),
                data.get("remarks"), now, now
            ))
            con.commit()
        audit("lead_added", data.get("name",""))
        return redirect(url_for("leads"))
    with db() as con:
        rows = con.execute("SELECT * FROM leads ORDER BY id DESC").fetchall()
    return render_template("leads.html", rows=rows)


@app.route("/intake", methods=["GET", "POST"])
@login_required
@management_required
def intake():
    if request.method == "POST":
        now = datetime.now().isoformat(timespec="seconds")
        data = request.form
        group_size = int(data.get("group_size") or 1)
        partners = []
        for i in range(1, group_size):
            val = data.get(f"partner_{i}", "").strip()
            if val:
                partners.append(val)
        with db() as con:
            con.execute("""
                INSERT INTO client_intake(visa_type,section_type,group_size,main_investor,partner_names,reference_status,reference_type,partner_office_name,director_name,other_reference,reference_name,issue_status,issue_details,final_remarks,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                data.get("visa_type"), data.get("section_type"), group_size, data.get("main_investor"),
                json.dumps(partners, ensure_ascii=False), data.get("reference_status"), data.get("reference_type"),
                data.get("partner_office_name"), data.get("director_name"), data.get("other_reference"),
                data.get("reference_name"), data.get("issue_status"), data.get("issue_details"), data.get("final_remarks"), data.get("status","Active"), now, now
            ))

            # Also create main client record if not exists.
            name = data.get("main_investor", "").strip()
            if name:
                found = con.execute("SELECT id FROM clients WHERE lower(client_name)=lower(?)", (name,)).fetchone()
                if not found:
                    con.execute("""
                        INSERT INTO clients(client_name,phone,email,city,country,program,status,inactive_reason,remarks,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """, (name, "", "", "", "", data.get("visa_type"), data.get("status","Active"), "", data.get("final_remarks"), now, now))
            con.commit()
        audit("intake_added", data.get("main_investor",""))
        return redirect(url_for("intake"))

    with db() as con:
        rows = con.execute("SELECT * FROM client_intake ORDER BY id DESC").fetchall()
    return render_template("intake.html", rows=rows)



@app.route("/stage-payments", methods=["GET", "POST"])
@login_required
@accounts_or_management
def stage_payments():
    if request.method == "POST":
        now = datetime.now().isoformat(timespec="seconds")
        data = request.form
        action = data.get("action", "payment_update")

        def num(v):
            try:
                return float(str(v or "0").replace(",", ""))
            except Exception:
                return 0.0

        with db() as con:
            if action == "new_account":
                total_eur = num(data.get("total_eur"))
                invoice_roe = num(data.get("invoice_roe"))
                invoice_pkr = total_eur * invoice_roe
                con.execute("""
                    INSERT INTO payment_accounts(client_name, visa_type, inv, total_eur, invoice_roe, invoice_pkr, created_at, updated_at)
                    VALUES(?,?,?,?,?,?,?,?)
                """, (
                    data.get("client_name"), data.get("visa_type"), data.get("inv"),
                    total_eur, invoice_roe, invoice_pkr, now, now
                ))
                con.commit()
                audit("payment_account_created", data.get("client_name",""))
                return redirect(url_for("stage_payments"))

            account_id = data.get("payment_account_id")
            stage = data.get("payment_stage")
            received_eur = num(data.get("received_eur"))
            roe = num(data.get("roe"))
            received_pkr = received_eur * roe
            updated_by = data.get("updated_by") or session.get("name") or session.get("username")
            con.execute("""
                INSERT INTO payment_updates(payment_account_id,payment_date,payment_stage,received_eur,roe,received_pkr,updated_by,remarks,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)
            """, (
                account_id, data.get("payment_date"), stage, received_eur, roe,
                received_pkr, updated_by, data.get("remarks"), now
            ))
            con.execute("UPDATE payment_accounts SET updated_at=? WHERE id=?", (now, account_id))
            con.commit()
            audit("payment_updated_by_accounts", f"{account_id} | {stage} | {updated_by}")
        return redirect(url_for("stage_payments"))

    with db() as con:
        accounts = con.execute("SELECT * FROM payment_accounts ORDER BY id DESC").fetchall()
        updates = con.execute("SELECT u.*, a.client_name FROM payment_updates u JOIN payment_accounts a ON a.id=u.payment_account_id ORDER BY u.id DESC").fetchall()
        old_clients = con.execute("SELECT client_name, program FROM clients ORDER BY client_name").fetchall()

    summaries = []
    for a in accounts:
        acc_updates = [u for u in updates if u["payment_account_id"] == a["id"]]
        stage_received = {"Stage 1 - 70%": 0.0, "Stage 2 - 15%": 0.0, "Stage 3 - 15%": 0.0}
        total_received_eur = 0.0
        total_received_pkr = 0.0
        for u in acc_updates:
            stage_received[u["payment_stage"]] = stage_received.get(u["payment_stage"], 0.0) + (u["received_eur"] or 0)
            total_received_eur += u["received_eur"] or 0
            total_received_pkr += u["received_pkr"] or 0

        total_eur = a["total_eur"] or 0
        balance_eur = max(total_eur - total_received_eur, 0)
        invoice_pkr = a["invoice_pkr"] or 0
        balance_pkr = max(invoice_pkr - total_received_pkr, 0)
        percent = round((total_received_eur / total_eur * 100), 2) if total_eur else 0

        req1, req2, req3 = total_eur * .70, total_eur * .15, total_eur * .15
        summaries.append({
            "account": a,
            "updates": acc_updates,
            "stage_received": stage_received,
            "req1": req1, "req2": req2, "req3": req3,
            "st1": "Complete" if stage_received.get("Stage 1 - 70%",0) >= req1 and req1 else "Pending",
            "st2": "Complete" if stage_received.get("Stage 2 - 15%",0) >= req2 and req2 else "Pending",
            "st3": "Complete" if stage_received.get("Stage 3 - 15%",0) >= req3 and req3 else "Pending",
            "total_received_eur": total_received_eur,
            "total_received_pkr": total_received_pkr,
            "balance_eur": balance_eur,
            "balance_pkr": balance_pkr,
            "percent": percent
        })

    return render_template("stage_payments.html", summaries=summaries, accounts=accounts, updates=updates, old_clients=old_clients)


@app.route("/shares", methods=["GET", "POST"])
@login_required
@accounts_or_management
def shares():
    if request.method == "POST":
        now = datetime.now().isoformat(timespec="seconds")
        data = request.form
        share_type = data.get("share_type")
        total = float(data.get("total_share") or 0)

        if share_type == "FS Lisbon":
            reqs = (total * 0.70, total * 0.15, total * 0.15)
        elif share_type == "Lawyer":
            reqs = (total * 0.70, total * 0.30, 0)
        else:
            reqs = (0, 0, 0)

        p1 = float(data.get("stage1_paid") or 0)
        p2 = float(data.get("stage2_paid") or 0)
        p3 = float(data.get("stage3_paid") or 0)

        def status(paid, req):
            if req == 0:
                return "N/A"
            return "Complete" if paid >= req else "Pending"

        with db() as con:
            con.execute("""
                INSERT INTO share_stages(share_type,client_name,total_share,stage1_required,stage1_paid,stage1_status,stage1_remarks,stage2_required,stage2_paid,stage2_status,stage2_remarks,stage3_required,stage3_paid,stage3_status,stage3_remarks,other_department,other_person,manual_remarks,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                share_type, data.get("client_name"), total,
                reqs[0], p1, status(p1, reqs[0]), data.get("stage1_remarks"),
                reqs[1], p2, status(p2, reqs[1]), data.get("stage2_remarks"),
                reqs[2], p3, status(p3, reqs[2]), data.get("stage3_remarks"),
                data.get("other_department"), data.get("other_person"), data.get("manual_remarks"), now, now
            ))
            con.commit()
        audit("share_added", f"{share_type} | {data.get('client_name','')}")
        return redirect(url_for("shares"))

    with db() as con:
        rows = con.execute("SELECT * FROM share_stages ORDER BY id DESC").fetchall()
        clients = con.execute("SELECT client_name FROM clients ORDER BY client_name").fetchall()
    return render_template("shares.html", rows=rows, clients=clients)


@app.route("/visa-updates", methods=["GET", "POST"])
@login_required
@management_required
def visa_updates():
    if request.method == "POST":
        now = datetime.now().isoformat(timespec="seconds")
        data = request.form
        with db() as con:
            con.execute("""
                INSERT INTO visa_updates(client_name,visa_program,update_source,source_name,current_step,moved_forward,issue_end,update_details,sheet_updated,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (
                data.get("client_name"), data.get("visa_program"), data.get("update_source"), data.get("source_name"),
                data.get("current_step"), data.get("moved_forward"), data.get("issue_end"), data.get("update_details"),
                data.get("sheet_updated","No"), now, now
            ))
            con.commit()
        audit("visa_update_added", data.get("client_name",""))
        return redirect(url_for("visa_updates"))

    with db() as con:
        rows = con.execute("SELECT * FROM visa_updates ORDER BY id DESC").fetchall()
        clients = con.execute("SELECT client_name, program FROM clients ORDER BY client_name").fetchall()
    return render_template("visa_updates.html", rows=rows, clients=clients)


@app.post("/visa-updates/<int:id>/sheet")
@login_required
@management_required
def mark_visa_sheet(id):
    with db() as con:
        con.execute("UPDATE visa_updates SET sheet_updated='Yes', updated_at=? WHERE id=?", (datetime.now().isoformat(timespec="seconds"), id))
        con.commit()
    audit("visa_sheet_updated", str(id))
    return redirect(url_for("visa_updates"))


@app.route("/backoffice-work", methods=["GET", "POST"])
@login_required
@management_required
def backoffice_work():
    if request.method == "POST":
        now = datetime.now().isoformat(timespec="seconds")
        data = request.form
        with db() as con:
            con.execute("""
                INSERT INTO backoffice_work(client_name,document_type,work_given_by_role,work_given_by_name,work_details,internal_status,checked_by_role,checked_by_name,sent_method,sent_status,external_response,external_remarks,final_remarks,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                data.get("client_name"), data.get("document_type"), data.get("work_given_by_role"), data.get("work_given_by_name"),
                data.get("work_details"), data.get("internal_status"), data.get("checked_by_role"), data.get("checked_by_name"),
                data.get("sent_method"), data.get("sent_status"), data.get("external_response"), data.get("external_remarks"),
                data.get("final_remarks"), now, now
            ))
            con.commit()
        audit("backoffice_work_added", data.get("client_name",""))
        return redirect(url_for("backoffice_work"))

    with db() as con:
        rows = con.execute("SELECT * FROM backoffice_work ORDER BY id DESC").fetchall()
        clients = con.execute("SELECT client_name FROM clients ORDER BY client_name").fetchall()
    return render_template("backoffice_work.html", rows=rows, clients=clients)


@app.route("/marketing-work", methods=["GET", "POST"])
@login_required
@management_required
def marketing_work():
    if request.method == "POST":
        now = datetime.now().isoformat(timespec="seconds")
        data = request.form
        with db() as con:
            con.execute("""
                INSERT INTO marketing_work(meeting_date,meeting_topic,discussion_remarks,content_required,content_status,post_date,program_name,post_status,post_remarks,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (
                data.get("meeting_date"), data.get("meeting_topic"), data.get("discussion_remarks"), data.get("content_required"),
                data.get("content_status"), data.get("post_date"), data.get("program_name"), data.get("post_status"),
                data.get("post_remarks"), now, now
            ))
            con.commit()
        audit("marketing_work_added", data.get("program_name",""))
        return redirect(url_for("marketing_work"))

    with db() as con:
        rows = con.execute("SELECT * FROM marketing_work ORDER BY id DESC").fetchall()
    return render_template("marketing_work.html", rows=rows)

@app.get("/audit")
@login_required
@management_required
def audit_page():
    with db() as con:
        rows = con.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 300").fetchall()
    return render_template("audit.html", rows=rows)

@app.get("/export/<table>")
@login_required
def export_table(table):
    allowed = {"clients", "processing", "payments", "leads", "client_intake", "payment_stages", "share_stages", "visa_updates", "backoffice_work", "marketing_work", "audit_log"}
    if table not in allowed:
        return "Invalid table", 400
    if session.get("role") == "accounts" and table not in ["payments", "payment_stages", "share_stages"]:
        return "Access denied", 403
    with db() as con:
        rows = con.execute(f"SELECT * FROM {table}").fetchall()
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        for r in rows:
            writer.writerow(dict(r))
    else:
        output.write("")
    mem = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name=f"{table}.csv")

@app.post("/backup")
@login_required
@management_required
def backup():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUP_DIR / f"flair_crm_backup_{stamp}.json"
    data = {}
    with db() as con:
        for table in ["clients", "processing", "payments", "leads", "client_intake", "payment_stages", "share_stages", "visa_updates", "backoffice_work", "marketing_work", "audit_log"]:
            rows = con.execute(f"SELECT * FROM {table}").fetchall()
            data[table] = [dict(r) for r in rows]
    backup_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    audit("backup_created", backup_file.name)
    return send_file(backup_file, as_attachment=True)


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(APP_DIR / "static" / "images", "flair-logo.png")


@app.post("/import-old-backup")
@login_required
@management_required
def import_old_backup_route():
    import_seed_backup_once()
    flash("Old backup import checked/completed.")
    return redirect(url_for("dashboard"))

@app.get("/health")
def health():
    return jsonify({"ok": True})

if __name__ == "__main__":
    init_db()
    import_seed_backup_once()
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
