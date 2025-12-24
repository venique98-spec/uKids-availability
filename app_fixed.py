# app_fixed.py
import re
import time
import random
from io import BytesIO
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st

# Optional: Google Sheets libs. If missing, the app runs in Local CSV mode.
try:
    import gspread
    from gspread.exceptions import APIError, WorksheetNotFound
except Exception:  # still run in local mode if not installed
    gspread = None

    class APIError(Exception):
        ...

    class WorksheetNotFound(Exception):
        ...


# ──────────────────────────────────────────────────────────────────────────────
# UI CONFIG + mobile tweaks
# ──────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Availability Form", page_icon="🗓️", layout="centered")
st.title("🗓️ Availability Form")

st.markdown(
    """
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
""",
    unsafe_allow_html=True,
)

# ──────────────────────────────────────────────────────────────────────────────
# File paths
# ──────────────────────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
FQ_PATH = DATA_DIR / "Form questions.csv"
SB_PATH = DATA_DIR / "Serving base with allocated directors.csv"
LOCAL_RESP_PATH = DATA_DIR / "responses_local.csv"

# ──────────────────────────────────────────────────────────────────────────────
# Secrets helpers
# ──────────────────────────────────────────────────────────────────────────────
def _get_secret_any(*paths):
    """Try multiple secret paths, return the first value found."""
    try:
        cur = st.secrets
    except Exception:
        return None
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


def is_sheets_enabled() -> bool:
    if gspread is None:
        return False
    sa = _get_secret_any(["gcp_service_account"], ["general", "gcp_service_account"])
    sid = _get_secret_any(
        ["GSHEET_ID"], ["general", "GSHEET_ID"], ["gcp_service_account", "GSHEET_ID"]
    )
    return bool(sa and sid)


SHEETS_MODE = is_sheets_enabled()

# ──────────────────────────────────────────────────────────────────────────────
# CSV loading
# ──────────────────────────────────────────────────────────────────────────────
def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [
        str(c).replace("\u00A0", " ").replace("\u200B", "").strip() for c in df.columns
    ]
    return df


def _read_csv_any(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
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
    fq = _normalize_columns(_read_csv_any(FQ_PATH))
    sb = _normalize_columns(_read_csv_any(SB_PATH))

    needed_fq = {
        "QuestionID",
        "QuestionText",
        "QuestionType",
        "Options Source",
        "DependsOn",
        "Show Condition",
    }
    miss_fq = needed_fq - set(fq.columns)
    if miss_fq:
        raise RuntimeError(
            f"`Form questions.csv` missing columns: {', '.join(sorted(miss_fq))}"
        )

    needed_sb = {"Director", "Serving Girl"}
    miss_sb = needed_sb - set(sb.columns)
    if miss_sb:
        raise RuntimeError(
            f"`Serving base with allocated directors.csv` missing columns: {', '.join(sorted(miss_sb))}"
        )

    fq = fq.assign(
        QuestionID=fq["QuestionID"].astype(str).str.strip(),
        QuestionText=fq["QuestionText"].astype(str).str.strip(),
        QuestionType=fq["QuestionType"].astype(str).str.strip(),
        **{
            "Options Source": fq["Options Source"].astype(str).str.strip(),
            "DependsOn": fq["DependsOn"].astype(str).str.strip(),
            "Show Condition": fq["Show Condition"].astype(str).str.strip(),
        },
    )
    sb = sb.assign(
        Director=sb["Director"].astype(str).str.strip(),
        **{"Serving Girl": sb["Serving Girl"].astype(str).str.strip()},
    )

    serving_map = (
        sb.groupby("Director")["Serving Girl"].apply(lambda s: sorted({x for x in s if x})).to_dict()
    )
    return fq, sb, serving_map


# ──────────────────────────────────────────────────────────────────────────────
# Labels + report helpers
# ──────────────────────────────────────────────────────────────────────────────
def _norm(s: str) -> str:
    return str(s).replace("\u00A0", " ").replace("\u200B", "").strip().lower()


def pick_report_label_col(df: pd.DataFrame):
    candidates = ["report label", "reportlabel", "label"]
    cmap = {_norm(c): c for c in df.columns}
    for cand in candidates:
        if cand in cmap:
            return cmap[cand]
    return None


def extract_date_from_label(label: str) -> str:
    m = re.search(
        r"(\d{1,2})(?:st|nd|rd|th)?\s+of\s+(October|November|December|Sept|September|Oct|Nov|Dec)",
        label,
        flags=re.I,
    )
    if m:
        return f"{m.group(1)} {m.group(2).title().replace('Sept','September').replace('Oct','October').replace('Nov','November').replace('Dec','December')}"
    m2 = re.search(r"(\d{1,2})\s+(October|November|December)", label, flags=re.I)
    if m2:
        return f"{m2.group(1)} {m2.group(2).title()}"
    return label.strip()


def get_report_label(row, report_label_col: str | None) -> str:
    if report_label_col and report_label_col in row and str(row[report_label_col]).strip():
        return str(row[report_label_col]).strip()
    return extract_date_from_label(str(row.get("QuestionText", "")).strip())


def yesno_labels(form_questions: pd.DataFrame, report_label_col: str | None) -> list[str]:
    labels = []
    rows = form_questions[form_questions["Options Source"].astype(str).str.lower() == "yes_no"]
    for _, r in rows.iterrows():
        lbl = get_report_label(r, report_label_col)
        if lbl not in labels:
            labels.append(lbl)
    return labels


def yes_count(answers: dict, ids) -> int:
    return sum(1 for qid in ids if str(answers.get(qid, "")).lower() == "yes")


def build_human_report(form_questions: pd.DataFrame, answers: dict, report_label_col: str | None) -> str:
    director = answers.get("Q1") or "—"
    name = answers.get("Q2") or "—"
    lines = [f"Director: {director}", f"Serving Girl: {name}", "Availability:"]
    rows = form_questions[form_questions["Options Source"].astype(str).str.lower() == "yes_no"]
    for _, r in rows.iterrows():
        qid = str(r["QuestionID"])
        label = get_report_label(r, report_label_col)
        val = (answers.get(qid) or "No").title()
        lines.append(f"{label}: {val}")
    reason_row = form_questions[(form_questions["QuestionType"].str.lower() == "text")]
    if not reason_row.empty:
        rid = str(reason_row.iloc[0]["QuestionID"])
        reason = (answers.get(rid) or "").strip()
        if reason:
            lines.append(f"Reason: {reason}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Google Sheets helpers
# ──────────────────────────────────────────────────────────────────────────────
def gs_retry(func, *args, **kwargs):
    for attempt in range(5):
        try:
            return func(*args, **kwargs)
        except APIError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (429, 500, 502, 503):
                time.sleep(min(10, (2**attempt) + random.random()))
                continue
            raise


@st.cache_resource
def get_worksheet():
    """Open spreadsheet by key and return/create a stable 'Responses' worksheet."""
    if not SHEETS_MODE:
        raise RuntimeError("Sheets mode disabled (missing secrets or gspread not installed).")

    sa_dict = _get_secret_any(["gcp_service_account"], ["general", "gcp_service_account"])
    sheet_id = _get_secret_any(["GSHEET_ID"], ["general", "GSHEET_ID"], ["gcp_service_account", "GSHEET_ID"])

    gc = gspread.service_account_from_dict(sa_dict)
    sh = gs_retry(gc.open_by_key, sheet_id)

    try:
        ws = sh.worksheet("Responses")
    except WorksheetNotFound:
        ws = sh.add_worksheet(title="Responses", rows=1, cols=50)
    return ws


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


# ──────────────────────────────────────────────────────────────────────────────
# Local CSV fallback
# ──────────────────────────────────────────────────────────────────────────────
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


# ──────────────────────────────────────────────────────────────────────────────
# Non-responders
# ──────────────────────────────────────────────────────────────────────────────
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
    resp = resp.sort_values("Last submission").drop_duplicates(
        subset=["Director", "Serving Girl"], keep="last"
    )

    merged = sb.merge(resp, on=["Director", "Serving Girl"], how="left")
    merged["Responded"] = merged["Last submission"].notna() & (merged["Last submission"] != "")
    nonresp = merged[~merged["Responded"]].copy()
    return nonresp.sort_values(["Director", "Serving Girl"]).reset_index(drop=True)


# ──────────────────────────────────────────────────────────────────────────────
# Load input data
# ──────────────────────────────────────────────────────────────────────────────
try:
    form_questions, serving_base, serving_map = load_data()
except Exception as e:
    st.error(f"Data load error: {e}")
    with st.expander("Debug info"):
        st.code(str(FQ_PATH))
        st.code(str(SB_PATH))
        try:
            st.write("Directory listing of ./data:", [p.name for p in DATA_DIR.iterdir()])
        except Exception:
            st.write("Could not list ./data")
    st.stop()

REPORT_LABEL_COL = pick_report_label_col(form_questions)
if not REPORT_LABEL_COL:
    st.warning("No 'Report Label' column found (exact match not detected). Using auto-detected labels.")

# ──────────────────────────────────────────────────────────────────────────────
# UI state
# ──────────────────────────────────────────────────────────────────────────────
if "answers" not in st.session_state:
    st.session_state.answers = {}
answers = st.session_state.answers

# ──────────────────────────────────────────────────────────────────────────────
# Form UI
# ──────────────────────────────────────────────────────────────────────────────
st.subheader("Your details")

directors = sorted([d for d in serving_map.keys() if d])
answers["Q1"] = st.selectbox("Please select your director’s name", options=[""] + directors, index=0)

if answers.get("Q1"):
    girls = serving_map.get(answers["Q1"], [])
    answers["Q2"] = st.selectbox("Please select your name", options=[""] + girls, index=0)
else:
    answers["Q2"] = ""

st.subheader("Availability")

# All yes/no radios (e.g., Q3–Q7)
availability_questions = form_questions[form_questions["Options Source"].astype(str).str.lower() == "yes_no"].copy()
radio_options = ["Yes", "No"]
for _, q in availability_questions.iterrows():
    qid = str(q["QuestionID"])
    qtext = str(q["QuestionText"])

    saved = answers.get(qid)
    idx = radio_options.index(saved) if saved in radio_options else None  # no default
    choice = st.radio(
        qtext,
        options=radio_options,
        index=idx,
        key=f"avail_{qid}",
        horizontal=False,
    )
    answers[qid] = choice

# Find the Reason (text) row dynamically (Q8 in your CSV)
reason_row_df = form_questions[form_questions["QuestionType"].astype(str).str.lower() == "text"]
reason_qid = None
reason_dep_ids = []

if not reason_row_df.empty:
    rr = reason_row_df.iloc[0]
    reason_qid = str(rr["QuestionID"])
    # Parse DependsOn, e.g., Q3,Q4,Q5,Q6,Q7
    if pd.notna(rr["DependsOn"]) and str(rr["DependsOn"]).strip().lower() != "none":
        reason_dep_ids = [s.strip() for s in str(rr["DependsOn"]).split(",") if s.strip()]

# *** UPDATED RULE: hide the reason box when there are 2 or more "Yes" answers ***
REASON_YES_THRESHOLD = 2  # <<— changed from 3 to 2

# Render reason box only if count of Yes among its DependsOn is below threshold
if reason_qid:
    show_reason = True
    if reason_dep_ids:
        show_reason = yes_count(answers, reason_dep_ids) < REASON_YES_THRESHOLD
    if show_reason:
        answers[reason_qid] = st.text_area(
            str(reason_row_df.iloc[0]["QuestionText"]),
            value=answers.get(reason_qid, ""),
        )
    else:
        answers[reason_qid] = answers.get(reason_qid, "")

# Review
st.subheader("Review")
yes_ids = form_questions[form_questions["Options Source"].str.lower() == "yes_no"]["QuestionID"].astype(str).tolist()
c1, c2, c3 = st.columns(3)
with c1:
    st.metric("Director", answers.get("Q1") or "—")
with c2:
    st.metric("Name", answers.get("Q2") or "—")
with c3:
    st.metric("Yes count", yes_count(answers, yes_ids))

# ──────────────────────────────────────────────────────────────────────────────
# Submit (sticky)
# ──────────────────────────────────────────────────────────────────────────────
errors = {}
st.markdown('<div class="sticky-submit">', unsafe_allow_html=True)
submitted = st.button("Submit")
st.markdown("</div>", unsafe_allow_html=True)

if submitted:
    if not answers.get("Q1"):
        errors["Q1"] = "Please select a director."
    if not answers.get("Q2"):
        errors["Q2"] = "Please select your name."
    # If the reason box is being shown (i.e., < threshold), enforce a short reason
    if reason_qid and reason_dep_ids and (yes_count(answers, reason_dep_ids) < REASON_YES_THRESHOLD):
        if not answers.get(reason_qid) or len(answers[reason_qid].strip()) < 5:
            errors[reason_qid] = "Please provide a brief reason (at least 5 characters)."

    if errors:
        for msg in errors.values():
            st.error(msg)
    else:
        now = datetime.utcnow().isoformat() + "Z"
        labels = yesno_labels(form_questions, REPORT_LABEL_COL)
        row_map = {
            "timestamp": now,
            "Director": answers.get("Q1") or "",
            "Serving Girl": answers.get("Q2") or "",
            "Reason": (answers.get(reason_qid) or "").strip() if reason_qid else "",
        }
        for _, r in availability_questions.iterrows():
            qid = str(r["QuestionID"])
            label = get_report_label(r, REPORT_LABEL_COL)
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

        # Screenshot-friendly report
        report_text = build_human_report(form_questions, answers, REPORT_LABEL_COL)
        st.markdown("### 📄 Screenshot-friendly report (text)")
        st.code(report_text, language=None)
        st.download_button(
            "Download report as .txt",
            data=report_text.encode("utf-8"),
            file_name=f"Availability_{(answers.get('Q2') or 'name').replace(' ', '_')}.txt",
            mime="text/plain",
        )

# ──────────────────────────────────────────────────────────────────────────────
# Admin: exports + non-responders + diagnostics
# ──────────────────────────────────────────────────────────────────────────────
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
        try:
            responses_df = fetch_responses_df_sheets() if SHEETS_MODE else fetch_responses_df_local()
        except Exception as e:
            st.error(f"Could not load responses: {e}")
            responses_df = pd.DataFrame()

        st.write(f"Total submissions: **{len(responses_df)}**")
        if not responses_df.empty:
            st.dataframe(responses_df, use_container_width=True)
            # Excel if possible, else CSV
            try:
                import openpyxl  # noqa
                out = BytesIO()
                with pd.ExcelWriter(out, engine="openpyxl") as xw:
                    responses_df.to_excel(xw, index=False, sheet_name="Responses")
                st.download_button(
                    "Download all responses",
                    data=out.getvalue(),
                    file_name="uKids_availability_responses.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            except Exception:
                st.download_button(
                    "Download all responses",
                    data=responses_df.to_csv(index=False).encode("utf-8"),
                    file_name="uKids_availability_responses.csv",
                    mime="text/csv",
                )
        else:
            st.warning("No submissions yet.")

        st.markdown("### ❌ Non-responders")
        nonresp_df = compute_nonresponders(serving_base, responses_df)
        all_directors = ["All"] + sorted(
            serving_base["Director"].dropna().astype(str).str.strip().unique().tolist()
        )
        sel_dir = st.selectbox("Filter by director", options=all_directors, index=0)
        view_df = nonresp_df if sel_dir == "All" else nonresp_df[nonresp_df["Director"] == sel_dir]
        total_expected = len(serving_base[["Director", "Serving Girl"]].dropna().drop_duplicates())
        st.write(
            f"Non-responders shown: **{len(view_df)}**  |  Total expected pairs: **{total_expected}**"
        )
        st.dataframe(view_df[["Director", "Serving Girl"]], use_container_width=True)

        st.divider()
        st.markdown("#### 🔍 Secrets / Sheets check")
        try:
            s = st.secrets
            gsa = s.get("gcp_service_account", {})
            gs_id = s.get("GSHEET_ID") or s.get("general", {}).get("GSHEET_ID") or gsa.get("GSHEET_ID")
            st.write(
                {
                    "has_gcp_service_account_block": bool(gsa),
                    "GSHEET_ID_present": bool(gs_id),
                    "client_email": gsa.get("client_email", "(missing)"),
                    "private_key_id_present": bool(gsa.get("private_key_id")),
                    "private_key_length": len(gsa.get("private_key", "")),
                    "gspread_installed": gspread is not None,
                }
            )
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
