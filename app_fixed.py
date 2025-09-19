
import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import datetime

st.set_page_config(page_title="Venique uKidz Rooster App - Dynamic Form", page_icon="📋", layout="centered")

st.title("📋 Venique uKidz Rooster App — Dynamic Form")
st.caption("Replace Google Forms with CSV-driven, conditional forms.")

_READ_HELPER_DEFINED = True

def _read_table_any(f):
    \"\"\"Robustly read a CSV/TSV with unknown encoding/delimiter; fallback to Excel.\"\"\"
    import io
    import pandas as pd
    from pandas.errors import ParserError
    # If this is a Streamlit UploadedFile, get raw bytes
    raw = f.getvalue() if hasattr(f, "getvalue") else None
    candidates = []
    if raw is not None:
        buf = io.BytesIO(raw)
        candidates.append(("utf-8", None))
        candidates.append(("utf-8-sig", None))
        candidates.append(("cp1252", None))
        candidates.append(("iso-8859-1", None))
        # Try CSV with autodetected delimiter
        for enc, sep in candidates:
            buf.seek(0)
            try:
                return pd.read_csv(buf, encoding=enc, sep=None, engine="python")
            except UnicodeDecodeError:
                continue
            except ParserError:
                continue
        # Try semicolon-delimited variants
        for enc in ["utf-8", "utf-8-sig", "cp1252", "iso-8859-1"]:
            buf.seek(0)
            try:
                return pd.read_csv(buf, encoding=enc, sep=";", engine="python")
            except Exception:
                continue
        # Fallback: Excel
        buf.seek(0)
        try:
            return pd.read_excel(buf)
        except Exception:
            pass
        raise ValueError("Unable to parse the uploaded file. Please ensure it's CSV or Excel.")
    else:
        # Path-like on disk
        p = Path(f)
        # Try CSV first
        for enc in ["utf-8", "utf-8-sig", "cp1252", "iso-8859-1"]:
            try:
                return pd.read_csv(p, encoding=enc, sep=None, engine="python")
            except UnicodeDecodeError:
                continue
            except ParserError:
                continue
        for enc in ["utf-8", "utf-8-sig", "cp1252", "iso-8859-1"]:
            try:
                return pd.read_csv(p, encoding=enc, sep=";", engine="python")
            except Exception:
                continue
        try:
            return pd.read_excel(p)
        except Exception:
            raise ValueError(f"Unable to parse file at {p}.")
    
YES_NO = ["Yes", "No"]

# ---------- Helpers ----------
def read_csv_upload(label, help_text=None, default_paths=None):
    \"\"\"Upload or load CSV. If not uploaded, try a list of default_paths in order.\"\"\"
    up = st.file_uploader(label, type=[\"csv\", \"xlsx\", \"xls\"], help=help_text, key=f\"uploader_{label}\")
    if up is not None:
        try:
            return _read_table_any(up)
        except Exception as e:
            st.error(f\"Failed to parse uploaded file: {e}\")
            st.stop()
    if default_paths:
        for p in default_paths:
            p = Path(p)
            if p.exists():
                try:
                    return _read_table_any(p)
                except Exception as e:
                    st.error(f\"Failed to parse default file '{p}': {e}\")
                    st.stop()
    return None

def normalize_depends(x: str):
    if pd.isna(x) or x in (None, "None", "", "nan"):
        return []
    return [item.strip() for item in str(x).split(",") if item.strip()]

def sort_by_qid(df: pd.DataFrame):
    def qnum(qid: str):
        try:
            return int(str(qid).lstrip("Q"))
        except:
            return 9999
    return df.sort_values(by="QuestionID", key=lambda s: s.map(qnum))

def compute_yes_count(answers: dict, form_df: pd.DataFrame):
    yes_count = 0
    for _, row in form_df.iterrows():
        if row["QuestionType"] == "radio" and str(row.get("OptionsSource", "")).lower() == "yes_no":
            val = answers.get(row["QuestionID"])
            if val == "Yes":
                yes_count += 1
    return yes_count

def serving_girls_available(data_df: pd.DataFrame, responses_df: pd.DataFrame, director: str):
    options = data_df.loc[data_df["Director"] == director, "ServingGirl"].dropna().astype(str).unique().tolist()
    if responses_df is not None and not responses_df.empty:
        used = responses_df.loc[responses_df["Director"] == director, "ServingGirl"].dropna().astype(str).unique().tolist()
        options = [o for o in options if o not in set(used)]
    return options

def ensure_response_columns(responses_df: pd.DataFrame | None, form_df: pd.DataFrame):
    needed_cols = ["Director", "ServingGirl"]
    needed_cols += [qid for qid in form_df["QuestionID"].tolist() if qid not in ("Q1", "Q2")]
    if responses_df is None or responses_df.empty:
        return pd.DataFrame(columns=needed_cols)
    for c in needed_cols:
        if c not in responses_df.columns:
            responses_df[c] = None
    return responses_df[needed_cols]

def make_summary_table(answers: dict, form_df: pd.DataFrame):
    """Return a tidy dataframe with two columns: Field, Answer. Includes Director and ServingGirl first."""
    rows = []
    rows.append({"Field": "Director", "Answer": answers.get("Q1")})
    rows.append({"Field": "Serving Girl", "Answer": answers.get("Q2")})
    for _, r in form_df.iterrows():
        qid = r["QuestionID"]
        if qid in ("Q1", "Q2"):
            continue
        label = r["QuestionText"]
        val = answers.get(qid, None)
        rows.append({"Field": label, "Answer": val})
    return pd.DataFrame(rows)

def summary_as_text(answers: dict, form_df: pd.DataFrame):
    """Create a human-readable text receipt from the summary table."""
    df = make_summary_table(answers, form_df)
    lines = []
    lines.append("Venique uKidz — Submission Summary")
    lines.append(f"Timestamp: {datetime.now().isoformat(timespec='seconds')}")
    lines.append("-" * 40)
    for _, row in df.iterrows():
        lines.append(f"{row['Field']}: {row['Answer'] if pd.notna(row['Answer']) else ''}")
    return "\n".join(lines)

# ---------- Sidebar: CSV inputs ----------
st.sidebar.header("CSV Inputs")
st.sidebar.write("Upload your real CSVs here (or use filenames next to app.py).")

# Support your uploaded filenames as defaults
form_defaults = ["form_structure.csv", "Form questions.csv"]
data_defaults = ["director_serving.csv", "Serving base with allocated directors.csv"]
responses_defaults = ["responses.csv"]

form_df = read_csv_upload("Form Structure CSV", "Defines questions, types, dependencies, conditions.", form_defaults)
data_df = read_csv_upload("Data CSV (director_serving.csv)", "Mapping of Director → ServingGirl", data_defaults)
responses_df = read_csv_upload("Responses CSV (responses.csv)", "Existing submissions. Optional; will be created if missing.", responses_defaults)

if form_df is None or data_df is None:
    st.error("Please provide both **Form Structure CSV** and **Data CSV** (upload or place files beside app.py)." )
    st.stop()

form_df = form_df.fillna({"DependsOn": "None", "ShowCondition": "None", "OptionsSource": "None"})
form_df = sort_by_qid(form_df)

responses_df = ensure_response_columns(responses_df, form_df)

with st.expander("Preview: Form Structure"):
    st.dataframe(form_df, use_container_width=True, hide_index=True)
with st.expander("Preview: Data CSV (Director → ServingGirl)"):
    st.dataframe(data_df, use_container_width=True, hide_index=True)
with st.expander("Preview: Current Responses"):
    st.dataframe(responses_df, use_container_width=True, hide_index=True)

st.markdown("---")


# ---------- Render dynamic form ----------
st.subheader("Fill in the form")
answers = {}

# Q1 Director
q1_row = form_df.loc[form_df["QuestionID"] == "Q1"]
if not q1_row.empty:
    q1_text = q1_row.iloc[0]["QuestionText"]
    directors = sorted(data_df["Director"].dropna().astype(str).unique().tolist())
    answers["Q1"] = st.selectbox(q1_text, directors, index=None, placeholder="Select a director…", key="Q1")
else:
    st.error("Form must include Q1 for Director selection.")
    st.stop()

# Q2 Serving Girl
q2_row = form_df.loc[form_df["QuestionID"] == "Q2"]
if not q2_row.empty:
    q2_text = q2_row.iloc[0]["QuestionText"]
    if answers.get("Q1"):
        sg_options = serving_girls_available(data_df, responses_df, director=answers["Q1"])
        answers["Q2"] = st.selectbox(q2_text, sg_options, index=None, placeholder="Select your name…", key="Q2")
    else:
        st.info("Please select a Director first to choose your name.")
        answers["Q2"] = None
else:
    st.error("Form must include Q2 for Serving Girl selection.")
    st.stop()

# Remaining questions
for _, row in form_df.iterrows():
    qid = row["QuestionID"]
    if qid in ("Q1", "Q2"):
        continue

    qtext = row["QuestionText"]
    qtype = str(row["QuestionType"]).lower().strip()
    optsrc = str(row.get("OptionsSource", "None")).lower().strip()
    depends = normalize_depends(row.get("DependsOn", "None"))
    showcond = row.get("ShowCondition", "None")

    # dependency gate
    dep_ok = True
    for d in depends:
        if d and not answers.get(d):
            dep_ok = False
            break
    if not dep_ok:
        continue

    # show condition gate (supports yes_count<2)
    if isinstance(showcond, str) and showcond.strip() not in ("", "None", "nan"):
        yes_count = compute_yes_count(answers, form_df)
        cond = showcond.replace(" ", "")
        if cond == "yes_count<2" and not (yes_count < 2):
            continue

    # Render
    if qtype == "radio":
        options = YES_NO if optsrc == "yes_no" else []
        answers[qid] = st.radio(qtext, options, horizontal=True, key=qid, index=None)
    elif qtype == "text":
        answers[qid] = st.text_area(qtext, key=qid, placeholder="Type here…")
    elif qtype == "dropdown":
        answers[qid] = st.selectbox(qtext, [], index=None, placeholder="No options", key=qid)
    else:
        st.warning(f"Unsupported QuestionType: {row['QuestionType']} for {qid}")
        answers[qid] = None

yes_count_final = compute_yes_count(answers, form_df)
st.caption(f"✅ Availability 'Yes' count so far: **{yes_count_final}**")

# ---------- Submit ----------
st.markdown("---")
submit = st.button("Submit", type="primary", disabled=not (answers.get('Q1') and answers.get('Q2')))

if submit:
    director = answers.get("Q1")
    serving = answers.get("Q2")
    still_available = serving in serving_girls_available(data_df, responses_df, director=director)

    if not still_available:
        st.error(f"'{serving}' is no longer available under Director '{director}'. Please pick another name.")
        st.stop()

    # Append to responses
    row_out = {"Director": director, "ServingGirl": serving}
    for qid in form_df["QuestionID"].tolist():
        if qid in ("Q1", "Q2"):
            continue
        row_out[qid] = answers.get(qid, None)

    responses_df = ensure_response_columns(responses_df, form_df)
    responses_df = pd.concat([responses_df, pd.DataFrame([row_out])], ignore_index=True)

    st.success("Submission recorded.")
    st.dataframe(responses_df.tail(10), use_container_width=True, hide_index=True)

    # -------- Submission Summary Report --------
    st.subheader("🧾 Submission Summary")
    summary_df = make_summary_table(answers, form_df)
    st.dataframe(summary_df, use_container_width=True, hide_index=True)

    txt_receipt = summary_as_text(answers, form_df)
    st.download_button(
        "⬇️ Download your submission summary (txt)",
        data=txt_receipt.encode("utf-8"),
        file_name=f"submission_summary_{serving}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
        mime="text/plain",
    )

    # Build/update submission_reports.csv (aggregated log)
    summary_row = {"Timestamp": datetime.now().isoformat(timespec="seconds"),
                   "Director": director,
                   "ServingGirl": serving}
    for _, r in form_df.iterrows():
        qid = r["QuestionID"]
        if qid in ("Q1", "Q2"):
            continue
        summary_row[r["QuestionText"]] = answers.get(qid, None)

    summary_csv_name = "submission_reports.csv"
    try:
        existing = pd.read_csv(summary_csv_name)
    except Exception:
        existing = pd.DataFrame()
    new_summary_df = pd.concat([existing, pd.DataFrame([summary_row])], ignore_index=True)

    with st.expander("Report log preview (submission_reports.csv)"):
        st.dataframe(new_summary_df.tail(25), use_container_width=True, hide_index=True)

    st.download_button(
        "⬇️ Download submission_reports.csv",
        data=new_summary_df.to_csv(index=False).encode("utf-8"),
        file_name="submission_reports.csv",
        mime="text/csv",
    )

    st.download_button(
        "⬇️ Download updated responses.csv",
        data=responses_df.to_csv(index=False).encode("utf-8"),
        file_name="responses.csv",
        mime="text/csv",
    )

st.markdown("---")
st.caption("Tip: Upload your CSVs in the sidebar, or place them next to app.py with names like 'Form questions.csv' and 'Serving base with allocated directors.csv'.")
