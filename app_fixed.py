import json
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
            val = st.secrets["general"]["ADMIN_KEY"]
            if val:
                return str(val)
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


def yes_count(answers: dict, ids: list[str]) -> int:
    """Count how many of the given question IDs have the answer 'Yes'."""
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
# REPORT RENDERING (screenshot-friendly)
# -----------------------------------------------------------------------------
def inject_screenshot_css():
    st.markdown(
        """
        <style>
        /* Hide Streamlit default chrome in screenshot mode */
        #MainMenu {visibility: hidden;}
        header {visibility: hidden;}
        footer {visibility: hidden;}
        /* Clean card look */
        .report-card {
            max-width: 760px;
            margin: 0 auto;
            padding: 20px 22px;
            border: 1px solid #e6e6e6;
            border-radius: 14px;
            background: white;
            box-shadow: 0 4px 18px rgba(0,0,0,0.06);
            font-family: system-ui, -apple-system, Segoe UI, Roboto, 'Helvetica Neue', Arial, 'Noto Sans', 'Liberation Sans', sans-serif;
        }
        .report-title { font-size: 20px; font-weight: 700; margin: 0 0 6px; }
        .report-sub { color: #666; margin-bottom: 14px; font-size: 13px; }
        .report-row { display: flex; gap: 12px; margin: 6px 0; }
        .report-label { width: 160px; color: #444; font-weight: 600; }
        .report-value { flex: 1; color: #111; }
        .avail-item { display:flex; justify-content: space-between; padding: 8px 12px; border: 1px solid #eee; border-radius: 10px; margin: 6px 0; }
        .ok { color: #0a7b36; font-wei
