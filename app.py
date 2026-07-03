from __future__ import annotations
import json, sqlite3, os
from datetime import datetime
from pathlib import Path
from flask import Flask, jsonify, render_template, request, send_from_directory, redirect, url_for, session

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "flair_jarvis_crm.db"
BACKUP_DIR = APP_DIR / "backups"
BACKUP_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "flair-bilal-super-secret-key-2026")

USERS = {
    "bilal": {"password": "bilal2004", "role": "admin", "name": "Muhammad Bilal Tahir"},
    "accounts": {"password": "accounts123", "role": "accounts", "name": "Accounts Team"},
    "manager": {"password": "manager123", "role": "manager", "name": "Management Team"},
    "director": {"password": "director123", "role": "director", "name": "Director"},
}

def logged_in():
    return "user" in session

def db():
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL)")
    con.commit()
    return con

def log(action, details=""):
    with db() as con:
        con.execute("INSERT INTO audit_log(action, details, created_at) VALUES (?,?,?)", (action, details, datetime.now().isoformat(timespec="seconds")))
        con.commit()

@app.get("/")
def index():
    if not logged_in():
        return redirect(url_for("login"))
    return render_template("index.html", user=session["user"])

@app.get("/login")
def login():
    if logged_in():
        return redirect(url_for("index"))
    return render_template("login.html")

@app.post("/login")
def login_post():
    username = request.form.get("username", "").lower().strip()
    password = request.form.get("password", "")

    user = USERS.get(username)
    if not user or user["password"] != password:
        return render_template("login.html", error="Wrong username or password")

    session["user"] = {
        "username": username,
        "role": user["role"],
        "name": user["name"]
    }
    log("login", username)
    return redirect(url_for("index"))

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.get("/api/store")
def get_store():
    if not logged_in():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    with db() as con:
        row = con.execute("SELECT value FROM kv_store WHERE key='store'").fetchone()

    if not row:
        return jsonify({"store": None})

    return jsonify({"store": json.loads(row[0])})

@app.post("/api/store")
def save_store():
    if not logged_in():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    store = payload.get("store")

    if not isinstance(store, dict):
        return jsonify({"ok": False, "error": "Invalid store"}), 400

    value = json.dumps(store, ensure_ascii=False)
    now = datetime.now().isoformat(timespec="seconds")

    with db() as con:
        con.execute(
            """INSERT INTO kv_store(key,value,updated_at) VALUES('store',?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (value, now)
        )
        con.commit()

    user = session.get("user", {}).get("username", "unknown")
    log("store_saved", f"{user} | {len(value)} bytes")
    return jsonify({"ok": True, "updated_at": now})

@app.post("/api/backup")
def backup_store():
    if not logged_in():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    with db() as con:
        row = con.execute("SELECT value FROM kv_store WHERE key='store'").fetchone()

    if not row:
        return jsonify({"ok": False, "error": "No data"}), 404

    name = f"flair_jarvis_crm_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path = BACKUP_DIR / name
    path.write_text(row[0], encoding="utf-8")

    log("backup_created", name)
    return jsonify({"ok": True, "file": str(path)})

@app.get("/api/audit")
def audit():
    if not logged_in():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    with db() as con:
        rows = con.execute("SELECT action, details, created_at FROM audit_log ORDER BY id DESC LIMIT 200").fetchall()

    return jsonify({"rows": rows})

@app.get("/backups/<path:name>")
def backups(name):
    if not logged_in():
        return redirect(url_for("login"))
    return send_from_directory(BACKUP_DIR, name, as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)