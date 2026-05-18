# app.py (patched)
from flask import Flask, render_template, send_from_directory, session, redirect, url_for, request, jsonify
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
import os, json, datetime
import joblib
import pandas as pd
from io import StringIO
import csv
from numbers import Integral

# ---------- Configuration ----------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
# Use a local 'data' folder to avoid OneDrive locking issues
DB_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DB_DIR, "users.db")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "").strip().lower()

app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-this")

# ---------- SQLite helper (single place to configure) ----------
def get_conn():
    """
    Always use this to get a sqlite3 connection.
    - timeout: wait up to 30s for locks to clear instead of failing immediately
    - check_same_thread=False: allow usage across threads (dev server may spawn threads)
    - enable WAL/journal PRAGMAs for better concurrency
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    try:
        # set PRAGMAs for reduced locking
        cur = conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA synchronous=NORMAL;")
        except Exception:
            # if PRAGMA fails, ignore (not fatal)
            pass
        finally:
            cur.close()
    except Exception:
        # ignore any PRAGMA errors but keep connection
        pass
    return conn

# ---------- Helper: DB initialization ----------
def init_db():
    # ensure folder exists
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    conn = get_conn()
    try:
        c = conn.cursor()
        # create tables if they don't exist
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            );
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NULL,
                input_json TEXT NOT NULL,
                predicted_role TEXT,
                confidence REAL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        ''')
        conn.commit()
    finally:
        conn.close()

# Call init on startup
init_db()

# ---------- Helper: DB actions (use get_conn) ----------
def create_user(username, email, password):
    pw_hash = generate_password_hash(password)
    try:
        with get_conn() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                      (username, email, pw_hash))
            conn.commit()
        return True, None
    except sqlite3.IntegrityError as e:
        # return DB constraint error (username/email duplicate)
        return False, str(e)
    except Exception as e:
        print("create_user error:", e)
        return False, str(e)

def get_user_by_username(username_or_email):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT id, username, email, password_hash FROM users WHERE username = ? OR email = ?",
                  (username_or_email, username_or_email))
        row = c.fetchone()
    return row

def save_prediction(user_id, input_obj, predicted_role, confidence=None):
    created_at = datetime.datetime.utcnow().isoformat() + "Z"
    try:
        with get_conn() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO predictions (user_id, input_json, predicted_role, confidence, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, json.dumps(input_obj, ensure_ascii=False), predicted_role, confidence, created_at)
            )
            conn.commit()
    except Exception as e:
        print("Failed to save prediction:", e)

def get_user_predictions(user_id, limit=200):
    items = []
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT id, input_json, predicted_role, confidence, created_at FROM predictions WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                  (user_id, limit))
        rows = c.fetchall()

    for r in rows:
        _id, input_json, predicted_role, confidence, created_at = r
        try:
            inp = json.loads(input_json)
        except Exception:
            inp = {"raw": input_json}
        items.append({
            "id": _id,
            "input": inp,
            "predicted_role": predicted_role,
            "confidence": confidence,
            "created_at": created_at
        })
    return items

# ---------- Model loading (robust) ----------
MODEL = None
LABEL_MAP = None
FEATURE_COLUMNS = None

def try_load_model():
    global MODEL, LABEL_MAP, FEATURE_COLUMNS
    # look for candidate model files
    candidates = []
    try:
        for fn in os.listdir(BASE_DIR):
            if fn.lower().endswith((".pkl", ".joblib")) and ("model" in fn.lower() or "career" in fn.lower() or "prediction" in fn.lower()):
                candidates.append(fn)
    except Exception:
        candidates = []

    if not candidates:
        print("MODEL LOAD: No candidate model files found in project root:", BASE_DIR)
    else:
        print("MODEL LOAD: Found candidate files:", candidates)

    model_loaded = False
    for fn in candidates:
        p = os.path.join(BASE_DIR, fn)
        try:
            print(f"MODEL LOAD: Attempting to load model from {p} ...")
            MODEL = joblib.load(p)
            print("MODEL LOAD: Successfully loaded model from", fn)
            model_loaded = True
            break
        except Exception as e:
            print(f"MODEL LOAD: Failed to load {fn}: {e}")

    # label mapping
    label_candidates = []
    try:
        for fn in os.listdir(BASE_DIR):
            if ("label" in fn.lower() or "mapping" in fn.lower()) and fn.lower().endswith((".pkl", ".json", ".joblib")):
                label_candidates.append(fn)
    except Exception:
        label_candidates = []

    if label_candidates:
        for lf in label_candidates:
            lp = os.path.join(BASE_DIR, lf)
            try:
                if lf.lower().endswith((".pkl", ".joblib")):
                    LABEL_MAP = joblib.load(lp)
                else:
                    with open(lp, "r", encoding="utf-8") as fh:
                        LABEL_MAP = json.load(fh)
                print("LABEL LOAD: Loaded label mapping from", lf)
                break
            except Exception as e:
                print("LABEL LOAD: Failed to load", lf, ":", e)
    else:
        print("LABEL LOAD: No label_mapping file found (optional).")

    # feature_columns.json
    fc_path = os.path.join(BASE_DIR, "feature_columns.json")
    if os.path.exists(fc_path):
        try:
            with open(fc_path, "r", encoding="utf-8") as f:
                FEATURE_COLUMNS = json.load(f)
            print("FEATURES LOAD: Loaded feature_columns.json")
        except Exception as e:
            print("FEATURES LOAD: Failed to load feature_columns.json:", e)
    else:
        print("FEATURES LOAD: feature_columns.json not found (optional).")

    if not model_loaded:
        print("MODEL LOAD: No model loaded. /predict will return fallback message.")
    return

try_load_model()

# ---------- Helpers ----------
def is_admin_user(session_user):
    try:
        return bool(
            ADMIN_USERNAME
            and session_user
            and session_user.get("username")
            and session_user.get("username").lower() == ADMIN_USERNAME
        )
    except Exception:
        return False

# ---------- Routes ----------

# Home: modal-aware
@app.route("/")
def home():
    show_login = session.pop("show_login", False)
    show_signup = session.pop("show_signup", False)
    signup_error = session.pop("signup_error", None)
    login_error = session.pop("login_error", None)
    registered = session.pop("registered", None)

    return render_template(
        "home.html",
        user=session.get("user"),
        show_login=show_login,
        show_signup=show_signup,
        signup_error=signup_error,
        login_error=login_error,
        registered=registered
    )

# Career form route
@app.route("/career-form")
def index():
    return render_template("index.html", user=session.get("user"))

# Signup
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "GET":
        return render_template("signup.html", user=session.get("user"))

    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")

    if not username or not email or not password:
        session["signup_error"] = "All fields required."
        session["show_signup"] = True
        return redirect(url_for("home"))

    ok, err = create_user(username, email, password)
    if not ok:
        session["signup_error"] = "Could not create user. " + (err or "")
        session["show_signup"] = True
        return redirect(url_for("home"))

    session["registered"] = True
    session["show_login"] = True
    return redirect(url_for("home"))

# Login
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        registered = request.args.get("registered")
        return render_template("login.html", user=session.get("user"), registered=registered)

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not username or not password:
        session["login_error"] = "Username and password required."
        session["show_login"] = True
        return redirect(url_for("home"))

    row = get_user_by_username(username)
    if not row:
        session["login_error"] = "User not found."
        session["show_login"] = True
        return redirect(url_for("home"))

    uid, uname, email, pw_hash = row
    if not check_password_hash(pw_hash, password):
        session["login_error"] = "Invalid password."
        session["show_login"] = True
        return redirect(url_for("home"))

    session["user"] = {"id": uid, "username": uname, "email": email}
    if is_admin_user({"id": uid, "username": uname, "email": email}):
        return redirect(url_for("admin"))
    else:
        return redirect(url_for("index"))

# Logout
@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("home"))

# Predict route (saves to DB)
@app.route("/predict", methods=["POST"])
def predict():
    try:
        data = request.get_json() or request.form.to_dict()
        # Save raw input even if model missing
        user = session.get("user")
        user_id = user["id"] if user else None

        if MODEL is None:
            # Save the attempt with fallback message
            save_prediction(user_id, data, "Model not available (dev).", None)
            return jsonify({
                "predicted_job_role_id": -1,
                "predicted_job_role": "Model not available (dev)."
            }), 200

        # prepare feature DataFrame if feature list exists
        if FEATURE_COLUMNS and isinstance(FEATURE_COLUMNS, list):
            row = {}
            for col in FEATURE_COLUMNS:
                found_val = None
                for k, v in data.items():
                    if k.strip().lower() == col.strip().lower():
                        found_val = v
                        break
                try:
                    row[col] = float(found_val) if (found_val is not None and str(found_val) != "") else 0.0
                except Exception:
                    row[col] = 0.0
            X = pd.DataFrame([row], columns=FEATURE_COLUMNS)
        else:
            row = {}
            for k, v in data.items():
                try:
                    row[k] = float(v)
                except Exception:
                    row[k] = v
            X = pd.DataFrame([row])

        # prediction
        try:
            preds = MODEL.predict(X)
        except Exception as e:
            try:
                preds = MODEL.predict(X.values)
            except Exception as e2:
                # save failed attempt
                save_prediction(user_id, data, f"Prediction failed: {e}; {e2}", None)
                return jsonify({"error": f"Model prediction failed: {e}; {e2}"}), 500

        pred = preds[0]
        predicted_label = str(pred)
        if LABEL_MAP:
            try:
                if isinstance(LABEL_MAP, dict):
                    if pred in LABEL_MAP:
                        predicted_label = LABEL_MAP[pred]
                    elif str(pred) in LABEL_MAP:
                        predicted_label = LABEL_MAP[str(pred)]
                    else:
                        for k, v in LABEL_MAP.items():
                            if v == pred:
                                predicted_label = k
                                break
                else:
                    predicted_label = str(pred)
            except Exception:
                predicted_label = str(pred)

        # Optional: get probability/confidence if model supports it
        confidence = None
        try:
            if hasattr(MODEL, "predict_proba"):
                probs = MODEL.predict_proba(X)
                # if binary/multi return max prob for predicted class
                if len(probs.shape) == 2:
                    confidence = float(probs[0].max())
                else:
                    confidence = float(probs[0])
        except Exception:
            confidence = None

        # Save prediction into DB
        save_prediction(user_id, data, predicted_label, confidence)

        return jsonify({
            "predicted_job_role_id": int(pred) if isinstance(pred, Integral) else -1,
            "predicted_job_role": predicted_label,
            "confidence": confidence
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# History route
@app.route("/history")
def history():
    user = session.get("user")
    if not user:
        # redirect to home and show login modal
        session["show_login"] = True
        return redirect(url_for("home"))

    user_id = user["id"]
    items = get_user_predictions(user_id, limit=500)
    return render_template("history.html", user=user, items=items)

# Admin & CSV export
@app.route("/admin")
def admin():
    user = session.get("user")
    if not is_admin_user(user):
        session["show_login"] = True
        return redirect(url_for("home"))

    # fetch all predictions (join with users for username/email)
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
          SELECT p.id, p.user_id, u.username, u.email, p.predicted_role, p.confidence, p.created_at, p.input_json
          FROM predictions p
          LEFT JOIN users u ON u.id = p.user_id
          ORDER BY p.id DESC
          LIMIT 1000
        """)
        rows = c.fetchall()

    items = []
    for r in rows:
        pid, uid, uname, email, role, conf, created_at, input_json = r
        try:
            inp = json.loads(input_json)
        except Exception:
            inp = {"raw": input_json}
        items.append({
            "id": pid,
            "user_id": uid,
            "username": uname,
            "email": email,
            "predicted_role": role,
            "confidence": conf,
            "created_at": created_at,
            "input": inp
        })

    return render_template("admin.html", user=user, items=items)

@app.route("/export_csv")
def export_csv():
    # Only admin can export
    user = session.get("user")
    if not is_admin_user(user):
        session["show_login"] = True
        return redirect(url_for("home"))

    # optional filter by user_id
    uid = request.args.get("user_id")
    with get_conn() as conn:
        c = conn.cursor()
        if uid:
            c.execute("SELECT p.id, p.user_id, u.username, u.email, p.predicted_role, p.confidence, p.created_at, p.input_json FROM predictions p LEFT JOIN users u ON u.id = p.user_id WHERE p.user_id = ? ORDER BY p.id DESC", (uid,))
        else:
            c.execute("SELECT p.id, p.user_id, u.username, u.email, p.predicted_role, p.confidence, p.created_at, p.input_json FROM predictions p LEFT JOIN users u ON u.id = p.user_id ORDER BY p.id DESC")
        rows = c.fetchall()

    # build CSV
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "user_id", "username", "email", "predicted_role", "confidence", "created_at", "input_json"])
    for r in rows:
        writer.writerow(r)

    csv_str = output.getvalue()
    output.close()
    # return as attachment
    return (csv_str, 200, {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": "attachment; filename=predictions_export.csv"
    })

@app.route('/offline')
def offline():
    return render_template('offline.html')

@app.route("/manifest.json")
def serve_manifest():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "manifest.json",
        mimetype="application/manifest+json",
    )

@app.route("/sw.js")
def serve_service_worker():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "sw.js",
        mimetype="application/javascript",
    )

@app.route("/.well-known/assetlinks.json")
def serve_assetlinks():
    folder = os.path.join(app.root_path, "static", ".well-known")
    # safety: avoid directory-traversal, ensure file exists
    file_path = os.path.join(folder, "assetlinks.json")
    if not os.path.exists(file_path):
        from flask import abort
        abort(404)
    return send_from_directory(folder, "assetlinks.json", mimetype="application/json")

# Run
if __name__ == "__main__":
    # helpful debug output so you can confirm which DB file is used
    print("Starting Flask app. DB_PATH =", DB_PATH)
    # use_reloader=False prevents the spawn of a second process that can hold DB open
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", use_reloader=False)
