import pandas as pd
import streamlit as st

# Load CSVs
form_questions = pd.read_csv("Form questions.csv", encoding="cp1252")
serving_base = pd.read_csv("Serving base with allocated directors.csv", encoding="cp1252")

# Build mapping Director -> Serving Girls
serving_map = (
    serving_base.groupby("Director")["Serving Girl"]
    .apply(list)
    .to_dict()
)

# Helper: count "Yes" answers
def yes_count(answers, ids):
    return sum(1 for qid in ids if answers.get(qid, "").lower() == "yes")

# Initialize session state
if "answers" not in st.session_state:
    st.session_state["answers"] = {}

answers = st.session_state["answers"]

st.title("Availability Form")

# Q1: Director
directors = list(serving_map.keys())
answers["Q1"] = st.selectbox("Please select your director’s name", [""] + directors)

# Q2: Serving Girl (depends on Q1)
if answers.get("Q1"):
    girls = serving_map.get(answers["Q1"], [])
    answers["Q2"] = st.selectbox("Please select your name", [""] + girls)

# Q3–Q6: Availability
availability_questions = form_questions[
    form_questions["Options Source"].str.lower() == "yes_no"
]

for _, q in availability_questions.iterrows():
    qid = q["QuestionID"]
    qtext = q["QuestionText"]
    answers[qid] = st.radio(qtext, ["", "Yes", "No"], index=0, key=qid)

# Q7: Reason (show only if yes_count < 2)
q7 = form_questions[form_questions["QuestionID"] == "Q7"].iloc[0]
dep_ids = q7["DependsOn"].split(",")
if yes_count(answers, dep_ids) < 2:
    answers["Q7"] = st.text_area(q7["QuestionText"])

# Submit button
if st.button("Submit"):
    st.success("Form submitted!")
    st.json(answers)
