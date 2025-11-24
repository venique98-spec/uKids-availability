# app_fixed.py
import re
import time
import random
from io import BytesIO
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st

# Try to import gspread (optional). If missing, app still runs in Local mode.
try:
    import gspread
    from gspread.exceptions import APIError
except Exception:
    gspread = None
    class APIError(Exception): ...
# -----------------------------------------------------------------------------
# APP CONFIG
# -----------------------------------------------------------------------------
st.set_page_config(page_title="Availability Form", page_icon="🗓️", layout="centered")
st.title("🗓️ Availability Form")


def apply_mobile_tweaks():
    st.markdown("""
    <style>
      .stButton > button { width: 100%; height: 48px; font-size: 16px; }
      label[data-baseweb="radio"] { padding: 6px 0; }
      @media (max-width: 520px){
        div[data-testid="column"] { width: 100% !important; flex: 0 0 100% !important; }
        pre, code {
          white-space: pre-wrap !important;
          word-break: break-word !important;
          overflow-wrap: anywhere !important;
          font-size: 16px; line-height: 1.45;
        }
      }
      /* Screenshot-friendly report */
      .report-box {
        white-space: pre-wrap;
        word-break: break-word;
        overflow-wrap: anywhere;
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
        font-size: 16px;
        line-height: 1.5;
        padding: 12px 14px;
        background: #0e1117;
        color: #e6edf3;
        border-radius: 8px;
        border: 1px solid #2d333b;
      }
      @media (max-width: 520px){
        .report-box { font-size: 17px; line-height: 1.6; padding: 14px; }
      }
      .sticky-submit {
        position: sticky; bottom: 0; z-index: 999;
        background: #fff; padding: 10px 0; border-top: 1px solid #eee;
      }
    </style>
    """, unsafe_allow_html=True)

apply_mobile_tweaks()

# CSVs expected in ./data
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
FQ_PATH = DATA_DIR / "Form questions.csv"
SB_PATH = DATA_DIR / "Serving base with allocated directors.csv"
LOCAL_RESP_PATH = DATA_DIR / "responses_local.csv"

# -----------------------------------------------------------------------------
# SECRETS / ADMIN KEY
# -----------------------------------------------------------------------------
def _get_secret_any(*paths):
    """Return secrets value by trying several key paths (top-level or sectioned)."""
    try:
        cur = st.secrets
    except Exception:
        return None
    for path in paths:
        c = cur; ok = True
        for k in path:
            if k in c:
                c = c[k]
            else:
                ok = False; break
        if ok:
            return c
    return None

def get_admin_key() -> str:
    v = _get_secret_any(["ADMIN_KEY"], ["general", "ADMIN_KEY"])
    return str(v) if v else ""

ADMIN_KEY = get_admin_key()

# -----------------------------------------------------------------------------
# MODE: Google Sheets (if secrets + gspread) OR Local CSV fallback
# -----------------------------------------------------------------------------
def is_sheets_enabled() -> bool:
    if gspread is None:
        return False
    sa = _get_secret_any(["gcp_service_account"], ["general", "gcp_service_account"])
    sid = _get_secret_any(["GSHEET_ID"], ["general", "GSHEET_ID"])
    return bool(sa and sid)

SHEETS_MODE = is_sheets_enabled()

# -----------------------------------------------------------------------------
# COLUMN NORMALIZATION HELPERS
# -----------------------------------------------------------------------------
def _norm_col(s: str) -> str:
    return str(s).replace("\u00A0", " ").replace("\u200B", "").strip().lower()

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).replace("\u00A0", " ").replace("\u200B", "").strip() for c in df.columns]
    return df

def pick_report_label_col(df: pd.DataFrame):
    candidates = ["report label", "reportlabel", "label"]
    cmap = {_norm_col(c): c for c in df.columns}
    for cand in candidates:
        if cand in cmap:
            return cmap[cand]
    return None

def has_cols(df: pd.DataFrame, required: set[str]) -> set[str]:
    low = {_norm_col(c) for c in df.columns}
    return {c for c in required if _norm_col(c) not in low}

def get_col(df: pd.DataFrame, name: str) -> str:
    cmap = {_norm_col(c): c for c in df.columns}
    return cmap.get(_norm_col(name), name)

REPORT_LABEL_COL = None  # set after load_data()

# -----------------------------------------------------------------------------
# DATA LOADERS
# -----------------------------------------------------------------------------
def read_csv_local(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    # Try multiple encodings and auto-delimiter
    for enc in ("utf-8-sig", "cp1252", "latin1", "utf-8"):
        try:
            return pd.read_csv(path, encoding=enc, sep=None, engine="python")
        except Exception:
            pass
    sample = path.read_text(errors="ignore")
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    return pd.read_csv(path, encoding="latin1", sep=delimiter, engine="python")

@st.cache_data(show_spinner=False)
def load_data():
    fq = read_csv_local(FQ_PATH)
    sb = read_csv_local(SB_PATH)
    fq = normalize_columns(fq)
    sb = normalize_columns(sb)

    required_fq = {"QuestionID", "QuestionText", "QuestionType", "Options Source", "DependsOn", "Show Condition"}
    missing_fq = has_cols(fq, required_fq)
    if missing_fq:
        raise RuntimeError(f"`Form questions.csv` missing columns: {', '.join(sorted(missing_fq))}")

    required_sb = {"Director", "Serving Girl"}
    missing_sb = has_cols(sb, required_sb)
    if missing_sb:
        raise RuntimeError(f"`Serving base with allocated directors.csv` missing columns: {', '.join(sorted(missing_sb))}")

    fq = fq.assign(
        **{
            get_col(fq, "QuestionID"): fq[get_col(fq, "QuestionID")].astype(str).str.strip(),
            get_col(fq, "QuestionText"): fq[get_col(fq, "QuestionText")].astype(str).str.strip(),
            get_col(fq, "QuestionType"): fq[get_col(fq, "QuestionType")].astype(str).str.strip(),
            get_col(fq, "Options Source"): fq[get_col(fq, "Options Source")].astype(str).str.strip(),
            get_col(fq, "DependsOn"): fq[get_col(fq, "DependsOn")].astype(str).str.strip(),
            get_col(fq, "Show Condition"): fq[get_col(fq, "Show Condition")].astype(str).str.strip(),
        }
    )
    sb = sb.assign(
        Director=sb[get_col(sb, "Director")].astype(str).str.strip(),
        **{"Serving Girl": sb[get_col(sb, "Serving Girl")].astype(str).str.strip()}
    )

    serving_map = (
        sb.groupby("Director")["Serving Girl"]
        .apply(lambda s: sorted({x for x in s if x}))
        .to_dict()
    )
    return fq, sb, serving_map

# -----------------------------------------------------------------------------
# GOOGLE SHEETS HELPERS (used only if SHEETS_MODE True)
# -----------------------------------------------------------------------------
def gs_retry(func, *args, **kwargs):
    """Call a gspread function with retries (handles 429/5xx bursts)."""
    for attempt in range(5):
        try:
            return func(*args, **kwargs)
        except APIError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (429, 500, 502, 503):
                time.sleep(min(10, (2 ** attempt) + random.random()))
                continue
            raise

@st.cache_resource
def get_worksheet():
    if not SHEETS_MODE:
        raise RuntimeError("Sheets mode disabled (missing secrets or gspread not installed).")
    sa_dict = _get_secret_any(["gcp_service_account"], ["general", "gcp_service_account"])
    sheet_id = _get_secret_any(["GSHEET_ID"], ["general", "GSHEET_ID"])
    gc = gspread.service_account_from_dict(sa_dict)
    try:
        sh = gs_retry(gc.open_by_key, sheet_id)
        return sh.sheet1
    except Exception as e:
        raise RuntimeError(f"Failed to open Google Sheet with ID '{sheet_id}': {e}. Please check that the sheet exists and is shared with your service account email.")

@st.cache_resource
def init_sheet_headers(desired_header: list[str]) -> list[str]:
    ws = get_worksheet()
    header = gs_retry(ws.row_values, 1)
    if not header:
        gs_retry(ws.update, "1:1", [desired_header])
        return desired_header
    missing = [c for c in desired_header if c not in header]
    if missing:
        header = header + missing
        gs_retry(ws.update, "1:1", [header])
    return header

def sheet_get_df(ws) -> pd.DataFrame:
    values = gs_retry(ws.get_all_values)
    if not values:
        return pd.DataFrame()
    header, rows = values[0], values[1:]
    return pd.DataFrame(rows, columns=header)

@st.cache_data(ttl=30, show_spinner=False)
def fetch_responses_df_sheets() -> pd.DataFrame:
    ws = get_worksheet()
    return sheet_get_df(ws)

# -----------------------------------------------------------------------------
# LOCAL CSV FALLBACK HELPERS (used if Sheets mode is off)
# -----------------------------------------------------------------------------
def ensure_local_headers(desired_header: list[str]) -> list[str]:
    if not LOCAL_RESP_PATH.exists():
        pd.DataFrame(columns=desired_header).to_csv(LOCAL_RESP_PATH, index=False)
        return desired_header
    try:
        df = pd.read_csv(LOCAL_RESP_PATH)
    except Exception:
        df = pd.DataFrame(columns=desired_header)
    missing = [c for c in desired_header if c not in df.columns]
    if missing:
        for col in missing:
            df[col] = ""
        df = df[[c for c in desired_header]]
        df.to_csv(LOCAL_RESP_PATH, index=False)
        return desired_header
    return list(df.columns)

def append_row_local(header: list[str], row_map: dict):
    row = {k: row_map.get(k, "") for k in header}
    try:
        df = pd.read_csv(LOCAL_RESP_PATH)
    except Exception:
        df = pd.DataFrame(columns=header)
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.to_csv(LOCAL_RESP_PATH, index=False)

@st.cache_data(ttl=30, show_spinner=False)
def fetch_responses_df_local() -> pd.DataFrame:
    if not LOCAL_RESP_PATH.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(LOCAL_RESP_PATH)
    except Exception:
        return pd.DataFrame()

def clear_responses_cache():
    for fn in (fetch_responses_df_sheets, fetch_responses_df_local):
        try:
            fn.clear()
        except Exception:
            pass
    try:
        st.cache_data.clear()
    except Exception:
        pass

# -----------------------------------------------------------------------------
# REPORT / LABEL HELPERS
# -----------------------------------------------------------------------------
def extract_date_from_label(label: str) -> str:
    """
    Fallback: convert question text like
    'Are you available the 5th of November?' -> '5 November'
    or 'Are you available 5 November?' -> '5 November'
    """
    text = str(label).strip()
    month_map = {
        "jan": "January", "january": "January",
        "feb": "February", "february": "February",
        "mar": "March", "march": "March",
        "apr": "April", "april": "April",
        "may": "May",
        "jun": "June", "june": "June",
        "jul": "July", "july": "July",
        "aug": "August", "august": "August",
        "sep": "September", "september": "September",
        "sept": "September",
        "oct": "October", "october": "October",
        "nov": "November", "november": "November",
        "dec": "December", "december": "December",
    }
    m = re.search(r'(\d{1,2})(?:st|nd|rd|th)?\s+of\s+([A-Za-z]+)', text, flags=re.I)
    if m:
        day = m.group(1)
        mon = month_map.get(m.group(2).lower(), m.group(2).title())
        return f"{day} {mon}"
    m2 = re.search(r'(\d{1,2})\s+([A-Za-z]+)', text, flags=re.I)
    if m2:
        day = m2.group(1)
        mon = month_map.get(m2.group(2).lower(), m2.group(2).title())
        return f"{day} {mon}"
    return text

def get_report_label(row) -> str:
    global REPORT_LABEL_COL
    if REPORT_LABEL_COL and REPORT_LABEL_COL in row and str(row[REPORT_LABEL_COL]).strip():
        return str(row[REPORT_LABEL_COL]).strip()
    return extract_date_from_label(str(row.get(get_col(form_questions, "QuestionText"), "")).strip())

def yesno_labels(form_questions: pd.DataFrame) -> list[str]:
    labels = []
    rows = form_questions[form_questions[get_col(form_questions, "Options Source")].astype(str).str.lower() == "yes_no"]
    for _, r in rows.iterrows():
        lbl = get_report_label(r)
        if lbl not in labels:
            labels.append(lbl)
    return labels

def yes_count(answers: dict, ids):
    return sum(1 for qid in ids if str(answers.get(qid, "")).lower() == "yes")

# ----- Reason question helpers (dynamic, based on your CSV) -------------------
def find_reason_row(form_questions: pd.DataFrame):
    rl_col = get_col(form_questions, "Report Label")
    if rl_col is not None and rl_col in form_questions.columns:
        mask = form_questions[rl_col].astype(str).str.strip().str.lower() == "reason"
        if mask.any():
            return form_questions[mask].iloc[0]
    # Fallback: first text question
    mask_text = form_questions[get_col(form_questions, "QuestionType")].astype(str).str.lower() == "text"
    if mask_text.any():
        return form_questions[mask_text].iloc[0]
    return None  # no reason question present

def parse_yescount_condition(cond_str: str):
    """
    Parse patterns like: 'yes_count<3', 'yes_count<=2', 'yes_count==2', 'yes_count>=4'
    Returns (operator, threshold). Defaults to ('>=', 1) if not provided.
    """
    if not cond_str or not cond_str.strip():
        return ">=", 1
    m = re.search(r'yes_count\s*(<=|<|>=|>|==|=)\s*(\d+)', str(cond_str).strip(), flags=re.I)
    if not m:
        return ">=", 1
    op = m.group(1)
    if op == "=": op = "=="
    threshold = int(m.group(2))
    return op, threshold

def eval_yescount_condition(yes_ct: int, op: str, n: int) -> bool:
    if op == "<":  return yes_ct < n
    if op == "<=": return yes_ct <= n
    if op == ">":  return yes_ct > n
    if op == ">=": return yes_ct >= n
    if op == "==": return yes_ct == n
    return yes_ct >= n

# -----------------------------------------------------------------------------
# LOAD DATA
# -----------------------------------------------------------------------------
try:
    form_questions, serving_base, serving_map = load_data()
except Exception as e:
    st.error(f"Data load error: {e}")
    with st.expander("Debug info"):
        st.code(str(FQ_PATH)); st.code(str(SB_PATH))
        try:
            st.write("Directory listing of ./data:", [p.name for p in DATA_DIR.iterdir()])
        except Exception:
            st.write("Could not list ./data")
    st.stop()

REPORT_LABEL_COL = pick_report_label_col(form_questions)
if not REPORT_LABEL_COL:
    st.warning("No 'Report Label' column found (exact match not detected). Using auto-detected labels.")

# Cache reason row + id
_REASON_ROW = find_reason_row(form_questions)
if _REASON_ROW is not None:
    REASON_QID = str(_REASON_ROW[get_col(form_questions, "QuestionID")])
else:
    REASON_QID = None

# -----------------------------------------------------------------------------
# STATE
# -----------------------------------------------------------------------------
if "answers" not in st.session_state:
    st.session_state.answers = {}
answers = st.session_state.answers

# -----------------------------------------------------------------------------
# FORM UI
# -----------------------------------------------------------------------------
st.subheader("Your details")

directors = sorted([d for d in serving_map.keys() if d])
answers["Q1"] = st.selectbox("Please select your director’s name", options=[""] + directors, index=0)

if answers.get("Q1"):
    girls = serving_map.get(answers["Q1"], [])
    answers["Q2"] = st.selectbox("Please select your name", options=[""] + girls, index=0)
else:
    answers["Q2"] = ""

st.subheader("Availability in December")
availability_questions = form_questions[
    form_questions[get_col(form_questions, "Options Source")].astype(str).str.lower() == "yes_no"
].copy()

# Radios with placeholder (no default "No")
for _, q in availability_questions.iterrows():
    qid = str(q[get_col(form_questions, "QuestionID")])
    qtext = str(q[get_col(form_questions, "QuestionText")])
    options = ["—", "Yes", "No"]
    current = answers.get(qid, "")
    idx = options.index(current) if current in ("Yes", "No") else 0
    choice = st.radio(qtext, options, index=idx, key=qid, horizontal=False)
    answers[qid] = "" if choice == "—" else choice

# ----- Conditional REASON (dynamic) ------------------------------------------
dep_ids = []
show_reason = False
if _REASON_ROW is not None:
    reason_text = str(_REASON_ROW[get_col(form_questions, "QuestionText")])
    # DependsOn parsing
    dep_ids = [s.strip() for s in str(_REASON_ROW.get(get_col(form_questions, "DependsOn"), "")).split(",") if s.strip()]

    # Fallback to all yes/no question IDs if DependsOn empty
    if not dep_ids:
        dep_ids = form_questions.loc[
            form_questions[get_col(form_questions, "Options Source")].astype(str).str.lower() == "yes_no",
            get_col(form_questions, "QuestionID")
        ].astype(str).tolist()

    op, threshold = parse_yescount_condition(str(_REASON_ROW.get(get_col(form_questions, "Show Condition"), "")))
    show_reason = eval_yescount_condition(yes_count(answers, dep_ids), op, threshold)

    if show_reason:
        answers[REASON_QID] = st.text_area(reason_text, value=answers.get(REASON_QID, ""))
    else:
        answers[REASON_QID] = answers.get(REASON_QID, "")

# Review
st.subheader("Review")
yes_ids = form_questions[
    form_questions[get_col(form_questions, "Options Source")].str.lower() == "yes_no"
][get_col(form_questions, "QuestionID")].astype(str).tolist()
c1, c2, c3 = st.columns(3)
with c1: st.metric("Director", answers.get("Q1") or "—")
with c2: st.metric("Name", answers.get("Q2") or "—")
with c3: st.metric("Yes count", yes_count(answers, yes_ids))

# -----------------------------------------------------------------------------
# SUBMIT (sticky, full-width)
# -----------------------------------------------------------------------------
errors = {}
st.markdown('<div class="sticky-submit">', unsafe_allow_html=True)
submitted = st.button("Submit")
st.markdown('</div>', unsafe_allow_html=True)

if submitted:
    if not answers.get("Q1"):
        errors["Q1"] = "Please select a director."
    if not answers.get("Q2"):
        errors["Q2"] = "Please select your name."

    # Ensure all availability questions answered
    unanswered = [
        str(r[get_col(form_questions, "QuestionID")])
        for _, r in availability_questions.iterrows()
        if not answers.get(str(r[get_col(form_questions, "QuestionID")]))
    ]
    if unanswered:
        errors["unanswered"] = "Please answer all availability items."

    # Validate Reason only if condition requires it
    if _REASON_ROW is not None:
        dep_ids = [s.strip() for s in str(_REASON_ROW.get(get_col(form_questions, "DependsOn"), "")).split(",") if s.strip()]
        if not dep_ids:
            dep_ids = yes_ids
        op, threshold = parse_yescount_condition(str(_REASON_ROW.get(get_col(form_questions, "Show Condition"), "")))
        need_reason = eval_yescount_condition(yes_count(answers, dep_ids), op, threshold)
        if need_reason:
            if not answers.get(REASON_QID) or len(str(answers[REASON_QID]).strip()) < 5:
                errors[REASON_QID] = "Please provide a brief reason (at least 5 characters)."

    if errors:
        for v in errors.values():
            st.error(v)
    else:
        now = datetime.utcnow().isoformat() + "Z"

        # Build labels directly from the questions we're saving from (avoid drift)
        labels_in_use = []
        for _, r in availability_questions.iterrows():
            lbl = get_report_label(r)
            if lbl not in labels_in_use:
                labels_in_use.append(lbl)
        if len(set(labels_in_use)) != len(labels_in_use):
            st.warning("Some availability labels are duplicated; headers may clash.")

        row_map = {
            "timestamp": now,
            "Director": answers.get("Q1") or "",
            "Serving Girl": answers.get("Q2") or "",
        }

        # Stable Reason column name
        reason_col_name = "Reason"
        row_map[reason_col_name] = str(answers.get(REASON_QID, "")).strip() if _REASON_ROW is not None else ""

        # Save the Yes/No values (we already required all answered)
        for _, r in availability_questions.iterrows():
            qid = str(r[get_col(form_questions, "QuestionID")])
            label = get_report_label(r)
            val = (answers.get(qid) or "").title()  # "Yes" / "No"
            row_map[label] = val

        desired_header = ["timestamp", "Director", "Serving Girl", reason_col_name] + labels_in_use

        try:
            if SHEETS_MODE:
                ws = get_worksheet()
                header = init_sheet_headers(desired_header)
                row = [row_map.get(col, "") for col in header]
                gs_retry(ws.append_row, row)
                clear_responses_cache()
                st.success("Submission saved to Google Sheets.")
            else:
                header = ensure_local_headers(desired_header)
                append_row_local(header, row_map)
                clear_responses_cache()
                st.success("Submission saved (local file).")
        except Exception as e:
            st.error(f"Failed to save submission: {e}")

        # Text report for screenshot / sharing (mobile-wrapped)
        def build_human_report(form_questions: pd.DataFrame, answers: dict) -> str:
            director = answers.get("Q1") or "—"
            name = answers.get("Q2") or "—"
            lines = [f"Director: {director}", f"Serving Girl: {name}", "Availability:"]
            rows = form_questions[form_questions[get_col(form_questions, "Options Source")].astype(str).str.lower() == "yes_no"]
            for _, r in rows.iterrows():
                qid = str(r[get_col(form_questions, "QuestionID")])
                label = get_report_label(r)
                val = (answers.get(qid) or "").title()
                lines.append(f"{label}: {val}")
            # Reason (if present)
            if _REASON_ROW is not None:
                reason_text_val = (answers.get(REASON_QID) or "").strip()
                if reason_text_val:
                    lines.append(f"Reason: {reason_text_val}")
            return "\n".join(lines)

        report_text = build_human_report(form_questions, answers)
        st.markdown("### 📄 Screenshot-friendly report (text)")

        from html import escape
        st.markdown(f'<pre class="report-box">{escape(report_text)}</pre>', unsafe_allow_html=True)

        st.download_button(
            "Download report as .txt",
            data=report_text.encode("utf-8"),
            file_name=f"Availability_{(answers.get('Q2') or 'name').replace(' ', '_')}.txt",
            mime="text/plain",
        )

# -----------------------------------------------------------------------------
# ADMIN (exports + non-responders + diagnostics)
# -----------------------------------------------------------------------------
with st.expander("Admin"):
    st.caption(f"Mode: {'Google Sheets' if SHEETS_MODE else 'Local CSV'}")
    if not ADMIN_KEY:
        st.info("To protect exports, set an ADMIN_KEY in Streamlit Secrets (optional).")

    key = st.text_input("Enter admin key to access exports", type="password")
    if ADMIN_KEY and key != ADMIN_KEY:
        if key:
            st.error("Incorrect admin key.")
    else:
        st.success("Admin unlocked.")

        # Load responses from active store
        try:
            if SHEETS_MODE:
                responses_df = fetch_responses_df_sheets()
            else:
                responses_df = fetch_responses_df_local()
        except Exception as e:
            st.error(f"Could not load responses: {e}")
            responses_df = pd.DataFrame()

        st.write(f"Total submissions: **{len(responses_df)}**")

        if not responses_df.empty:
            st.dataframe(responses_df, use_container_width=True)

            # Export helper
            def make_download_payload(df: pd.DataFrame):
                try:
                    import openpyxl  # noqa
                    out = BytesIO()
                    with pd.ExcelWriter(out, engine="openpyxl") as xw:
                        df.to_excel(xw, index=False, sheet_name="Responses")
                    return out.getvalue(), "uKids_availability_responses.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                except Exception:
                    csv_bytes = df.to_csv(index=False).encode("utf-8")
                    return csv_bytes, "uKids_availability_responses.csv", "text/csv"

            bytes_data, fname, mime = make_download_payload(responses_df)
            st.download_button("Download all responses", data=bytes_data, file_name=fname, mime=mime)
        else:
            st.warning("No submissions yet.")

        st.markdown("### ❌ Non-responders")
        def compute_nonresponders(serving_base_df: pd.DataFrame, responses_df: pd.DataFrame) -> pd.DataFrame:
            if serving_base_df is None or serving_base_df.empty:
                return pd.DataFrame(columns=["Director", "Serving Girl"])
            sb = serving_base_df[["Director", "Serving Girl"]].copy()
            sb["Director"] = sb["Director"].astype(str).str.strip()
            sb["Serving Girl"] = sb["Serving Girl"].astype(str).str.strip()
            sb = sb[(sb["Director"] != "") & (sb["Serving Girl"] != "")].drop_duplicates()

            if responses_df is None or responses_df.empty:
                out = sb.copy()
                out["Responded"] = False
                out["Last submission"] = pd.NaT
                return out

            cols = list(responses_df.columns)
            ts_col = "timestamp" if "timestamp" in cols else (cols[0] if cols else "timestamp")
            use_cols = [c for c in cols if c in ["Director", "Serving Girl"] or c == ts_col]
            resp = responses_df[use_cols].copy()
            if ts_col not in resp.columns:
                resp[ts_col] = ""
            resp.rename(columns={ts_col: "Last submission"}, inplace=True)
            resp["Director"] = resp["Director"].astype(str).str.strip()
            resp["Serving Girl"] = resp["Serving Girl"].astype(str).str.strip()

            # Coerce timestamp for proper sorting
            resp["Last submission"] = pd.to_datetime(resp["Last submission"], errors="coerce")
            resp = resp.sort_values("Last submission").drop_duplicates(subset=["Director", "Serving Girl"], keep="last")

            merged = sb.merge(resp, on=["Director", "Serving Girl"], how="left")
            merged["Responded"] = merged["Last submission"].notna().fillna(False)
            nonresp = merged.loc[~merged["Responded"]].copy()
            return nonresp.sort_values(["Director", "Serving Girl"]).reset_index(drop=True)

        nonresp_df = compute_nonresponders(serving_base, responses_df)
        all_directors = ["All"] + sorted(serving_base["Director"].dropna().astype(str).str.strip().unique().tolist())
        sel_dir = st.selectbox("Filter by director", options=all_directors, index=0)
        view_df = nonresp_df if sel_dir == "All" else nonresp_df[nonresp_df["Director"] == sel_dir]
        total_expected = len(serving_base[["Director", "Serving Girl"]].dropna().drop_duplicates())
        st.write(f"Non-responders shown: **{len(view_df)}**  |  Total expected pairs: **{total_expected}**")
        st.dataframe(view_df[["Director", "Serving Girl"]], use_container_width=True)

        # export NR
        def make_download_payload2(df: pd.DataFrame):
            try:
                import openpyxl  # noqa
                out = BytesIO()
                with pd.ExcelWriter(out, engine="openpyxl") as xw:
                    df.to_excel(xw, index=False, sheet_name="NonResponders")
                return out.getvalue(), "non_responders.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            except Exception:
                csv_bytes = df.to_csv(index=False).encode("utf-8")
                return csv_bytes, "non_responders.csv", "text/csv"

        nr_bytes, nr_name, nr_mime = make_download_payload2(view_df[["Director", "Serving Girl"]])
        st.download_button("Download non-responders", data=nr_bytes, file_name=nr_name, mime=nr_mime)

        st.divider()
        st.markdown("#### 🔍 Secrets / Sheets check")
        try:
            s = st.secrets
            gsa = s.get("gcp_service_account", {})
            gs_id = s.get("GSHEET_ID") or s.get("general", {}).get("GSHEET_ID")
            st.write({
                "has_gcp_service_account_block": bool(gsa),
                "GSHEET_ID_present": bool(gs_id),
                "GSHEET_ID_value": gs_id[:20] + "..." if gs_id and len(gs_id) > 20 else gs_id,
                "client_email": gsa.get("client_email", "(missing)"),
                "private_key_id_present": bool(gsa.get("private_key_id")),
                "private_key_length": len(gsa.get("private_key", "")),
                "gspread_installed": gspread is not None,
            })
            if gspread is None:
                st.warning("gspread not installed. Add 'gspread' and 'google-auth' to requirements.txt and reboot.")
            elif gsa and gs_id:
                try:
                    gc = gspread.service_account_from_dict(gsa)
                    sh = gc.open_by_key(gs_id)
                    st.success(f"✅ Auth OK. Opened sheet: {sh.title}")
                except Exception as e:
                    st.error(f"❌ Auth test error: {e}")
            else:
                st.info("Secrets incomplete. Ensure [gcp_service_account] block and GSHEET_ID are set, then reboot.")
        except Exception as e:
            st.error(f"❌ Diagnostics failed: {e}")
