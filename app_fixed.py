# app_fixed.py
import re
from io import BytesIO
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st
import gspread

# -----------------------------------------------------------------------------
# APP CONFIG
# -----------------------------------------------------------------------------
st.set_page_config(page_title="Availability Form", page_icon="🗓️", layout="centered")
st.title("🗓️ Availability Form")

# CSVs expected in ./data
FQ_PATH = Path(__file__).parent / "data" / "Form questions.csv"
SB_PATH = Path(__file__).parent / "data" / "Serving base with allocated directors.csv"

# -----------------------------------------------------------------------------
# SECRETS HELPERS
# -----------------------------------------------------------------------------
def _get_secret_any(*paths):
    """Return secrets value by trying several key paths (top-level or sectioned)."""
    cur = st.secrets
    for path in paths:
        c = cur
        ok = True
        for k in path:
            if k in c:
                c = c[k]
            else:
                ok = False
                break
        if ok:
            return c
    return None

def get_admin_key() -> str:
    v = _get_secret_any(["ADMIN_KEY"], ["general", "ADMIN_KEY"])
    return str(v) if v else ""

ADMIN_KEY = get_admin_key()

# -----------------------------------------------------------------------------
# COLUMN NORMALIZATION (fix hidden spaces/casing from Excel)
# -----------------------------------------------------------------------------
def _norm_col(s: str) -> str:
    return (
        str(s).replace("\u00A0", " ").replace("\u200B", "").strip().lower()
    )

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).replace("\u00A0", " ").replace("\u200B", "").strip() for c in df.columns]
    return df

def pick_report_label_col(df: pd.DataFrame):
    candidates = ["report label", "reportlabel", "label"]
    cmap = {_norm_col(c): c for c in df.columns}
    for cand in candidates:
        if cand in cmap:
            return cmap[cand]  # actual column name present in df
    return None

REPORT_LABEL_COL = None  # set after load_data()

# -----------------------------------------------------------------------------
# DATA LOADERS
# -----------------------------------------------------------------------------
def read_csv_local(path: Path) -> pd.DataFrame:
    """Robust CSV reader for Excel/Sheets exports (handles BOM, ; delimiter, cp1252/utf8/latin1)."""
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")

    for enc in ("utf-8-sig", "cp1252", "latin1", "utf-8"):
        try:
            return pd.read_csv(path, encoding=enc, sep=None, engine="python")
        except Exception:
            pass

    # Sniff delimiter
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
# GOOGLE SHEETS CLIENT
# -----------------------------------------------------------------------------
@st.cache_resource
def get_worksheet():
    sa_dict = _get_secret_any(["gcp_service_account"], ["general", "gcp_service_account"])
    sheet_id = _get_secret_any(["GSHEET_ID"], ["general", "GSHEET_ID"])
    if not sa_dict or not sheet_id:
        raise RuntimeError(
            "Google Sheets secrets not set. Add [gcp_service_account] and GSHEET_ID in Streamlit Secrets."
        )
    gc = gspread.service_account_from_dict(sa_dict)
    sh = gc.open_by_key(sheet_id)
    return sh.sheet1  # first worksheet

def sheet_get_df(ws) -> pd.DataFrame:
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()
    header, rows = values[0], values[1:]
    return pd.DataFrame(rows, columns=header)

def ensure_headers(ws, desired_cols: list[str]) -> list[str]:
    """Make sure the sheet has at least the columns in desired_cols; return actual header."""
    values = ws.get_all_values()
    if not values:
        ws.append_row(desired_cols)
        return desired_cols
    header = ws.row_values(1)
    changed = False
    for col in desired_cols:
        if col not in header:
            header.append(col)
            changed = True
    if changed:
        ws.update("1:1", [header])
    return header

@st.cache_data(ttl=30, show_spinner=False)
def fetch_responses_df() -> pd.DataFrame:
    ws = get_worksheet()
    return sheet_get_df(ws)

def clear_responses_cache():
    try:
        fetch_responses_df.clear()
    except Exception:
        st.cache_data.clear()

# -----------------------------------------------------------------------------
# REPORT HELPERS
# -----------------------------------------------------------------------------
def extract_date_from_label(label: str) -> str:
    """Fallback: 'Are you available the 5th of October?' -> '5 October'"""
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
        val = (answers.get(qid) or "No").title()
        lines.append(f"{label}: {val}")
    reason = (answers.get("Q7") or "").strip()
    if reason:
        lines.append(f"Reason: {reason}")
    return "\n".join(lines)

# ---- Export helper: Excel if possible, else CSV fallback ---------------------
def make_download_payload(df: pd.DataFrame):
    """Return (bytes, filename, mime)."""
    try:
        import openpyxl  # noqa: F401
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
    """Return rows from serving_base who have not submitted yet (by Director + Serving Girl)."""
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

    # Our sheet columns are 'timestamp', 'Director', 'Serving Girl', 'Reason', plus date labels
    cols = [c for c in responses_df.columns]
    # Try to find timestamp-like column
    ts_col = "timestamp" if "timestamp" in cols else (cols[0] if cols else "timestamp")
    resp = responses_df[[c for c in cols if c in ["Director", "Serving Girl"] or c == ts_col]].copy()
    if ts_col not in resp.columns:
        resp[ts_col] = ""
    resp.rename(columns={ts_col: "Last submission"}, inplace=True)

    resp["Director"] = resp["Director"].astype(str).str.strip()
    resp["Serving Girl"] = resp["Serving Girl"].astype(str).str.strip()

    # Keep latest per person
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
            st.write("Directory listing of ./data:", [p.name for p in (Path(__file__).parent / "data").iterdir()])
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

st.subheader("Availability in October")
availability_questions = form_questions[form_questions["Options Source"].astype(str).str.lower() == "yes_no"].copy()

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
        # Build flat row for Google Sheets
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

        # Ensure headers & append to Google Sheets
        try:
            ws = get_worksheet()
            desired_header = ["timestamp", "Director", "Serving Girl", "Reason"] + labels
            header = ensure_headers(ws, desired_header)
            row = [row_map.get(col, "") for col in header]
            ws.append_row(row)
            clear_responses_cache()
            st.success("Submission saved to Google Sheets.")
        except Exception as e:
            st.error(f"Failed to save to Google Sheets: {e}")

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
# ADMIN (exports + non-responders from Google Sheets)
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

            # Load all responses from Google Sheets
            try:
                responses_df = fetch_responses_df()
            except Exception as e:
                st.error(f"Could not load responses from Google Sheets: {e}")
                responses_df = pd.DataFrame()

            st.write(f"Total submissions: **{len(responses_df)}**")

            if not responses_df.empty:
                st.dataframe(responses_df, use_container_width=True)

                # Export
                bytes_data, fname, mime = make_download_payload(responses_df)
                st.download_button("Download all responses", data=bytes_data, file_name=fname, mime=mime)
            else:
                st.warning("No submissions yet.")

            # Non-responders
            st.markdown("### ❌ Non-responders")
            nonresp_df = compute_nonresponders(serving_base, responses_df)

            # Director filter
            all_directors = ["All"] + sorted(serving_base["Director"].dropna().astype(str).str.strip().unique().tolist())
            sel_dir = st.selectbox("Filter by director", options=all_directors, index=0)
            view_df = nonresp_df if sel_dir == "All" else nonresp_df[nonresp_df["Director"] == sel_dir]

            total_expected = len(serving_base[["Director", "Serving Girl"]].dropna().drop_duplicates())
            st.write(f"Non-responders shown: **{len(view_df)}**  |  Total expected pairs: **{total_expected}**")

            st.dataframe(view_df[["Director", "Serving Girl"]], use_container_width=True)

            # Download non-responders
            nr_bytes, nr_name, nr_mime = make_download_payload(view_df[["Director", "Serving Girl"]])
            st.download_button("Download non-responders", data=nr_bytes, file_name="non_responders.xlsx", mime=nr_mime)

        elif key:
            st.error("Incorrect admin key.")
