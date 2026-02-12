# app_fixed.py
import time
import random
from io import BytesIO
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st

# ✅ Timezone-aware deadlines
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None  # fallback

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

# Streamlined files
BASE_Q_PATH = DATA_DIR / "form_base_questions.csv"
DATES_PATH = DATA_DIR / "service_dates.csv"

# Existing data file
SB_PATH = DATA_DIR / "Serving base with allocated directors.csv"

# Storage
LOCAL_RESP_PATH = DATA_DIR / "responses_local.csv"

# Deadline file (availability month key -> deadline in prior month)
DEADLINES_PATH = DATA_DIR / "deadlines.csv"


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
def load_base_questions_and_serving():
    base_q = _normalize_columns(_read_csv_any(BASE_Q_PATH))
    sb = _normalize_columns(_read_csv_any(SB_PATH))

    needed_bq = {
        "QuestionID",
        "QuestionText",
        "ReportLabel",
        "QuestionType",
        "OptionsSource",
        "DependsOn",
        "ShowCondition",
    }
    miss_bq = needed_bq - set(base_q.columns)
    if miss_bq:
        raise RuntimeError(
            f"`form_base_questions.csv` missing columns: {', '.join(sorted(miss_bq))}"
        )

    needed_sb = {"Director", "Serving Girl"}
    miss_sb = needed_sb - set(sb.columns)
    if miss_sb:
        raise RuntimeError(
            f"`Serving base with allocated directors.csv` missing columns: {', '.join(sorted(miss_sb))}"
        )

    base_q = base_q.assign(
        QuestionID=base_q["QuestionID"].astype(str).str.strip(),
        QuestionText=base_q["QuestionText"].astype(str).str.strip(),
        ReportLabel=base_q["ReportLabel"].astype(str).str.strip(),
        QuestionType=base_q["QuestionType"].astype(str).str.strip(),
        OptionsSource=base_q["OptionsSource"].astype(str).str.strip(),
        DependsOn=base_q["DependsOn"].astype(str).str.strip(),
        ShowCondition=base_q["ShowCondition"].astype(str).str.strip(),
    )

    sb = sb.assign(
        Director=sb["Director"].astype(str).str.strip(),
        **{"Serving Girl": sb["Serving Girl"].astype(str).str.strip()},
    )

    serving_map = (
        sb.groupby("Director")["Serving Girl"]
        .apply(lambda s: sorted({x for x in s if x}))
        .to_dict()
    )

    return base_q, sb, serving_map


@st.cache_data(show_spinner=False)
def load_service_dates() -> pd.DataFrame:
    df = _normalize_columns(_read_csv_any(DATES_PATH))
    needed = {"target_month", "date", "label", "is_service_day"}
    miss = needed - set(df.columns)
    if miss:
        raise RuntimeError(f"`service_dates.csv` missing columns: {', '.join(sorted(miss))}")

    df = df.assign(
        target_month=df["target_month"].astype(str).str.strip(),
        date=df["date"].astype(str).str.strip(),
        label=df["label"].astype(str).str.strip(),
        is_service_day=df["is_service_day"].fillna(0),
    )

    def _to_int(x):
        try:
            return int(float(x))
        except Exception:
            return 0

    df["is_service_day"] = df["is_service_day"].map(_to_int)
    return df


@st.cache_data(show_spinner=False)
def load_deadlines() -> pd.DataFrame:
    df = _normalize_columns(_read_csv_any(DEADLINES_PATH))
    needed = {"month", "deadline_local", "timezone"}
    miss = needed - set(df.columns)
    if miss:
        raise RuntimeError(f"`deadlines.csv` missing columns: {', '.join(sorted(miss))}")

    df = df.assign(
        month=df["month"].astype(str).str.strip(),
        deadline_local=df["deadline_local"].astype(str).str.strip(),
        timezone=df["timezone"].astype(str).str.strip(),
    )
    return df


# ──────────────────────────────────────────────────────────────────────────────
# Time helpers
# ──────────────────────────────────────────────────────────────────────────────
def get_now_in_tz(tz_name: str) -> datetime:
    if ZoneInfo is None:
        return datetime.utcnow()
    return datetime.now(ZoneInfo(tz_name))


def parse_deadline_local(deadline_local: str, tz_name: str) -> datetime:
    dt_naive = datetime.strptime(deadline_local, "%Y-%m-%d %H:%M")
    if ZoneInfo is None:
        return dt_naive
    return dt_naive.replace(tzinfo=ZoneInfo(tz_name))


def add_one_month(dt: datetime) -> datetime:
    y, m = dt.year, dt.month
    if m == 12:
        y2, m2 = y + 1, 1
    else:
        y2, m2 = y, m + 1
    if dt.tzinfo:
        return datetime(y2, m2, 1, tzinfo=dt.tzinfo)
    return datetime(y2, m2, 1)


def get_target_month_key(now_local: datetime) -> str:
    """In Feb -> target is Mar, in Mar -> target is Apr, etc."""
    return add_one_month(now_local).strftime("%Y-%m")


def get_deadline_for_target_month(deadlines_df: pd.DataFrame, target_month_key: str):
    tz_guess = "Africa/Johannesburg"
    if not deadlines_df.empty and str(deadlines_df["timezone"].iloc[0]).strip():
        tz_guess = str(deadlines_df["timezone"].iloc[0]).strip()

    match = deadlines_df[deadlines_df["month"] == target_month_key]
    if match.empty:
        return None, tz_guess

    row = match.iloc[0]
    tz_name = str(row["timezone"]).strip() or tz_guess
    deadline_dt = parse_deadline_local(str(row["deadline_local"]).strip(), tz_name)
    return deadline_dt, tz_name


def format_minutes_remaining(delta_seconds: float) -> str:
    mins = max(0, int(delta_seconds // 60))
    hrs = mins // 60
    rem_m = mins % 60
    if hrs > 0:
        return f"{hrs}h {rem_m}m"
    return f"{rem_m}m"


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
        ws = sh.add_worksheet(title="Responses", rows=1, cols=120)
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
# Dynamic rules
# ──────────────────────────────────────────────────────────────────────────────
def required_yes_for_count(n_dates: int) -> int:
    # Your rule:
    # - 5 dates => must say YES to at least 3
    # - 4 dates => must say YES to at least 2
    if n_dates >= 5:
        return 3
    return 2


def yes_count_from_labels(answers: dict, labels: list[str]) -> int:
    return sum(1 for lbl in labels if str(answers.get(lbl, "")).strip().lower() == "yes")


def build_human_report(
    target_month_key: str,
    director: str,
    name: str,
    date_labels: list[str],
    answers: dict,
    reason: str,
) -> str:
    lines = [
        f"Availability month: {target_month_key}",
        f"Director: {director or '—'}",
        f"Serving Girl: {name or '—'}",
        "Availability:",
    ]
    for lbl in date_labels:
        val = (answers.get(lbl) or "No").title()
        lines.append(f"{lbl}: {val}")
    if reason:
        lines.append(f"Reason: {reason}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Load input data
# ──────────────────────────────────────────────────────────────────────────────
try:
    _base_questions, serving_base, serving_map = load_base_questions_and_serving()
    service_dates_all = load_service_dates()
except Exception as e:
    st.error(f"Data load error: {e}")
    with st.expander("Debug info"):
        st.code(str(BASE_Q_PATH))
        st.code(str(DATES_PATH))
        st.code(str(SB_PATH))
        try:
            st.write("Directory listing of ./data:", [p.name for p in DATA_DIR.iterdir()])
        except Exception:
            st.write("Could not list ./data")
    st.stop()

# Determine base timezone (prefer deadlines.csv timezone if present)
BASE_TZ = "Africa/Johannesburg"
try:
    if DEADLINES_PATH.exists():
        ddf = load_deadlines()
        if not ddf.empty and str(ddf["timezone"].iloc[0]).strip():
            BASE_TZ = str(ddf["timezone"].iloc[0]).strip()
except Exception:
    pass

# Target month is next month
now_base = get_now_in_tz(BASE_TZ)
target_month_key = get_target_month_key(now_base)

# Pull only that month's service dates
month_dates = service_dates_all[
    (service_dates_all["target_month"] == target_month_key)
    & (service_dates_all["is_service_day"] == 1)
].copy()

if month_dates.empty:
    st.markdown(
        f"""
        ## 🔒 This month’s availability form is not open yet.

        No service dates were found for **{target_month_key}**.

        Please contact your director.
        """
    )
    st.stop()

# Sort by actual date
def _safe_parse_date(s):
    try:
        return datetime.strptime(str(s), "%Y-%m-%d")
    except Exception:
        return datetime(1900, 1, 1)

month_dates["_sort"] = month_dates["date"].map(_safe_parse_date)
month_dates = month_dates.sort_values("_sort").drop(columns=["_sort"])

date_labels = month_dates["label"].astype(str).tolist()
n_dates = len(date_labels)
required_yes = required_yes_for_count(n_dates)

# ──────────────────────────────────────────────────────────────────────────────
# Deadline enforcement + ONLY-screen closed message (your requested wording)
# ──────────────────────────────────────────────────────────────────────────────
deadline_dt = None
deadline_tz = BASE_TZ
is_closed = False
remaining_seconds = None

try:
    if DEADLINES_PATH.exists():
        deadlines_df = load_deadlines()
        deadline_dt, deadline_tz = get_deadline_for_target_month(deadlines_df, target_month_key)

        if deadline_dt is None:
            is_closed = True
        else:
            now_local = get_now_in_tz(deadline_tz)
            remaining_seconds = (deadline_dt - now_local).total_seconds()
            is_closed = remaining_seconds <= 0

        # If closed, show ONLY your message (dynamic month names) and stop.
        if is_closed:
            # target month name (e.g., March)
            target_month_dt = datetime.strptime(target_month_key, "%Y-%m")
            target_month_name = target_month_dt.strftime("%B")

            # next availability cycle month (e.g., April)
            next_cycle_dt = add_one_month(target_month_dt)
            next_cycle_name = next_cycle_dt.strftime("%B")

            # open date is 1st of the target month (e.g., "1 March")
            open_date_str = target_month_dt.strftime("1 %B")

            st.markdown(
                f"""
                ## 🔒 {target_month_name} availability submissions are now closed.

                If you have not submitted your dates, please contact your director.

                ---

                **{next_cycle_name} availability submissions will open on {open_date_str}.**
                """
            )
            st.stop()

        # If open, show countdown info and refresh every 60 seconds (minute countdown)
        st.markdown('<meta http-equiv="refresh" content="60">', unsafe_allow_html=True)
        st.info(
            f"🗓️ Submitting availability for **{target_month_key}**.\n\n"
            f"✅ You must select **YES** for at least **{required_yes}** date(s) "
            f"(this month has **{n_dates}** service dates).\n\n"
            f"⏳ Form closes at **{deadline_dt.strftime('%Y-%m-%d %H:%M')}** ({deadline_tz}). "
            f"Time remaining: **{format_minutes_remaining(remaining_seconds)}**"
        )
    else:
        st.warning("⚠️ deadlines.csv not found. Deadline enforcement is OFF.")
except Exception as e:
    st.warning(f"⚠️ Deadline check error: {e}. Deadline enforcement is OFF.")


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
answers["Q1"] = st.selectbox(
    "Please select your director’s name",
    options=[""] + directors,
    index=0,
)

if answers.get("Q1"):
    girls = serving_map.get(answers["Q1"], [])
    answers["Q2"] = st.selectbox(
        "Please select your name",
        options=[""] + girls,
        index=0,
    )
else:
    answers["Q2"] = ""

st.subheader(f"Availability for {target_month_key}")

radio_options = ["Yes", "No"]
for lbl in date_labels:
    saved = answers.get(lbl)
    idx = radio_options.index(saved) if saved in radio_options else None
    choice = st.radio(
        f"Are you available {lbl}?",
        options=radio_options,
        index=idx,
        key=f"avail_{target_month_key}_{lbl}",
        horizontal=False,
    )
    answers[lbl] = choice

yes_cnt = yes_count_from_labels(answers, date_labels)
needs_reason = yes_cnt < required_yes

# Reason box (dynamic rule)
reason_text = answers.get("Q_REASON", "")
if needs_reason:
    answers["Q_REASON"] = st.text_area(
        f"Please provide a reason why you cannot serve **{required_yes}** time(s) this month:",
        value=reason_text,
    )
else:
    answers["Q_REASON"] = reason_text

# Review
st.subheader("Review")
c1, c2, c3, c4 = st.columns(4)
with c1:
    st.metric("Director", answers.get("Q1") or "—")
with c2:
    st.metric("Name", answers.get("Q2") or "—")
with c3:
    st.metric("Yes count", yes_cnt)
with c4:
    st.metric("Required YES", required_yes)


# ──────────────────────────────────────────────────────────────────────────────
# Submit (sticky)
# ──────────────────────────────────────────────────────────────────────────────
errors = {}
st.markdown('<div class="sticky-submit">', unsafe_allow_html=True)
submitted = st.button("Submit")
st.markdown("</div>", unsafe_allow_html=True)

if submitted:
    # Hard deadline enforcement at submit time too (safety)
    if deadline_dt is not None:
        now_check = get_now_in_tz(deadline_tz)
        if (deadline_dt - now_check).total_seconds() <= 0:
            target_month_dt = datetime.strptime(target_month_key, "%Y-%m")
            target_month_name = target_month_dt.strftime("%B")
            next_cycle_dt = add_one_month(target_month_dt)
            next_cycle_name = next_cycle_dt.strftime("%B")
            open_date_str = target_month_dt.strftime("1 %B")

            st.markdown(
                f"""
                ## 🔒 {target_month_name} availability submissions are now closed.

                If you have not submitted your dates, please contact your director.

                ---

                **{next_cycle_name} availability submissions will open on {open_date_str}.**
                """
            )
            st.stop()

    if not answers.get("Q1"):
        errors["Q1"] = "Please select a director."
    if not answers.get("Q2"):
        errors["Q2"] = "Please select your name."

    if needs_reason:
        if not answers.get("Q_REASON") or len(str(answers["Q_REASON"]).strip()) < 5:
            errors["Q_REASON"] = "Please provide a brief reason (at least 5 characters)."

    if errors:
        for msg in errors.values():
            st.error(msg)
    else:
        now = datetime.utcnow().isoformat() + "Z"

        row_map = {
            "timestamp": now,
            "Availability month": target_month_key,
            "Director": answers.get("Q1") or "",
            "Serving Girl": answers.get("Q2") or "",
            "Reason": (answers.get("Q_REASON") or "").strip(),
        }
        for lbl in date_labels:
            row_map[lbl] = (answers.get(lbl) or "No").title()

        desired_header = ["timestamp", "Availability month", "Director", "Serving Girl", "Reason"] + date_labels

        try:
            if SHEETS_MODE:
                ws = get_worksheet()
                header = init_sheet_headers(desired_header)
                row = [row_map.get(col, "") for col in header]
                gs_retry(ws.append_row, row)
                clear_responses_cache()
                st.success("Submission saved to Google Sheets.")
            else:
                header = ensure_local_headers(desired_header)
                append_row_local(header, row_map)
                clear_responses_cache()
                st.success("Submission saved (local file).")
        except Exception as e:
            st.error(f"Failed to save submission: {e}")

        report_text = build_human_report(
            target_month_key=target_month_key,
            director=answers.get("Q1") or "",
            name=answers.get("Q2") or "",
            date_labels=date_labels,
            answers=answers,
            reason=(answers.get("Q_REASON") or "").strip(),
        )
        st.markdown("### 📄 Screenshot-friendly report (text)")
        st.code(report_text, language=None)
        st.download_button(
            "Download report as .txt",
            data=report_text.encode("utf-8"),
            file_name=f"Availability_{target_month_key}_{(answers.get('Q2') or 'name').replace(' ', '_')}.txt",
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
