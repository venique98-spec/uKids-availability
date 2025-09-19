import json
from io import BytesIO
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Availability Form", page_icon="🗓️", layout="centered")
st.title("🗓️ Availability Form")

# -----------------------------------------------------------------------------
# CONFIG / PATHS
# -----------------------------------------------------------------------------
# If your CSVs are top-level, use these:
FQ_PATH = Path(__file__).parent / "Form questions.csv"
SB_PATH = Path(__file__).parent / "Serving base with allocated directors.csv"

# Secret for admin export. Set this in Streamlit Cloud > Settings > Secrets:
# [general]
# ADMIN_KEY = "your-super-secret"
ADMIN_KEY = st.secrets.get("ADMIN_KEY", "")

# -----------------------------------------------------------------------------
# LOADERS
# -----------------------------------------------------------------------------
def read_csv_local(path: Path) -> pd.DataFrame:
    """
    Robustly read CSVs exported from Excel/Google Sheets (handles BOM, ; delimiters, cp1252/utf8/latin1).
    """
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

def yes_count(answers: dict, ids: list[str]) -> int:
    return sum(1 for qid in ids if str(answers.get(qid, "")).lower() == "yes")

# -----------------------------------------------------------------------------
# PERSIST SUBMISSIONS (server-side cache)
# -----------------------------------------------------------------------------
@st.cache_resource
def submission_store():
    # list of dicts: one per submission
    return []

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

def df_to_excel_bytes(df: pd.DataFrame) -> bytes:
    output = BytesIO()
    # openpyxl is used under the hood by pandas for .to_excel
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Responses")
    return output.getvalue()

# -----------------------------------------------------------------------------
# LOAD DATA
# -----------------------------------------------------------------------------
try:
    form_questions, serving_base, serving_map = load_data()
except Exception as e:
    st.error(f"Data load error: {e}")
    st.stop()

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

# Conditional Q7 (reason)
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
yes_ids = availability_questions["QuestionID"].astype(str).tolist()
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
    # If Q7 visible (yes_count < 2), require a reason
    if not q7_row.empty and yes_count(answers, dep_ids) < 2:
        if not answers.get("Q7") or len(answers["Q7"].strip()) < 5:
            errors["Q7"] = "Please provide a brief reason (at least 5 characters)."

    if errors
