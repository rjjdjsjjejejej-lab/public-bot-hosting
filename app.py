import os, re, io, json, time, uuid, zipfile, sqlite3, subprocess
from pathlib import Path
from functools import wraps
from flask import Flask, request, redirect, url_for, render_template, session, flash, abort
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
UPLOADS = DATA / "bots"
LOGS = DATA / "logs"
for p in (DATA, UPLOADS, LOGS):
    p.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key or len(app.secret_key) < 32:
    raise RuntimeError("Set SECRET_KEY to a random value of at least 32 characters before starting.")
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "20")) * 1024 * 1024
DB = DATA / "panel.sqlite3"
BOT_IMAGE = os.environ.get("BOT_RUNNER_IMAGE", "python:3.12-slim")
MAX_BOTS_PER_USER = int(os.environ.get("MAX_BOTS_PER_USER", "3"))
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS bots(
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            container_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, name),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )""")
init_db()

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def current_user():
    if not session.get("user_id"):
        return None
    with db() as con:
        return con.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()

def owned_bot(bot_id):
    if not re.fullmatch(r"[a-f0-9]{32}", bot_id or ""):
        abort(404)
    with db() as con:
        row = con.execute("SELECT * FROM bots WHERE id=? AND user_id=?",
                          (bot_id, session["user_id"])).fetchone()
    if not row:
        abort(404)
    return row

def bot_path(bot_id):
    p = (UPLOADS / bot_id).resolve()
    if UPLOADS.resolve() not in p.parents:
        abort(404)
    return p

def docker_available():
    try:
        subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=3, check=True)
        return True
    except Exception:
        return False

def container_status(row):
    if not row["container_id"]:
        return "STOPPED"
    try:
        r = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}", row["container_id"]],
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip().upper() if r.returncode == 0 else "STOPPED"
    except Exception:
        return "UNKNOWN"

@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"
    return resp

@app.route("/")
def index():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    with db() as con:
        rows = con.execute("SELECT * FROM bots WHERE user_id=? ORDER BY created_at DESC",
                           (session["user_id"],)).fetchall()
    bots = [{"id": r["id"], "name": r["name"], "status": container_status(r)} for r in rows]
    return render_template("index.html", bots=bots, username=session.get("username"),
                           docker_ok=docker_available(), max_bots=MAX_BOTS_PER_USER)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
            flash("Username must be 3–32 letters, numbers, dots, underscores or hyphens.", "error")
        elif len(password) < 12:
            flash("Use a password with at least 12 characters.", "error")
        else:
            try:
                with db() as con:
                    con.execute("INSERT INTO users(username,password_hash) VALUES(?,?)",
                                (username, generate_password_hash(password)))
                flash("Account created. Please log in.", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("That username is already taken.", "error")
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with db() as con:
            user = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("index"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")

@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.post("/upload")
@login_required
def upload():
    f = request.files.get("file")
    name = secure_filename(request.form.get("bot_name", "").strip())
    if not name or not NAME_RE.fullmatch(name) or not f or not f.filename.lower().endswith(".zip"):
        flash("Choose a ZIP file and a bot name (letters, numbers, _ or -).", "error")
        return redirect(url_for("index"))
    with db() as con:
        count = con.execute("SELECT COUNT(*) FROM bots WHERE user_id=?", (session["user_id"],)).fetchone()[0]
        if count >= MAX_BOTS_PER_USER:
            flash(f"Your account limit is {MAX_BOTS_PER_USER} bots.", "error")
            return redirect(url_for("index"))
        if con.execute("SELECT 1 FROM bots WHERE user_id=? AND name=?", (session["user_id"], name)).fetchone():
            flash("You already have a bot with that name.", "error")
            return redirect(url_for("index"))
    bot_id = uuid.uuid4().hex
    target = bot_path(bot_id)
    temp = DATA / f"{bot_id}.zip"
    f.save(temp)
    try:
        with zipfile.ZipFile(temp) as z:
            infos = z.infolist()
            if len(infos) > 300 or sum(i.file_size for i in infos) > 50 * 1024 * 1024:
                raise ValueError("ZIP is too large after extraction or contains too many files.")
            for info in infos:
                dest = (target / info.filename).resolve()
                if target.resolve() not in dest.parents and dest != target.resolve():
                    raise ValueError("Unsafe ZIP path.")
                if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    raise ValueError("ZIP symlinks are not allowed.")
            target.mkdir(parents=True)
            z.extractall(target)
        children = list(target.iterdir())
        if len(children) == 1 and children[0].is_dir():
            nested = children[0]
            for child in list(nested.iterdir()):
                child.rename(target / child.name)
            nested.rmdir()
        script = "main.py" if (target / "main.py").exists() else "bot.py"
        if not (target / script).exists():
            raise ValueError("ZIP must contain main.py or bot.py at its root.")
        # Deliberately do not install arbitrary requirements or build user Dockerfiles.
        with db() as con:
            con.execute("INSERT INTO bots(id,user_id,name) VALUES(?,?,?)",
                        (bot_id, session["user_id"], name))
        flash("Bot uploaded. Review its code before starting it.", "success")
    except Exception as e:
        import shutil
        shutil.rmtree(target, ignore_errors=True)
        flash(f"Upload failed: {e}", "error")
    finally:
        temp.unlink(missing_ok=True)
    return redirect(url_for("index"))

@app.post("/bot/<bot_id>/start")
@login_required
def start(bot_id):
    row = owned_bot(bot_id)
    if not docker_available():
        flash("Docker is not available on this server. Configure Docker before starting bots.", "error")
        return redirect(url_for("index"))
    folder = bot_path(bot_id)
    script = "main.py" if (folder / "main.py").exists() else "bot.py"
    name = "bot-" + bot_id
    # Network access is required by Telegram bots; only run trusted code.
    # A container is a useful boundary, not a guarantee against malicious code.
    cmd = ["docker", "run", "-d", "--name", name, "--label", "public-bot-panel=true",
           "--restart", "no", "--memory", "256m", "--cpus", "0.50", "--pids-limit", "64",
           "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
           "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
           "--network", "bridge", "--user", "65534:65534",
           "--mount", f"type=bind,src={folder.resolve()},dst=/app,readonly",
           "--workdir", "/app", "-e", "PYTHONUNBUFFERED=1",
           BOT_IMAGE, "python", "-u", f"/app/{script}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode:
            flash("Container failed to start. Check server Docker configuration and image.", "error")
        else:
            cid = r.stdout.strip()
            with db() as con:
                con.execute("UPDATE bots SET container_id=? WHERE id=?", (cid, bot_id))
            flash("Bot container started.", "success")
    except Exception:
        flash("Could not start container. Check server logs.", "error")
    return redirect(url_for("index"))

@app.post("/bot/<bot_id>/stop")
@login_required
def stop(bot_id):
    row = owned_bot(bot_id)
    if row["container_id"]:
        subprocess.run(["docker", "rm", "-f", row["container_id"]],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        with db() as con:
            con.execute("UPDATE bots SET container_id=NULL WHERE id=?", (bot_id,))
    flash("Bot stopped.", "success")
    return redirect(url_for("index"))

@app.route("/bot/<bot_id>/logs")
@login_required
def logs(bot_id):
    row = owned_bot(bot_id)
    output = "No container has been started."
    if row["container_id"]:
        r = subprocess.run(["docker", "logs", "--tail", "250", row["container_id"]],
                          capture_output=True, text=True, timeout=10)
        output = (r.stdout or "") + (r.stderr or "")
    return render_template("logs.html", name=row["name"], output=output[-18000:])

@app.post("/bot/<bot_id>/delete")
@login_required
def delete(bot_id):
    import shutil
    row = owned_bot(bot_id)
    if row["container_id"]:
        subprocess.run(["docker", "rm", "-f", row["container_id"]],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    shutil.rmtree(bot_path(bot_id), ignore_errors=True)
    with db() as con:
        con.execute("DELETE FROM bots WHERE id=?", (bot_id,))
    flash("Bot deleted.", "success")
    return redirect(url_for("index"))

@app.errorhandler(413)
def too_large(_):
    flash("Upload exceeds the configured file-size limit.", "error")
    return redirect(url_for("index"))

if __name__ == "__main__":
    # For local testing only. Use Gunicorn behind HTTPS for public deployment.
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
