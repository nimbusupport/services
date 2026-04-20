from flask import Flask, render_template, request, jsonify, send_file
import os
import re
import io
import pandas as pd
import gspread
import requests
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from dotenv import load_dotenv
from flask import session, redirect, url_for

load_dotenv()

app = Flask(
    __name__,
    template_folder="template",
    static_folder="template",
    static_url_path=""
)
TOKEN_INFORU = os.environ.get("TOKEN_INFORU")
app.secret_key = os.environ.get("SECRET_KEY", "super-secret-key")
APP_USERNAME = os.environ.get("APP_USERNAME")
APP_PASSWORD = os.environ.get("APP_PASSWORD")
# Manual login users (same shared password)
ALLOWED_USERS = {
    "admin@nimbusip.com",
    "eugeni@nimbusip.com",
    "nir@nimbusip.com",
}
SHARED_PASSWORD = "Aa@0778066666"
# ====== .ENV ======
SMS_URL = os.environ.get("SMS_URL")
SMS_TOKEN = os.environ.get("SMS_TOKEN")
# ====== CONFIG ======
SPREADSHEET_ID = "1uwtREvtWENPabibI5FSlhdYokIbBs_kuZmYVeL-BgCQ"
SHEET_NAME = "SMS"
# Bot sheet
BOT_SHEET_NAME = "\u05e9\u05d9\u05e8\u05d5\u05ea \u05de\u05e2\u05e0\u05d4 - \u05d1\u05d5\u05d8"

# NumberCGR pool sheet
CGR_SHEET_NAME = "\u05d7\u05d9\u05e4_\u05e1\u05de\u05e1"
CGR_START_ROW = 312
CGR_COL_NUMBER = 1  # A
CGR_COL_MARK = 3    # B (checkbox/mark)

# Column mapping (1-based for gspread)
COL_NAME = 1       # A
COL_IDNUMBER = 2   # B (׳—.׳₪) hidden in UI
COL_STATUS = 8     # H
COL_SMS_TEXT = 10  # J
COL_K = 11         # K

STATUS_PENDING = "\u05de\u05de\u05ea\u05d9\u05df"
STATUS_DONE = "\u05d1\u05d5\u05e6\u05e2"
K_REQUIRED_VALUE = "\u05dc\u05e7\u05d5\u05d7 \u05d4\u05d5\u05ea\u05e7\u05df"

# ENV
CREDENTIALS_FILE = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json") or "").strip()
FIREBERRY_TOKENID = (os.environ.get("FIREBERRY_TOKENID") or "").strip()
CRM_URL = (os.environ.get("CRM_URL") or "").strip()
FIREBERRY_URL = CRM_URL
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "1MOdZ1gTYGizpKlc6CtErskM_KMRp-2Db")
DRIVE_DONE_FOLDER_NAME = os.environ.get("DRIVE_DONE_FOLDER_NAME", "Done")

if not FIREBERRY_TOKENID:
    raise RuntimeError("FIREBERRY_TOKENID not found in .env")
if not FIREBERRY_URL:
    raise RuntimeError("CRM_URL not found in .env")

# Logging
LOG_DIR = "log"
LOG_FILE = os.path.join(LOG_DIR, "created.log")
INFORU_LOG_FILENAME = "\u05de\u05e1\u05e4\u05e8\u05d9\u05dd \u05dc\u05d0\u05d9\u05de\u05d5\u05ea.txt"
ACTIVE_WINDOW_MINUTES = 30
SERVICE_ACTIVITY = {"sms": {}, "bot": {}, "recordings": {}}


def ensure_log_file():
    os.makedirs(LOG_DIR, exist_ok=True)
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("")


def append_log(customers):
    """
    customers: list of dicts with keys: name, domain, did
    """
    ensure_log_file()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"=== {ts} | Status -> {STATUS_DONE} | Count: {len(customers)} ===\n")
        f.write("׳©׳ ׳׳§׳•׳—\tDomain\tDID\n")
        for c in customers:
            name = (c.get("name") or "").strip()
            domain = (c.get("domain") or "").strip()
            did = (c.get("did") or "").strip()
            f.write(f"{name}\t{domain}\t{did}\n")
        f.write("\n")


def _service_key(service_name: str) -> str:
    if service_name == "record":
        return "recordings"
    return service_name


def register_service_activity(service_name: str):
    username = (session.get("username") or "").strip().lower()
    if not username:
        return
    key = _service_key(service_name)
    now = datetime.utcnow()
    cutoff = now - timedelta(minutes=ACTIVE_WINDOW_MINUTES)
    users = SERVICE_ACTIVITY.get(key, {})
    users = {u: ts for u, ts in users.items() if ts >= cutoff}
    users[username] = now
    SERVICE_ACTIVITY[key] = users


def get_active_users_for(service_name: str):
    key = _service_key(service_name)
    now = datetime.utcnow()
    cutoff = now - timedelta(minutes=ACTIVE_WINDOW_MINUTES)
    users = SERVICE_ACTIVITY.get(key, {})
    users = {u: ts for u, ts in users.items() if ts >= cutoff}
    SERVICE_ACTIVITY[key] = users
    return sorted(users.keys())


def get_gspread_client():
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scope)
    return gspread.authorize(creds)


def digits_only(s: str) -> str:
    return re.sub(r"\D+", "", (s or "").strip())


def first_number_clean(value: str) -> str:
    """
    Take the first number chunk from a string, keep only digits.
    If length >= 10 -> return first 10
    If length == 9 -> return 9
    Else -> return whatever digits exist
    """
    raw = (value or "").strip()
    if not raw:
        return ""

    parts = re.split(r"[\s,;]+", raw)
    for p in parts:
        d = digits_only(p)
        if d:
            if len(d) >= 10:
                return d[:10]
            if len(d) == 9:
                return d
            return d

    d = digits_only(raw)
    if not d:
        return ""
    if len(d) >= 10:
        return d[:10]
    if len(d) == 9:
        return d
    return d


def fireberry_lookup_by_idnumber(idnumber: str) -> dict:
    id_digits = digits_only(idnumber)
    if not id_digits:
        return {"found": False, "domain": "", "did": ""}

    headers = {"tokenid": FIREBERRY_TOKENID}
    body = {
        "objecttype": 1,
        "page_size": 50,
        "page_number": 1,
        "fields": "pcfsystemfield179,accountname,pcfsystemfield256,pcfsystemfield164,pcfsystemfield166",
        "query": f"(idnumber = {id_digits})",
        "sort_type": "desc"
    }

    r = requests.post(FIREBERRY_URL, headers=headers, json=body, timeout=30)
    r.raise_for_status()
    resp = r.json()

    rows = []
    if isinstance(resp, dict):
        inner = resp.get("data")
        if isinstance(inner, dict) and isinstance(inner.get("Data"), list):
            rows = inner.get("Data", [])

    if not rows or not isinstance(rows[0], dict):
        return {"found": False, "domain": "", "did": ""}

    row = rows[0]
    domain = (row.get("pcfsystemfield179") or "").strip()

    main_raw = (row.get("pcfsystemfield166") or "").strip()
    range_raw = (row.get("pcfsystemfield164") or "").strip()
    did_raw = main_raw if main_raw else range_raw
    did = first_number_clean(did_raw)

    return {"found": True, "domain": domain, "did": did}

#//fireberry_lookup_by_idnumber
def fireberry_lookup_domain_by_record_id(record_id):

    headers = {"tokenid": FIREBERRY_TOKENID}

    body = {
        "objecttype": 1,
        "page_size": 1,
        "page_number": 1,
        "fields": "pcfsystemfield179",
        "query": f"(id = {record_id})"
    }

    try:

        r = requests.post(FIREBERRY_URL, headers=headers, json=body, timeout=30)
        r.raise_for_status()

        resp = r.json()

        rows = resp.get("data", {}).get("Data", [])

        if rows:
            return (rows[0].get("pcfsystemfield179") or "").strip()

    except Exception as e:
        print("Fireberry BOT lookup error:", e)

    return ""


def get_drive_service(readonly=True):
    scope = ["https://www.googleapis.com/auth/drive.readonly"] if readonly else ["https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scope)
    return build("drive", "v3", credentials=creds)


def extract_order_id_from_record(filename: str) -> str:
    base_name = os.path.splitext(os.path.basename(filename or ""))[0]
    if not base_name:
        return ""
    # Primary rule: first 5 digits from the left side (allows leading spaces).
    match = re.match(r"\s*(\d{5})", base_name)
    if match:
        return match.group(1)
    # Fallback for names like "... - 12249.wav".
    tail_match = re.search(r"(\d{5})\s*$", base_name)
    return tail_match.group(1) if tail_match else ""


def normalize_domain_value(value: str) -> str:
    domain = (value or "").strip()
    if not domain:
        return ""
    if domain.lower() == "accepted":
        return ""
    return domain


def get_domain_from_crm(crmordernumber):
    try:
        if not crmordernumber:
            return ""
        if not FIREBERRY_TOKENID:
            return ""

        headers = {"tokenid": FIREBERRY_TOKENID}

        order_body = {
            "objecttype": 13,
            "page_size": 1,
            "page_number": 1,
            "fields": "accountid,CrmOrderNumber",
            "query": f"(CrmOrderNumber = '{crmordernumber}')",
            "sort_type": "desc"
        }
        order_resp = requests.post(FIREBERRY_URL, headers=headers, json=order_body, timeout=20)
        order_resp.raise_for_status()
        order_rows = order_resp.json().get("data", {}).get("Data", [])
        if not order_rows or not isinstance(order_rows[0], dict):
            return ""

        accountid = str(order_rows[0].get("accountid") or "").strip()
        if not accountid:
            return ""

        account_body = {
            "objecttype": 1,
            "page_size": 1,
            "page_number": 1,
            "fields": "accountid,pcfsystemfield179,accountname",
            "query": f"(accountid = '{accountid}')"
        }
        account_resp = requests.post(FIREBERRY_URL, headers=headers, json=account_body, timeout=20)
        account_resp.raise_for_status()
        account_rows = account_resp.json().get("data", {}).get("Data", [])
        if not account_rows or not isinstance(account_rows[0], dict):
            return ""

        value = account_rows[0].get("pcfsystemfield179")
        return normalize_domain_value(str(value).strip() if value is not None else "")

    except Exception as e:
        print("CRM error:", e)
        return ""


def get_pending_customers():
    """
    Returns customers where:
      H == ׳׳׳×׳™׳ AND K == ׳׳§׳•׳— ׳”׳•׳×׳§׳
    Also includes:
      - idnumber (hidden)
      - numbercgr from sheet ׳—׳™׳₪_׳¡׳׳¡ (only rows where column C empty)
      - cgr_row (for updates on export)
      - cgr_marked (green/yellow indicator from column B)
    """
    client = get_gspread_client()
    ws = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    data = ws.get_all_values()
    if not data or len(data) < 2:
        return []

    rows = data[1:]
    pending = []

    for i, row in enumerate(rows, start=2):
        status = row[COL_STATUS - 1].strip() if len(row) >= COL_STATUS else ""
        k_value = row[COL_K - 1].strip() if len(row) >= COL_K else ""

        if status != STATUS_PENDING or k_value != K_REQUIRED_VALUE:
            continue

        name = row[COL_NAME - 1].strip() if len(row) >= COL_NAME else ""
        idnumber = row[COL_IDNUMBER - 1].strip() if len(row) >= COL_IDNUMBER else ""
        sms_text = row[COL_SMS_TEXT - 1].strip() if len(row) >= COL_SMS_TEXT else ""

        pending.append({
            "sheet_row": i,
            "name": name,
            "idnumber": idnumber,
            "text": sms_text,
            "status": status
        })

    # Attach NumberCGR from ׳—׳™׳₪_׳¡׳׳¡ (ONLY rows where column C empty)
    try:
        if pending:
            cgr_ws = client.open_by_key(SPREADSHEET_ID).worksheet(CGR_SHEET_NAME)

            # read more rows so we can filter
            cgr_data = cgr_ws.get(f"A{CGR_START_ROW}:C")

            free_numbers = []

            for idx, row in enumerate(cgr_data):
                a_val = row[0] if len(row) >= 1 else ""
                b_val = row[1] if len(row) >= 2 else ""
                c_val = row[2] if len(row) >= 3 else ""

                # IMPORTANT: skip rows where column C is not empty
                if (c_val or "").strip():
                    continue

                num_digits = digits_only(a_val)
                if not num_digits:
                    continue

                numbercgr = num_digits if num_digits.startswith("0") else ("0" + num_digits)

                b_norm = (b_val or "").strip().upper()
                marked = bool(b_norm) and b_norm not in ("FALSE", "0", "NO")

                free_numbers.append({
                    "number": numbercgr,
                    "row": CGR_START_ROW + idx,
                    "marked": marked
                })

            # attach numbers to customers
            for idx, cust in enumerate(pending):
                if idx < len(free_numbers):
                    cust["numbercgr"] = free_numbers[idx]["number"]
                    cust["cgr_row"] = free_numbers[idx]["row"]
                    cust["cgr_marked"] = free_numbers[idx]["marked"]
                else:
                    cust["numbercgr"] = ""
                    cust["cgr_row"] = None
                    cust["cgr_marked"] = False

    except Exception:
        for cust in pending:
            cust["numbercgr"] = ""
            cust["cgr_row"] = None
            cust["cgr_marked"] = False

    return pending


def get_recordings_waiting_count():
    service = get_drive_service(readonly=True)
    results = service.files().list(
        q=f"'{DRIVE_FOLDER_ID}' in parents and mimeType='audio/wav' and trashed=false",
        fields="files(id)"
    ).execute()
    return len(results.get("files", []))


# ================= ROOT =================
@app.route("/")
def root():
    return redirect(url_for("login"))


# ================= LOGIN =================
@app.route("/login", methods=["GET", "POST"])
def login():

    # If already logged in ג†’ go to home
    if session.get("logged_in"):
        return redirect(url_for("home"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password")

        if username in ALLOWED_USERS and password == SHARED_PASSWORD:
            session["logged_in"] = True
            session["username"] = username
            return redirect(url_for("home"))

        return render_template("login.html", error="Invalid username or password")

    return render_template("login.html")


# ================= HOME =================
@app.route("/home")
def home():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    register_service_activity("dashboard")
    return render_template("home.html", current_user=session.get("username", ""))


# ================= SMS PAGE =================
@app.route("/sms")
def sms_page():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    register_service_activity("sms")
    return render_template("index.html", current_user=session.get("username", ""))


# ================= BOT PAGE =================
@app.route("/bot")
def bot_page():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    register_service_activity("bot")
    return render_template("bot.html", current_user=session.get("username", ""))


# ================= RECORD PAGE =================
@app.route("/record")
def record_page():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    register_service_activity("recordings")
    return render_template("record.html", current_user=session.get("username", ""))


@app.route("/dashboard-data")
def dashboard_data():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    register_service_activity("dashboard")

    sms_waiting = len(get_pending_customers())
    bot_waiting = len(get_bot_customers())
    recordings_waiting = get_recordings_waiting_count()

    return jsonify({
        "sms": {
            "waiting": sms_waiting,
            "active_users": get_active_users_for("sms"),
        },
        "bot": {
            "waiting": bot_waiting,
            "active_users": get_active_users_for("bot"),
        },
        "recordings": {
            "waiting": recordings_waiting,
            "active_users": get_active_users_for("recordings"),
        },
    })


@app.route("/recordings-data")
def recordings_data():

    if not session.get("logged_in"):
        return redirect(url_for("login"))
    register_service_activity("recordings")

    service = get_drive_service(readonly=True)
    results = service.files().list(
        q=f"'{DRIVE_FOLDER_ID}' in parents and mimeType='audio/wav' and trashed=false",
        fields="files(id,name)"
    ).execute()

    files = results.get("files", [])
    output = []
    domain_by_order = {}

    for f in files:
        order_id = extract_order_id_from_record(f.get("name", ""))
        if order_id and order_id not in domain_by_order:
            domain_by_order[order_id] = get_domain_from_crm(order_id)

    for f in files:
        name = f.get("name", "")
        file_id = f.get("id", "")
        order_id = extract_order_id_from_record(name)
        domain = domain_by_order.get(order_id, "") if order_id else ""

        output.append({
            "name": name,
            "file_id": file_id,
            "order_id": order_id,
            "domain": domain
        })

    return jsonify(output)


@app.route("/download-record/<file_id>/<domain>")
def download_record(file_id, domain):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    service = get_drive_service(readonly=True)
    request_drive = service.files().get_media(fileId=file_id)

    file_data = io.BytesIO()
    downloader = MediaIoBaseDownload(file_data, request_drive)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    file_data.seek(0)

    if domain == "nodomain":
        domain = ""

    filename = f"{domain}_IVR.wav" if domain else "record_IVR.wav"
    return send_file(
        file_data,
        mimetype="audio/wav",
        as_attachment=True,
        download_name=filename
    )


@app.route("/mark-done/<file_id>", methods=["POST"])
def mark_record_done(file_id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    service = get_drive_service(readonly=False)

    file_meta = service.files().get(fileId=file_id, fields="id,parents").execute()
    current_parents = file_meta.get("parents", [])

    done_query = (
        f"'{DRIVE_FOLDER_ID}' in parents and "
        f"name = '{DRIVE_DONE_FOLDER_NAME}' and "
        "mimeType = 'application/vnd.google-apps.folder' and trashed=false"
    )
    done_search = service.files().list(q=done_query, fields="files(id,name)").execute().get("files", [])

    if done_search:
        done_folder_id = done_search[0]["id"]
    else:
        done_folder = service.files().create(
            body={
                "name": DRIVE_DONE_FOLDER_NAME,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [DRIVE_FOLDER_ID]
            },
            fields="id"
        ).execute()
        done_folder_id = done_folder["id"]

    remove_parents = ",".join(current_parents) if current_parents else ""
    service.files().update(
        fileId=file_id,
        addParents=done_folder_id,
        removeParents=remove_parents,
        fields="id,parents"
    ).execute()

    return jsonify({"ok": True})


# ================= LOGOUT =================
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))
##Load data
@app.route("/load-data")
def load_data():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    register_service_activity("sms")
    return jsonify(get_pending_customers())

#Firebarry Sync by ID number
@app.route("/fireberry-by-id", methods=["POST"])
def fireberry_by_id():
    payload = request.get_json(silent=True) or {}
    idnumber = (payload.get("idnumber") or "").strip()

    if not idnumber:
        return jsonify({"ok": False, "message": "Missing idnumber"}), 400

    try:
        result = fireberry_lookup_by_idnumber(idnumber)
        return jsonify({"ok": True, **result})
    except requests.HTTPError as e:
        return jsonify({"ok": False, "message": f"Fireberry HTTP error: {str(e)}"}), 502
    except Exception as e:
        return jsonify({"ok": False, "message": f"Error: {str(e)}"}), 500


@app.route("/mark-done", methods=["POST"])
def mark_done():

    payload = request.get_json(silent=True) or {}
    customers = payload.get("customers", [])

    if not isinstance(customers, list) or not customers:
        return jsonify({"ok": False, "message": "No customers provided."}), 400

    rows = []
    cgr_updates = []
    clean_customers = []

    for c in customers:

        if not isinstance(c, dict):
            continue

        r = c.get("sheet_row")
        cgr_row = c.get("cgr_row")

        if not isinstance(r, int) or r < 2:
            continue

        name = (c.get("name") or "").strip()
        domain = (c.get("domain") or "").strip()
        did = (c.get("did") or "").strip()

        rows.append(r)

        clean_customers.append({
            "name": name,
            "domain": domain,
            "did": did
        })

        # CGR sheet update (׳—׳™׳₪_׳¡׳׳¡ column C)
        if isinstance(cgr_row, int) and domain:
            cgr_updates.append({
                "range": gspread.utils.rowcol_to_a1(cgr_row, CGR_COL_MARK),
                "values": [[domain]]
            })

    if not rows:
        return jsonify({"ok": False, "message": "No valid rows to update."}), 400

    client = get_gspread_client()

    # Update SMS sheet
    ws = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    updates = []
    for r in rows:
        updates.append({
            "range": gspread.utils.rowcol_to_a1(r, COL_STATUS),
            "values": [[STATUS_DONE]]
        })

    ws.batch_update(updates)

    # Update CGR sheet
    if cgr_updates:
        cgr_ws = client.open_by_key(SPREADSHEET_ID).worksheet(CGR_SHEET_NAME)
        cgr_ws.batch_update(cgr_updates)

    append_log(clean_customers)

    return jsonify({
        "ok": True,
        "updated": len(rows)
    })

@app.route("/send-inforu-mail", methods=["POST"])
def send_inforu_mail():

    payload = request.get_json(silent=True) or {}
    dids = payload.get("dids", [])
    # normalize numbers
    dids = [re.sub(r"\D", "", d) for d in dids]

    if not dids:
        return jsonify({"ok": False, "message": "No DID provided"}), 400

    # remove duplicates
    dids = list(dict.fromkeys(dids))

    os.makedirs("did_inforu", exist_ok=True)
    path = os.path.join("did_inforu", INFORU_LOG_FILENAME)

    # read existing numbers
    existing_numbers = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
            existing_numbers = set(re.findall(r"0\d{8,9}", text))

    # filter only new numbers
    new_dids = [d for d in dids if d not in existing_numbers]

    if not new_dids:
        return jsonify({"ok": False, "message": "All numbers already logged"}), 400

    numbers_str = " , ".join(new_dids)
    date_str = datetime.now().strftime("%d.%m.%Y")

    block = f"""
=={date_str}==
\u05e9\u05dc\u05d5\u05dd \u05e8\u05d1,
\u05d0\u05e0\u05d5 \u05d7\u05d1\u05e8\u05ea \u05e0\u05d9\u05de\u05d1\u05d5\u05e1 \u05d8\u05dc\u05e7\u05d5\u05dd \u05d1\u05e2\"\u05de (\u05d7.\u05e4 514684125), \u05de\u05d0\u05e9\u05e8\u05d9\u05dd \u05d1\u05d6\u05d0\u05ea \u05db\u05d9 \u05de\u05e1\u05e4\u05e8\u05d9 \u05d4\u05e7\u05d5 \u05d4\u05d1\u05d0\u05d9\u05dd:
{numbers_str}
\u05d4\u05dd \u05d1\u05d1\u05e2\u05dc\u05d5\u05ea\u05e0\u05d5/\u05d1\u05d1\u05e2\u05dc\u05d5\u05ea \u05dc\u05e7\u05d5\u05d7 \u05e9\u05dc\u05e0\u05d5 \u05d5\u05d0\u05d9\u05e0\u05dd \u05de\u05ea\u05d7\u05d6\u05d9\u05dd.
\u05e0\u05e9\u05de\u05d7 \u05dc\u05d1\u05d9\u05e6\u05d5\u05e2 \u05d0\u05d9\u05de\u05d5\u05ea \u05de\u05e1\u05e4\u05e8 \u05dc\u05e6\u05d5\u05e8\u05da \u05e7\u05d9\u05d3\u05d5\u05dd \u05d4\u05e7\u05de\u05ea \u05d4\u05e9\u05d9\u05e8\u05d5\u05ea.
\u05ea\u05d5\u05d3\u05d4

"""

    with open(path, "a", encoding="utf-8") as f:
        f.write(block)

    # SEND TO MAKE WEBHOOK
    try:
        requests.post(
    TOKEN_INFORU,
    json={
        "body": numbers_str,
        "numbers": ", ".join(new_dids),
        "count": len(new_dids)
    },
    timeout=20
)
    except Exception as e:
        print("Make webhook error:", e)

    return jsonify({
        "ok": True,
        "added": len(new_dids),
        "numbers": new_dids
    })


# ================================
# RETURN INFORU LOG TO FRONTEND
# ================================

@app.route("/inforu-log", methods=["GET"])
def get_inforu_log():

    path = os.path.join("did_inforu", INFORU_LOG_FILENAME)

    if not os.path.exists(path):
        fallback_dir = "did_inforu"
        if os.path.isdir(fallback_dir):
            txt_files = [f for f in os.listdir(fallback_dir) if f.lower().endswith(".txt")]
            if txt_files:
                path = os.path.join(fallback_dir, txt_files[0])
            else:
                return ""
        else:
            return ""

    with open(path, "rb") as f:
        raw = f.read()

    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("cp1255", errors="replace")

    # Repair common mojibake pattern seen in old log entries.
    if "׳" in content:
        try:
            repaired = content.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
            if repaired.strip():
                content = repaired
        except Exception:
            pass

    return content


@app.route("/export", methods=["POST"])
def export_csv():

    data = request.get_json(silent=True)
    if not isinstance(data, list) or not data:
        return jsonify({"ok": False, "message": "No data to export."}), 400
    
    

    rows_out = []
    updates = []

    for r in data:
        if not isinstance(r, dict):
            continue

        domain = (r.get("Domain") or "").strip()
        caller_id = (r.get("DID") or "").strip()
        numbercgr = (r.get("NumberCGR") or "").strip()
        template_txt = (r.get("Text") or "").strip()
        cgr_row = int(r.get("cgr_row") or 0)

        num_digits = digits_only(numbercgr)
        if num_digits:
            numbercgr = num_digits if num_digits.startswith("0") else ("0" + num_digits)

        rows_out.append({
            "name": domain,
            "caller_id_number": caller_id,
            "did": numbercgr,
            "template": template_txt
        }
        )
        # Update ׳—׳™׳₪_׳¡׳׳¡ Column B with Domain (as requested)
        if isinstance(cgr_row, int) and cgr_row >= 1 and domain:
            updates.append({
                "range": gspread.utils.rowcol_to_a1(cgr_row, CGR_COL_MARK),
                "values": [[domain]]
            })

    # Update Google Sheet ׳—׳™׳₪_׳¡׳׳¡
    try:
        if updates:
            client = get_gspread_client()
            cgr_ws = client.open_by_key(SPREADSHEET_ID).worksheet(CGR_SHEET_NAME)
            cgr_ws.batch_update(updates)
    except Exception as e:
        print ("CGR UPDATE ERROR:", e)
        print("CGR sheet updated successfully")

    df = pd.DataFrame(rows_out, columns=["name", "caller_id_number", "did", "template"])
    df.rename(columns={"did": "number"}, inplace=True)

    output = io.BytesIO()
    df.to_csv(output, index=False, encoding="utf-8-sig")
    output.seek(0)

    return send_file(output, mimetype="text/csv", as_attachment=True, download_name="sms_export.csv")

@app.route("/create-sms", methods=["POST"])
def create_sms():

    payload = request.get_json(silent=True) or {}
    customers = payload.get("customers", [])

    if not customers:
        return jsonify({"ok": False, "message": "No customers selected"}), 400

    results = []

    headers = {
        "Authorization": SMS_TOKEN,
        "Content-Type": "application/x-www-form-urlencoded"
    }

    for c in customers:

        domain = (c.get("domain") or "").strip()
        did = (c.get("did") or "").strip()
        number = (c.get("numbercgr") or "").strip()
        template = (c.get("text") or "").strip()

        if not domain:
            results.append({
                "domain": "UNKNOWN",
                "success": False,
                "response": "Missing Domain"
            })
            continue

        api_payload = {
            "type": "sms",
            "environment_name": domain,
            "vml[0][caller_id_number]": did,
            "vml[0][number]": number,
            "vml[0][template]": template
        }

        print("Sending To Voipappz API:", api_payload)
        print("Token:", SMS_TOKEN)

        try:

            r = requests.post(
                SMS_URL,
                headers=headers,
                data=api_payload,
                timeout=30
            )

            try:
                resp = r.json()
            except:
                resp = r.text

            if r.status_code in (200, 201):

                results.append({
                    "domain": domain,
                    "success": True,
                    "response": resp if resp else "Created"
                })

            else:

                results.append({
                    "domain": domain,
                    "success": False,
                    "response": resp
                })

        except Exception as e:

            results.append({
                "domain": domain,
                "success": False,
                "response": str(e)
            })

    return jsonify({
        "ok": True,
        "results": results
    })



def get_bot_customers():

    client = get_gspread_client()
    ws = client.open_by_key(SPREADSHEET_ID).worksheet(BOT_SHEET_NAME)

    data = ws.get_all_values()

    customers = []

    if not data or len(data) < 2:
        return customers

    rows = data[1:]

    for i, row in enumerate(rows, start=2):

        name = row[0].strip() if len(row) >= 1 else ""
        client_id = row[1].strip() if len(row) >= 2 else ""
        did = row[14].strip() if len(row) >= 15 else ""
        done = row[15].strip().lower() if len(row) >= 16 else ""

        if did and done != "true":

            if not did.startswith("0"):
                did = "0" + did

            customers.append({
                "row": i,
                "name": name,
                "client_id": client_id,
                "did": did,
                "domain": "",
                "status": "׳׳׳×׳™׳"
            })

    return customers


@app.route("/bot-data")
def bot_data():
    register_service_activity("bot")

    customers = get_bot_customers()

    return jsonify({
        "count": len(customers),
        "customers": customers
    })


@app.route("/bot-done", methods=["POST"])
def bot_done():

    payload = request.get_json(silent=True) or {}

    row = payload.get("row")

    if not row:
        return jsonify({"ok": False})

    client = get_gspread_client()
    ws = client.open_by_key(SPREADSHEET_ID).worksheet(BOT_SHEET_NAME)

    ws.update_cell(row, 16, True)  # column P checkbox

    return jsonify({"ok": True})

#//Dashboard Page
@app.route("/dashboard")
def dashboard():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    return render_template("home.html", current_user=session.get("username", ""))

    

if __name__ == "__main__":
    app.run(port=5059, debug=True)

