import pandas as pd
import streamlit as st
from pathlib import Path
import json

st.set_page_config(page_title="Availability Form", page_icon="🗓️", layout="centered")
st.title("🗓️ Availability Form")

DATA_DIR = Path(__file__).parent / "data"
FQ_PATH = DATA_DIR / "Form questions.csv"
SB_PATH = DATA_DIR / "Serving base with allocated directors.csv"

def read_csv_local(path: Path) -> pd.DataFrame:
    # Try common encodings used by Excel exports
    for enc in ("cp1252", "utf-8", "latin1"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception:
            continue
    raise ValueError(f"Could not parse: {path.name}")

# Load once
@st.cache_data
def load_data():
    fq = read_csv_local(FQ_PATH)
    sb = read_csv_local(SB_PATH)

    # Basic sanity checks
    required_fq = {"QuestionID", "QuestionText", "QuestionType", "Options Source", "DependsOn", "Show Condition"}
    missing_fq = required_fq - set(fq.columns)
    if missing_fq:
        raise RuntimeError(f"`Form questions.csv` missing columns: {', '.join(sorted(missing_fq))}")

    required_sb = {"Director", "Serving Girl"}
    missing_sb = required_sb - set(sb.columns)
    if missing_sb:
        raise RuntimeError(f"`Serving base with allocated directors.csv` missing columns: {', '.join(sorted(missing_sb))}")

    # Build serving map
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

try:
    form_questions, serving_base, serving_map = load_data()
except Exception as e:
    st.error(f"Data load error: {e}")
    st.stop()

def yes_count(answers: dict, ids: list[str]) -> int:
    return sum(1 for qid in ids if str(answers.get(qid, "")).lower() == "yes")

if "answers" not in st.session_state:
    st.session_state.answers = {}
answers = st.session_state.answers

# ---------- FORM UI ----------
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
    current = answers.get(qid, None)
    choice = st.radio(qtext, ["Yes", "No"], index=(0 if current == "Yes" else 1 if current == "No" else 1), key=qid)
    answers[qid] = choice

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
        st.success("Form submitted!")
        st.json(payload)
        st.download_button(
            label="Download submission as JSON",
            data=json.dumps(payload, indent=2),
            file_name="submission.json",
            mime="application/json",
        )

with st.expander("Preview parsed CSVs"):
    st.write("**Form questions.csv (first rows)**")
    st.dataframe(form_questions.head(10), use_container_width=True)
    st.write("**Serving base with allocated directors.csv (first rows)**")
    st.dataframe(serving_base.head(10), use_container_width=True)
