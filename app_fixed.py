import io
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Availability Form", page_icon="🗓️", layout="centered")
st.title("🗓️ Availability Form (CSV-powered)")

# ---------- Helpers ----------
def read_csv_safely(uploaded_file) -> pd.DataFrame:
    """
    Try cp1252 first (common for Excel-exported CSVs), then utf-8.
    Works with Streamlit's UploadedFile object.
    """
    raw = uploaded_file.read()
    for enc in ("cp1252", "utf-8", "latin1"):
        try:
            return pd.read_csv(io.BytesIO(raw), encoding=enc)
        except Exception:
            continue
    # If all fail, raise a readable error
    raise ValueError("Could not parse the CSV. Try re-exporting as CSV (comma-separated).")

def yes_count(answers: dict, ids: list[str]) -> int:
    return sum(1 for qid in ids if str(answers.get(qid, "")).lower() == "yes")

# Keep answers across reruns
if "answers" not in st.session_state:
    st.session_state.answers = {}
answers = st.session_state.answers

# ---------- Upload section ----------
st.subheader("Upload your CSVs")
col1, col2 = st.columns(2)
with col1:
    fq_file = st.file_uploader("Form questions.csv", type=["csv"], key="fq")
with col2:
    sb_file = st.file_uploader("Serving base with allocated directors.csv", type=["csv"], key="sb")

if not fq_file or not sb_file:
    st.info("Please upload **both** CSVs to continue.")
    st.stop()

# Read both CSVs
try:
    form_questions = read_csv_safely(fq_file)
except Exception as e:
    st.error(f"Failed to read **Form questions.csv**: {e}")
    st.stop()

try:
    serving_base = read_csv_safely(sb_file)
except Exception as e:
    st.error(f"Failed to read **Serving base with allocated directors.csv**: {e}")
    st.stop()

# Basic sanity checks
required_fq_cols = {"QuestionID", "QuestionText", "QuestionType", "Options Source", "DependsOn", "Show Condition"}
missing_fq = required_fq_cols - set(form_questions.columns)
if missing_fq:
    st.error(f"`Form questions.csv` is missing columns: {', '.join(sorted(missing_fq))}")
    st.stop()

required_sb_cols = {"Director", "Serving Girl"}
missing_sb = required_sb_cols - set(serving_base.columns)
if missing_sb:
    st.error(f"`Serving base with allocated directors.csv` is missing columns: {', '.join(sorted(missing_sb))}")
    st.stop()

# ---------- Build mapping Director -> Serving Girls ----------
serving_map = (
    serving_base.assign(
        Director=lambda df: df["Director"].astype(str).str.strip(),
        **{"Serving Girl": serving_base["Serving Girl"].astype(str).str.strip()}
    )
    .groupby("Director")["Serving Girl"].apply(lambda s: sorted(set(x for x in s if x)))
    .to_dict()
)

directors = sorted([d for d in serving_map.keys() if d])

# ---------- Render form ----------
st.subheader("Your details")

# Q1: Director
answers["Q1"] = st.selectbox("Please select your director’s name", options=[""] + directors, index=0)

# Q2: Serving Girl (depends on Q1)
if answers.get("Q1"):
    girls = serving_map.get(answers["Q1"], [])
    answers["Q2"] = st.selectbox("Please select your name", options=[""] + girls, index=0)
else:
    answers["Q2"] = ""

# Availability (Q3–Q6): rows where Options Source == yes_no
availability_questions = form_questions[
    form_questions["Options Source"].astype(str).str.lower() == "yes_no"
].copy()

st.subheader("Availability in October")
for _, q in availability_questions.iterrows():
    qid = str(q["QuestionID"])
    qtext = str(q["QuestionText"])
    current = answers.get(qid, None)
    # Present as Yes/No; treat no selection as None (not counted in yes_count)
    choice = st.radio(qtext, ["Yes", "No"], index=(0 if current == "Yes" else 1 if current == "No" else 1), key=qid)
    answers[qid] = choice

# Q7: Reason (show only if yes_count < 2 across DependsOn for Q7)
q7_row = form_questions[form_questions["QuestionID"].astype(str) == "Q7"]
if not q7_row.empty:
    q7 = q7_row.iloc[0]
    q7_text = str(q7["QuestionText"])
    dep_ids = [s.strip() for s in str(q7["DependsOn"]).split(",") if s.strip()]
    if yes_count(answers, dep_ids) < 2:
        answers["Q7"] = st.text_area(q7_text, value=answers.get("Q7", ""))
    else:
        answers["Q7"] = ""
else:
    # If Q7 row is missing, still keep the key consistent
    answers["Q7"] = answers.get("Q7", "")

# ---------- Validation & submission ----------
st.subheader("Review")
yes_ids = availability_questions["QuestionID"].astype(str).tolist()
summary_cols = st.columns(3)
with summary_cols[0]:
    st.metric("Director", answers.get("Q1") or "—")
with summary_cols[1]:
    st.metric("Name", answers.get("Q2") or "—")
with summary_cols[2]:
    st.metric("Yes count", yes_count(answers, yes_ids))

errors = {}
if st.button("Submit"):
    if not answers.get("Q1"):
        errors["Q1"] = "Please select a director."
    if not answers.get("Q2"):
        errors["Q2"] = "Please select your name."
    # If Q7 visible (i.e., yes_count < 2), require at least 5 characters
    if not q7_row.empty and yes_count(answers, dep_ids) < 2:
        if not answers.get("Q7") or len(answers["Q7"].strip()) < 5:
            errors["Q7"] = "Please provide a brief reason (at least 5 characters)."

    if errors:
        for k, v in errors.items():
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
        # Offer a download
        import json
        st.download_button(
            label="Download submission as JSON",
            data=json.dumps(payload, indent=2),
            file_name="submission.json",
            mime="application/json",
        )

# ---------- Optional: show first rows to confirm data ----------
with st.expander("Preview parsed CSVs"):
    st.write("**Form questions.csv (first rows)**")
    st.dataframe(form_questions.head(10), use_container_width=True)
    st.write("**Serving base with allocated directors.csv (first rows)**")
    st.dataframe(serving_base.head(10), use_container_width=True)
