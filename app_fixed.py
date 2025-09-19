# app_fixed.py
import re
from io import BytesIO
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st

# -----------------------------------------------------------------------------
# APP CONFIG
# -----------------------------------------------------------------------------
st.set_page_config(page_title="Availability Form", page_icon="🗓️", layout="centered")
st.title("🗓️ Availability Form")

# CSVs expected in ./data
FQ_PATH = Path(__file__).parent / "data" / "Form questions.csv"
SB_PATH = Path(__file__).parent / "data" / "Serving base with allocated directors.csv"

# -----------------------------------------------------------------------------
# SECRETS / ADMIN KEY (supports top-level or [general])
# -----------------------------------------------------------------------------
def get_admin_key() -> str:
    try:
        if "ADMIN_KEY" in st.secrets and st.secrets["ADMIN_KEY"]:
            return str(st.secrets["ADMIN_KEY"])
    except Exception:
        pass
    try:
        if "general" in st.secrets and "ADMIN_KEY" in st.secrets["general"]:
            v = st.secrets["general"]["ADMIN_KEY"]
            if v:
                return str(v)
    except Exception:
        pass
    return ""

ADMIN_KEY = get_admin_key()

# -----------------------------------------------------------------------------
# LOADERS
# -----------------------------------------------------------------------------
def read_csv_local(path: Path) -> pd.DataFrame:
    """Robust CSV reader for Excel/Sheets exports (BOM, ; delimiter, cp1252/utf8/latin1)."""
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")

    for enc in ("utf-8-sig", "cp1252", "latin1", "utf-8"):
        try:
            return pd.read_csv(path, encoding=enc, sep=None, engine="python")
        except Exception:
            pass

    try:
        sample = path.read_text(errors="ignore")
    except Exception:
        with open(path, "r", errors="ignore") as f:
            sample = f.read(4096)
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    return pd.read_csv(path, encoding="latin1", sep=delimiter, engine="python")

@st.cache_data(show_spinner=False)
def load_data():
    fq = read_csv_local(FQ_PATH)
    sb = read_csv_local(SB_PATH)

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

def yes_count(answers: dict, ids):
    return sum(1 for qid in ids if str(answers.get(qid, "")).lower() == "yes")

# -----------------------------------------------------------------------------
# IN-MEMORY SUBMISSION STORE
# -----------------------------------------------------------------------------
@st.cache_resource
def submission_store():
    return []  # list of dicts

def add_submission(payload: dict):
    store = submission_store()
    payload = dict(payload)
    payload["_timestamp"] = datetime.utcnow().isoformat() + "Z"
    store.append(payload)

def submissions_dataframe() -> pd.DataFrame:
    store = submission_store()
    if not store:
        return pd.DataFrame()
    return pd.DataFrame(store)

# ---- Export helper: Excel if possible, else CSV fallback ---------------------
def make_download_payload(df: pd.DataFrame):
    """
    Returns (bytes, filename, mime). Prefers Excel via openpyxl; falls back to CSV if openpyxl is missing.
    """
    try:
        import openpyxl  # noqa: F401
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Responses")
        return output.getvalue(), "uKids_availability_responses.xlsx", (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception:
        csv_bytes = df.to_csv(index=False).encode("utf-8")
        return csv_bytes, "uKids_availability_responses.csv", "text/csv"

# -----------------------------------------------------------------------------
# REPORT HELPERS (use Report Label column if present)
# -----------------------------------------------------------------------------
REPORT_LABEL_CANDIDATES = ["Report Label", "ReportLabel", "Label"]

def extract_date_from_label(label: str) -> str:
    """
    Fallback: 'Are you available the 5th of October?' -> '5 October'
    """
    m = re.search(r'(\d{1,2})(?:st|nd|rd|th)?\s+of\s+(October|Nov|November|Oct)', label, flags=re.I)
    if m:
        return f"{m.group(1)} October"
    m2 = re.search(r'(\d{1,2})\s+(October)', label, flags=re.I)
    if m2:
        return f"{m2.group(1)} {m2.group(2).title()}"
    return label.strip()

def get_report_label(row) -> str:
    """Prefer a custom label from the CSV; fall back to extracting from QuestionText."""
    for col in REPORT_LABEL_CANDIDATES:
        if col in row and str(row[col]).strip():
            return str(row[col]).strip()
    return extract_date_from_label(str(row.get("QuestionText", "")).strip())

def build_human_report(form_questions: pd.DataFrame, answers: dict) -> str:
    director = answers.get("Q1") or "—"
    name = answers.get("Q2") or "—"
    lines = [f"Director: {director}", f"Serving Girl: {name}", "Availability:"]
    rows = form_questions[form_questions["Options Source"].astype(str).str.lower() == "yes_no"]
    for _, r in rows.iterrows():
        qid = str(r["QuestionID"])
        label = get_report_label(r)
        val = (answers.get(qid) or "No").title()
        lines.append(f"{label}: {val}")
    reason = (answers.get("Q7") or "").strip()
    if reason:
        lines.append(f"Reason: {reason}")
    return "\n".join(lines)

def inject_screenshot_css():
    st.markdown(
        """
        <style>
        #MainMenu, header, footer {visibility: hidden;}
        .report-card {
            max-width: 760px; margin: 0 auto; padding: 20px 22px;
            border: 1px solid #e6e6e6; border-radius: 14px; background: white;
            box-shadow: 0 4px 18px rgba(0,0,0,0.06);
            font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
        }
        .report-title { font-size: 20px; font-weight: 700; margin: 0 0 6px; }
        .report-sub { color: #666; margin-bottom: 14px; font-size: 13px; }
        .report-row { display: flex; gap: 12px; margin: 6px 0; }
        .report-label { width: 160px; color: #444; font-weight: 600; }
        .report-value { flex: 1; color: #111; }
        .avail-item { display:flex; justify-content: space-between; padding: 8px 12px;
                      border: 1px solid #eee; border-radius: 10px; margin: 6px 0; }
        .ok { color: #0a7b36; font-weight: 700; }
        .no { color: #b00020; font-weight: 700; }
        .muted { color: #666; }
        </style>
        """,
        unsafe_allow_html=True,
    )

def availability_pairs(form_questions: pd.DataFrame, answers: dict):
    rows = form_questions[form_questions["Options Source"].astype(str).str.lower() == "yes_no"]
    pairs = []
    for _, r in rows.iterrows():
        qid = str(r["QuestionID"])
        label = get_report_label(r)
        pairs.append((label, (answers.get(qid) or "No").title()))
    return pairs

def render_report_card(name: str, director: str, pairs, reason: str, timestamp_iso: str):
    inject_screenshot_css()
    avail_html = []
    for date, val in pairs:
        badge = '<span class="ok">✅ Yes</span>' if val.lower() == "yes" else '<span class="no">❌ No</span>'
        avail_html.append(f'<div class="avail-item"><span>{date}</span><span>{badge}</span></div>')
    ts = timestamp_iso or datetime.utcnow().isoformat() + "Z"
    reason_html = f'<div class="report-row"><div class="report-label">Reason</div><div class="report-value">{reason}</div></div>' if (reason and reason.strip()) else ""
    st.markdown(
        f"""
        <div class="report-card">
            <div class="report-title">Availability Confirmation</div>
            <div class="report-sub">Generated: {ts}</div>

            <div class="report-row">
                <div class="report-label">Name</div>
                <div class="report-value">{name or '—'}</div>
            </div>
            <div class="report-row">
                <div class="report-label">Director</div>
                <div class="report-value">{director or '—'}</div>
            </div>

            <div class="report-row" style="margin-top:10px;">
                <div class="report-label">Availability</div>
                <div class="report-value">
                    {''.join(avail_html)}
                </div>
            </div>

            {reason_html}
            <div class="report-sub muted" style="margin-top:12px;">(Screenshot this card.)</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

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
            st.write("Directory listing of ./data:", [p.name for p in (Path(__file__).parent / "data").iterdir()])
        except Exception:
            st.write("Could not list ./data")
    st.stop()

# Optional heads-up if no Report Label column is found
if not any(col in form_questions.columns for col in REPORT_LABEL_CANDIDATES):
    st.warning("No 'Report Label' column found in Form questions.csv. Falling back to auto-detected labels.")

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

st.subheader("Availability in October")
availability_questions = form_questions[
    form_questions["Options Source"].astype(str).str.lower() == "yes_no"
].copy()

for _, q in availability_questions.iterrows():
    qid = str(q["QuestionID"])
    qtext = str(q["QuestionText"])
    current = answers.get(qid)
    idx = 0 if current == "Yes" else 1 if current == "No" else 1
    choice = st.radio(qtext, ["Yes", "No"], index=idx, key=qid, horizontal=True)
    answers[qid] = choice

# Conditional Q7 (reason shown if fewer than 2 Yes across its DependsOn)
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

# Review + Submit
st.subheader("Review")
yes_ids = form_questions[form_questions["Options Source"].str.lower() == "yes_no"]["QuestionID"].astype(str).tolist()
c1, c2, c3 = st.columns(3)
with c1: st.metric("Director", answers.get("Q1") or "—")
with c2: st.metric("Name", answers.get("Q2") or "—")
with c3: st.metric("Yes count", yes_count(answers, yes_ids))

errors = {}
if st.button("Submit"):
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
        payload = {
            "director": answers.get("Q1") or None,
            "servingGirl": answers.get("Q2") or None,
            "availability": {qid: answers.get(qid) for qid in yes_ids},
            "reason": answers.get("Q7") or None,
        }
        add_submission(payload)
        st.success("Form submitted! Thank you.")

        # --- Plain-English report (driven by 'Report Label' column) ---
        report_text = build_human_report(form_questions, answers)
        st.markdown("### 📄 Screenshot-friendly report (text)")
        st.code(report_text, language=None)
        st.download_button(
            "Download report as .txt",
            data=report_text.encode("utf-8"),
            file_name=f"Availability_{(answers.get('Q2') or 'name').replace(' ', '_')}.txt",
            mime="text/plain",
        )

        # --- Optional: visual report card ---
        st.markdown("### 🪪 Report card")
        pairs = availability_pairs(form_questions, answers)
        render_report_card(
            name=answers.get("Q2"),
            director=answers.get("Q1"),
            pairs=pairs,
            reason=answers.get("Q7"),
            timestamp_iso=datetime.utcnow().isoformat() + "Z",
        )

# -----------------------------------------------------------------------------
# ADMIN PANEL (excel export for you only, with fallback)
# -----------------------------------------------------------------------------
with st.expander("Admin"):
    has_secret = bool(ADMIN_KEY)
    st.caption(f"Secrets loaded: {'yes' if has_secret else 'no'}")
    if not has_secret:
        st.info(
            "Admin key not set. Add in Settings → Secrets as either:\n\n"
            "ADMIN_KEY = \"your-secret\"\n\nor\n\n[general]\nADMIN_KEY = \"your-secret\""
        )
    else:
        key = st.text_input("Enter admin key to access exports", type="password")
        if key == ADMIN_KEY:
            st.success("Admin unlocked.")
            df = submissions_dataframe()
            st.write(f"Total submissions: **{len(df)}**")
            if len(df) > 0:
                # Flatten availability dict to columns
                flat_rows = []
                for row in df.to_dict(orient="records"):
                    base = {
                        "timestamp": row.get("_timestamp"),
                        "director": row.get("director"),
                        "servingGirl": row.get("servingGirl"),
                        "reason": row.get("reason"),
                    }
                    avail = row.get("availability") or {}
                    for k, v in avail.items():
                        base[f"avail_{k}"] = v
                    flat_rows.append(base)
                flat_df = pd.DataFrame(flat_rows)
                st.dataframe(flat_df, use_container_width=True)

                bytes_data, fname, mime = make_download_payload(flat_df)
                st.download_button("Download all responses", data=bytes_data, file_name=fname, mime=mime)
            else:
                st.warning("No submissions yet.")
        elif key:
            st.error("Incorrect admin key.")
