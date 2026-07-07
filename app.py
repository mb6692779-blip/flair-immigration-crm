from __future__ import annotations
import json, os, sqlite3, csv, io
from datetime import datetime
from pathlib import Path
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DATABASE_PATH", APP_DIR / "flair_crm_server.db"))
SEED_PATH = APP_DIR / "seed_data" / "seed.json"
IMPORT_MARKER = APP_DIR / "seed_data" / ".imported"
BACKUP_DIR = APP_DIR / "backups"
BACKUP_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "flair-crm-secret-change-in-railway")

VISA_TYPES = ["D2","D7","D8","D9","Golden Visa","Digital Nomad","Visit Visa","Case Review","Other"]
SECTION_TYPES = ["Single","Group"]
REFERENCE_TYPES = ["Self","Partner Office","Director","Walk-in","Other"]
PROCESS_STATUS = ["ok","OK","Pending","N/A","Appointment","Final Decision","Funds Transfer","Company Name","Company Formation","Company Bank Account","Personal Account","Application Submission","Complete","Not Complete"]
DELAY_ENDS = ["Client end","Vendor end","Vendor Hand","Embassy end","Lawyer end","Bank end","Management end","Other"]
PAYMENT_STAGES = ["Stage 1 - 70%","Stage 2 - 15%","Stage 3 - 15%"]
SHARE_TYPES = ["FS Lisbon","Migration Lawyer","Other Partner"]

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def now():
    return datetime.now().isoformat(timespec="seconds")

def init_db():
    with db() as con:
        con.executescript("""
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
        """)
        for username, password, role, name in [
            ("management", "management2011", "management", "Management"),
            ("accounts", "accounts123", "accounts", "Accounts Team"),
        ]:
            exists = con.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            if not exists:
                con.execute("INSERT INTO users(username,password_hash,role,name,created_at) VALUES(?,?,?,?,?)",
                    (username, generate_password_hash(password), role, name, now()))
        con.commit()

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
        if session.get("role") != "management":
            flash("Management access required.")
            return redirect(url_for("dashboard"))
        return fn(*args, **kwargs)
    return wrap

def accounts_or_management(fn):
    @wraps(fn)
    def wrap(*args, **kwargs):
        if session.get("role") not in ["management","accounts"]:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrap

@app.context_processor
def inject():
    return dict(role=session.get("role"), name=session.get("name"), VISA_TYPES=VISA_TYPES, SECTION_TYPES=SECTION_TYPES, REFERENCE_TYPES=REFERENCE_TYPES, PROCESS_STATUS=PROCESS_STATUS, DELAY_ENDS=DELAY_ENDS, PAYMENT_STAGES=PAYMENT_STAGES, SHARE_TYPES=SHARE_TYPES)

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
    q = request.args.get("q","").strip()
    summary = None
    if q:
        summary = get_client_summary(q)
    with db() as con:
        totals = con.execute("SELECT COUNT(*) clients FROM clients").fetchone()
        p = con.execute("SELECT COALESCE(SUM(invoice_pkr),0) invoice, COALESCE(SUM(received_pkr),0) received, COALESCE(SUM(balance_pkr),0) balance FROM client_payments").fetchone()
        pending_proc = con.execute("SELECT COUNT(*) c FROM processing_sheet WHERE lower(status) LIKE '%pending%' OR lower(decision) LIKE '%pending%' OR lower(application_submission) LIKE '%pending%'").fetchone()["c"]
    return render_template("dashboard.html", q=q, summary=summary, totals=totals, p=p, pending_proc=pending_proc)

def get_client_summary(q):
    like = f"%{q}%"
    with db() as con:
        client = con.execute("SELECT * FROM clients WHERE main_investor LIKE ? ORDER BY id DESC LIMIT 1", (like,)).fetchone()
        proc = con.execute("SELECT * FROM processing_sheet WHERE client_name LIKE ? ORDER BY id DESC LIMIT 10", (like,)).fetchall()
        pays = con.execute("SELECT * FROM client_payments WHERE client_name LIKE ? ORDER BY id DESC", (like,)).fetchall()
        shares = con.execute("SELECT * FROM share_payments WHERE client_name LIKE ? ORDER BY share_type, id DESC", (like,)).fetchall()
        updates = []
        if pays:
            ids = [str(p["id"]) for p in pays]
            updates = con.execute(f"SELECT * FROM payment_updates WHERE client_payment_id IN ({','.join(['?']*len(ids))}) ORDER BY id DESC", ids).fetchall()
    return {"client": client, "processing": proc, "payments": pays, "shares": shares, "updates": updates}

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


def allowed_client_stage(total, received_eur):
    total=float(total or 0); rec=float(received_eur or 0)
    if total <= 0: return "Stage 1 - 70%"
    if rec < total*0.70: return "Stage 1 - 70%"
    if rec < total*0.85: return "Stage 2 - 15%"
    if rec < total: return "Stage 3 - 15%"
    return "Complete"

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
        amount_eur = float(d.get("received_eur") or 0)
        roe = float(d.get("roe") or 0)
        amount_pkr = amount_eur * roe
        cp_id = d.get("client_payment_id")
        with db() as con:
            # create payment account if selected new
            if d.get("mode") == "create":
                total_eur = float(d.get("total_eur") or 0)
                inv_roe = float(d.get("invoice_roe") or 0)
                con.execute("""INSERT INTO client_payments(date,visa,inv,client_name,roe_invoice,total_eur,invoice_pkr,received_pkr,received_percent,balance_pkr,balance_eur,payment_stage,remarks,updated_by,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    d.get("payment_date"), d.get("visa"), d.get("inv"), d.get("client_name"), inv_roe, total_eur, total_eur*inv_roe,
                    0, 0, total_eur*inv_roe, total_eur, "Pending", d.get("remarks"), d.get("updated_by"), now(), now()
                ))
                cp_id = con.execute("SELECT last_insert_rowid() id").fetchone()["id"]
            base_check = con.execute("SELECT * FROM client_payments WHERE id=?", (cp_id,)).fetchone()
            allowed = allowed_client_stage(base_check["total_eur"], (base_check["total_eur"] or 0) - (base_check["balance_eur"] or 0))
            if allowed != "Complete" and d.get("stage") != allowed:
                flash(f"Stage locked. Pehle {allowed} complete karo.")
                return redirect(url_for("new_payment"))
            if allowed == "Complete":
                flash("Client payment already complete.")
                return redirect(url_for("new_payment"))
            con.execute("""INSERT INTO payment_updates(client_payment_id,payment_date,stage,received_eur,roe,received_pkr,updated_by,remarks,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)""", (cp_id, d.get("payment_date"), d.get("stage"), amount_eur, roe, amount_pkr, d.get("updated_by"), d.get("remarks"), now()))
            rec = con.execute("SELECT COALESCE(SUM(received_eur),0) eur, COALESCE(SUM(received_pkr),0) pkr FROM payment_updates WHERE client_payment_id=?", (cp_id,)).fetchone()
            base = con.execute("SELECT * FROM client_payments WHERE id=?", (cp_id,)).fetchone()
            total_eur = base["total_eur"] or 0
            invoice_pkr = base["invoice_pkr"] or 0
            balance_eur = max(total_eur - rec["eur"], 0)
            balance_pkr = max(invoice_pkr - rec["pkr"], 0)
            percent = (rec["eur"]/total_eur*100) if total_eur else 0
            con.execute("""UPDATE client_payments SET received_pkr=?, received_percent=?, balance_pkr=?, balance_eur=?, payment_stage=?, updated_by=?, updated_at=? WHERE id=?""",
                (rec["pkr"], percent, balance_pkr, balance_eur, "Complete" if balance_eur<=0 else "Pending", d.get("updated_by"), now(), cp_id))
            con.commit()
        audit("payment_added", str(cp_id))
        return redirect(url_for("payments_sheet"))
    with db() as con:
        accounts = con.execute("SELECT * FROM client_payments ORDER BY client_name").fetchall()
        clients = con.execute("SELECT main_investor, visa_type FROM clients ORDER BY main_investor").fetchall()
    return render_template("new_payment.html", accounts=accounts, clients=clients)

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
                sp_id = con.execute("SELECT last_insert_rowid() id").fetchone()["id"]
            base_check = con.execute("SELECT * FROM share_payments WHERE id=?", (sp_id,)).fetchone()
            allowed = allowed_share_stage(base_check["share_type"], base_check["total_share"], (base_check["total_share"] or 0) - (base_check["balance_eur"] or 0))
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
    allowed = {"clients","processing_sheet","client_payments","share_payments","payment_updates","share_updates"}
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
    data={}
    with db() as con:
        for t in ["clients","processing_sheet","client_payments","share_payments","payment_updates","share_updates"]:
            data[t] = [dict(r) for r in con.execute(f"SELECT * FROM {t}").fetchall()]
    path = BACKUP_DIR / f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return send_file(path, as_attachment=True)

@app.get("/favicon.ico")
def favicon():
    return send_from_directory(APP_DIR/"static"/"images", "flair-logo.png")

@app.get("/health")
def health():
    return jsonify({"ok": True})

if __name__ == "__main__":
    init_db()
    import_seed_once()
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
