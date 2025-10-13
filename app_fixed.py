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
    class APIError(Exception): pass  # dummy so references compile

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
        pre, code { font-size: 15px; line-height: 1.35; }
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
# COLUMN NORMALIZATION
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
    missing_fq = required_fq - set(fq.columns)
    if missing_fq:
        raise RuntimeError(f"`Form questions.csv` missing columns: {', '.join(sorted(missing_fq))}")

    required_sb = {"Director", "Serving Girl"}
    missing_sb = required_sb - set(sb.columns)
    if missing_sb:
        raise RuntimeError(f"`Serving base with allocated directors.csv` missing columns: {', '.join(sorted(missing_sb))}")

    fq = fq.assign(
        QuestionID=fq["QuestionID"].astype(str).str.strip(),
        QuestionText=fq["QuestionText"].astype(str).str.strip(),
        QuestionType=fq["QuestionType"].astype(str).str.strip(),
        **{
            "Options Source": fq["Options Source"].astype(str).str.strip(),
            "DependsOn": fq["DependsOn"].astype(str).str.strip(),
            "Show Condition": fq["Show Condition"].astype(str).str.strip(),
        }
    )
    sb = sb.assign(
        Director=sb["Director"].astype(str).str.strip(),
        **{"Serving Girl": sb["Serving Girl"].astype(str).str.strip()}
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
# REPORT HELPERS
# -----------------------------------------------------------------------------
def extract_date_from_label(label: str) -> str:
    """Fallback: 'Are you available the 5th of October?' -> '5 October'."""
    m = re.search(r'(\d{1,2})(?:st|nd|rd|th)?\s+of\s+(October|Nov|November|Oct)', label, flags=re.I)
    if m:
        return f"{m.group(1)} October"
    m2 = re.search(r'(\d{1,2})\s+(October)', label, flags=re.I)
    if m2:
        return f"{m2.group(1)} {m2.group(2).title()}"
    return label.strip()

def get_report_label(row) -> str:
    global REPORT_LABEL_COL
    if REPORT_LABEL_COL and REPORT_LABEL_COL in row and str(row[REPORT_LABEL_COL]).strip():
        return str(row[REPORT_LABEL_COL]).strip()
    return extract_date_from_label(str(row.get("QuestionText", "")).strip())

def yesno_labels(form_questions: pd.DataFrame) -> list[str]:
    labels = []
    rows = form_questions[form_questions["Options Source"].astype(str).str.lower() == "yes_no"]
    for _, r in rows.iterrows():
        lbl = get_report_label(r)
        if lbl not in labels:
            labels.append(lbl)
    return labels

def yes_count(answers: dict, ids):
    return sum(1 for qid in ids if str(answers.get(qid, "")).lower() == "yes")

def build_human_report(form_questions: pd.DataFrame, answers: dict) -> str:
    director = answers.get("Q1") or "—"
    name = answers.get("Q2") or "—"
    lines = [f"Director: {director}", f"Serving Girl: {name}", "Availability:"]
    rows = form_questions[form_questions["Options Source"].astype(str).str.lower() == "yes_no"]
    for _, r in rows.iterrows():
        qid = str(r["QuestionID"])
        label = get_report_label(r)
        # FIXED: no stray characters; just title-case Yes/No
        val = (answers.get(qid) or "No").title()
        lines.append(f"{label}: {val}")
    reason = (answers.get("Q7") or "").strip()
    if reason:
        lines.append(f"Reason: {reason}")
    return "\n".join(lines)

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

# -----------------------------------------------------------------------------
# NON-RESPONDERS
# -----------------------------------------------------------------------------
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
        out["Last submission"] = ""
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
    resp = resp.sort_values("Last submission").drop_duplicates(subset=["Director", "Serving Girl"], keep="last")

    merged = sb.merge(resp, on=["Director", "Serving Girl"], how="left")
    merged["Responded"] = merged["Last submission"].notna() & (merged["Last submission"] != "")
    nonresp = merged[~merged["Responded"]].copy()
    return nonresp.sort_values(["Director", "Serving Girl"]).reset_index(drop=True)

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

st.subheader("Availability in November")
availability_questions = form_questions[form_questions["Options Source"].astype(str).str.lower() == "yes_no"].copy()

for _, q in availability_questions.iterrows():
    qid = str(q["QuestionID"])
    qtext = str(q["QuestionText"])
    current = answers.get(qid)
    idx = 0 if current == "Yes" else 1 if current == "No" else 1
    choice = st.radio(qtext, ["Yes", "No"], index=idx, key=qid, horizontal=False)
    answers[qid] = choice

# Conditional Q7
q7_row = form_questions[form_questions["QuestionID"].astype(str) == "Q7"]
dep_ids = []
if not q7_row.empty:
    q7 = q7_row.iloc[0]
    q7_text = str(q7["QuestionText"])
    dep_ids = [s.strip() for s in str(q7["DependsOn"]).split(",") if s.strip()]
    if yes_count(answers, dep_ids) < 2:
        answers["Q7"] = st.text_area(q7_text, value=answers.get("Q7", ""))
    else:
        answers["Q7"] = answers.get("Q7", "")

# Review
st.subheader("Review")
yes_ids = form_questions[form_questions["Options Source"].str.lower() == "yes_no"]["QuestionID"].astype(str).tolist()
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
    if not q7_row.empty and yes_count(answers, dep_ids) < 2:
        if not answers.get("Q7") or len(answers["Q7"].strip()) < 5:
            errors["Q7"] = "Please provide a brief reason (at least 5 characters)."

    if errors:
        for v in errors.values():
            st.error(v)
    else:
        now = datetime.utcnow().isoformat() + "Z"
        labels = yesno_labels(form_questions)
        row_map = {
            "timestamp": now,
            "Director": answers.get("Q1") or "",
            "Serving Girl": answers.get("Q2") or "",
            "Reason": (answers.get("Q7") or "").strip(),
        }
        for _, r in availability_questions.iterrows():
            qid = str(r["QuestionID"])
            label = get_report_label(r)
            row_map[label] = (answers.get(qid) or "No").title()

        try:
            if SHEETS_MODE:
                ws = get_worksheet()
                desired_header = ["timestamp", "Director", "Serving Girl", "Reason"] + labels
                header = init_sheet_headers(desired_header)
                row = [row_map.get(col, "") for col in header]
                gs_retry(ws.append_row, row)
                clear_responses_cache()
                st.success("Submission saved to Google Sheets.")
            else:
                desired_header = ["timestamp", "Director", "Serving Girl", "Reason"] + labels
                header = ensure_local_headers(desired_header)
                append_row_local(header, row_map)
                clear_responses_cache()
                st.success("Submission saved (local file).")
        except Exception as e:
            st.error(f"Failed to save submission: {e}")

        # Text report for screenshot / sharing
        report_text = build_human_report(form_questions, answers)
        st.markdown("### 📄 Screenshot-friendly report (text)")
        st.code(report_text, language=None)
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
            bytes_data, fname, mime = make_download_payload(responses_df)
            st.download_button("Download all responses", data=bytes_data, file_name=fname, mime=mime)
        else:
            st.warning("No submissions yet.")

        st.markdown("### ❌ Non-responders")
        nonresp_df = compute_nonresponders(serving_base, responses_df)
        all_directors = ["All"] + sorted(serving_base["Director"].dropna().astype(str).str.strip().unique().tolist())
        sel_dir = st.selectbox("Filter by director", options=all_directors, index=0)
        view_df = nonresp_df if sel_dir == "All" else nonresp_df[nonresp_df["Director"] == sel_dir]
        total_expected = len(serving_base[["Director", "Serving Girl"]].dropna().drop_duplicates())
        st.write(f"Non-responders shown: **{len(view_df)}**  |  Total expected pairs: **{total_expected}**")
        st.dataframe(view_df[["Director", "Serving Girl"]], use_container_width=True)
        nr_bytes, nr_name, nr_mime = make_download_payload(view_df[["Director", "Serving Girl"]])
        st.download_button("Download non-responders", data=nr_bytes, file_name="non_responders.xlsx", mime=nr_mime)

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
