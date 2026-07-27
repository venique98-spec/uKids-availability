# app_fixed.py
import time
import random
from io import BytesIO
from datetime import datetime

import pandas as pd
import streamlit as st

# ✅ Timezone-aware deadlines
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None  # fallback

# Optional: Google Sheets libs.
try:
    import gspread
    from gspread.exceptions import APIError, WorksheetNotFound
except Exception:
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
  .date-block {
    padding: 10px 0 4px 0;
    border-bottom: 1px solid #eee;
    margin-bottom: 4px;
  }
</style>
""",
    unsafe_allow_html=True,
)

# ──────────────────────────────────────────────────────────────────────────────
# Google Sheets (Option A: one spreadsheet, 4 tabs)
# Tabs: Responses, ServingBase, Deadlines, ServiceDates
# ──────────────────────────────────────────────────────────────────────────────
TAB_RESPONSES = "Responses"
TAB_SERVING = "ServingBase"
TAB_DEADLINES = "Deadlines"
TAB_DATES = "ServiceDates"

# Optional columns in ServingBase:
BREAK_WEEKS_COL = "Break weeks"
BREAK_SINCE_COL = "Break since"

# Session labels used for the Morning / Evening split
SESSION_AM = "AM"
SESSION_PM = "PM"
SESSION_LABELS = {SESSION_AM: "Morning", SESSION_PM: "Evening"}

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
    sid = _get_secret_any(["GSHEET_ID"], ["general", "GSHEET_ID"])
    return bool(sa and sid)


SHEETS_MODE = is_sheets_enabled()
if not SHEETS_MODE:
    st.error("Google Sheets is not configured. Add GSHEET_ID and [gcp_service_account] to Secrets.")
    st.stop()


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
def get_spreadsheet():
    """
    Open the single spreadsheet and return the gspread Spreadsheet object.
    Includes a robust private_key newline fixer.
    """
    sa = _get_secret_any(["gcp_service_account"], ["general", "gcp_service_account"])
    sheet_id = _get_secret_any(["GSHEET_ID"], ["general", "GSHEET_ID"])

    if not sa or not sheet_id:
        raise RuntimeError("Missing GSHEET_ID or gcp_service_account in secrets.")

    sa = dict(sa)
    pk = sa.get("private_key", "")
    if isinstance(pk, str):
        pk = pk.replace("\\n", "\n").strip()
        if not pk.endswith("\n"):
            pk += "\n"
        sa["private_key"] = pk

    gc = gspread.service_account_from_dict(sa)
    sh = gs_retry(gc.open_by_key, sheet_id)
    return sh


def ensure_worksheet(sh, title: str, rows: int = 2000, cols: int = 50):
    try:
        return sh.worksheet(title)
    except WorksheetNotFound:
        return sh.add_worksheet(title=title, rows=rows, cols=cols)


def ws_get_df(ws) -> pd.DataFrame:
    values = gs_retry(ws.get_all_values)
    if not values:
        return pd.DataFrame()
    header, rows = values[0], values[1:]
    if not header:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=header)


def ws_ensure_header(ws, desired_header: list[str]) -> list[str]:
    header = gs_retry(ws.row_values, 1)
    if not header:
        gs_retry(ws.update, "1:1", [desired_header])
        return desired_header
    missing = [c for c in desired_header if c not in header]
    if missing:
        header = header + missing
        gs_retry(ws.update, "1:1", [header])
    return header


@st.cache_data(ttl=30, show_spinner=False)
def fetch_serving_df() -> pd.DataFrame:
    sh = get_spreadsheet()
    ws = ensure_worksheet(sh, TAB_SERVING, rows=4000, cols=20)
    return ws_get_df(ws)


@st.cache_data(ttl=30, show_spinner=False)
def fetch_deadlines_df() -> pd.DataFrame:
    sh = get_spreadsheet()
    ws = ensure_worksheet(sh, TAB_DEADLINES, rows=500, cols=10)
    return ws_get_df(ws)


@st.cache_data(ttl=30, show_spinner=False)
def fetch_service_dates_df() -> pd.DataFrame:
    sh = get_spreadsheet()
    ws = ensure_worksheet(sh, TAB_DATES, rows=4000, cols=10)
    return ws_get_df(ws)


@st.cache_data(ttl=30, show_spinner=False)
def fetch_responses_df() -> pd.DataFrame:
    sh = get_spreadsheet()
    ws = ensure_worksheet(sh, TAB_RESPONSES, rows=8000, cols=250)
    return ws_get_df(ws)


def append_response_row(desired_header: list[str], row_map: dict):
    sh = get_spreadsheet()
    ws = ensure_worksheet(sh, TAB_RESPONSES, rows=8000, cols=max(250, len(desired_header) + 10))
    header = ws_ensure_header(ws, desired_header)
    row = [row_map.get(col, "") for col in header]
    gs_retry(ws.append_row, row)


def clear_caches():
    for fn in (fetch_serving_df, fetch_deadlines_df, fetch_service_dates_df, fetch_responses_df):
        try:
            fn.clear()
        except Exception:
            pass
    try:
        st.cache_data.clear()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# Time helpers
# ─────────────────────────────────────────────────────────────
def get_now_in_tz(tz_name: str) -> datetime:
    if ZoneInfo is None:
        return datetime.utcnow()
    return datetime.now(ZoneInfo(tz_name))


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


def parse_deadline_local(deadline_local: str, tz_name: str) -> datetime:
    dt_naive = datetime.strptime(deadline_local, "%Y-%m-%d %H:%M")
    if ZoneInfo is None:
        return dt_naive
    return dt_naive.replace(tzinfo=ZoneInfo(tz_name))


def format_minutes_remaining(delta_seconds: float) -> str:
    mins = max(0, int(delta_seconds // 60))
    hrs = mins // 60
    rem_m = mins % 60
    if hrs > 0:
        return f"{hrs}h {rem_m}m"
    return f"{rem_m}m"


# ─────────────────────────────────────────────────────────────
# Business rules
# ─────────────────────────────────────────────────────────────
def required_sessions_for_count(n_dates: int) -> int:
    """
    Each service date has a Morning AND an Evening service.
    A 4-date (4-week) month requires 2 Morning + 2 Evening selections.
    A 5-date (5-week) month requires 3 Morning + 3 Evening selections.
    """
    return 3 if n_dates >= 5 else 2


def session_yes_count(answers: dict, labels: list[str], session: str) -> int:
    return sum(
        1 for lbl in labels if str(answers.get(f"{lbl}__{session}", "")).strip().lower() == "yes"
    )


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
        am_val = (answers.get(f"{lbl}__{SESSION_AM}") or "No").title()
        pm_val = (answers.get(f"{lbl}__{SESSION_PM}") or "No").title()
        lines.append(f"{lbl} — Morning: {am_val}, Evening: {pm_val}")
    if reason:
        lines.append(f"Reason: {reason}")
    return "\n".join(lines)


def _safe_parse_date_ymd(s: str) -> datetime:
    try:
        return datetime.strptime(str(s).strip(), "%Y-%m-%d")
    except Exception:
        return datetime(1900, 1, 1)


def _to_float_or_none(v):
    try:
        if v is None:
            return None
        s = str(v).strip()
        if s == "" or s.lower() in {"none", "nan"}:
            return None
        return float(s)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# Load config from Google Sheets
# ─────────────────────────────────────────────────────────────
try:
    serving_base = fetch_serving_df()
    deadlines_df = fetch_deadlines_df()
    service_dates_all = fetch_service_dates_df()
except Exception as e:
    st.error(f"Failed to load config from Google Sheets: {e}")
    st.stop()

for df, name, needed in [
    (serving_base, "ServingBase", {"Director", "Serving Girl"}),
    (deadlines_df, "Deadlines", {"month", "deadline_local", "timezone"}),
    (service_dates_all, "ServiceDates", {"target_month", "date", "label", "is_service_day"}),
]:
    miss = needed - set(df.columns)
    if miss:
        st.error(f"Google Sheet tab '{name}' is missing columns: {', '.join(sorted(miss))}")
        st.stop()

# Clean columns (only the columns the app actually relies on;
# extra columns like Username, Password, Campus, Age, Serving Role, etc.
# are simply ignored and left untouched in the sheet)
serving_base["Director"] = serving_base["Director"].astype(str).str.strip()
serving_base["Serving Girl"] = serving_base["Serving Girl"].astype(str).str.strip()

if BREAK_SINCE_COL in serving_base.columns:
    serving_base[BREAK_SINCE_COL] = serving_base[BREAK_SINCE_COL].astype(str).str.strip()
else:
    serving_base[BREAK_SINCE_COL] = ""

if BREAK_WEEKS_COL in serving_base.columns:
    serving_base[BREAK_WEEKS_COL] = serving_base[BREAK_WEEKS_COL].astype(str).str.strip()
else:
    serving_base[BREAK_WEEKS_COL] = ""

serving_base = serving_base[(serving_base["Director"] != "") & (serving_base["Serving Girl"] != "")].drop_duplicates()

deadlines_df["month"] = deadlines_df["month"].astype(str).str.strip()
deadlines_df["deadline_local"] = deadlines_df["deadline_local"].astype(str).str.strip()
deadlines_df["timezone"] = deadlines_df["timezone"].astype(str).str.strip()

service_dates_all["target_month"] = service_dates_all["target_month"].astype(str).str.strip()
service_dates_all["date"] = service_dates_all["date"].astype(str).str.strip()
service_dates_all["label"] = service_dates_all["label"].astype(str).str.strip()
service_dates_all["is_service_day"] = service_dates_all["is_service_day"].astype(str).str.strip()

serving_map = (
    serving_base.groupby("Director")["Serving Girl"]
    .apply(lambda s: sorted({x for x in s if x}))
    .to_dict()
)

BASE_TZ = "Africa/Johannesburg"
try:
    tz0 = str(deadlines_df["timezone"].iloc[0]).strip()
    if tz0:
        BASE_TZ = tz0
except Exception:
    pass

now_base = get_now_in_tz(BASE_TZ)
target_month_key = get_target_month_key(now_base)

month_dates = service_dates_all[
    (service_dates_all["target_month"] == target_month_key)
    & (service_dates_all["is_service_day"] == "1")
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

month_dates["_sort"] = month_dates["date"].map(_safe_parse_date_ymd)
month_dates = month_dates.sort_values("_sort").drop(columns=["_sort"])

date_labels = month_dates["label"].astype(str).tolist()
required_sessions = required_sessions_for_count(len(date_labels))


def get_deadline_for_target_month(deadlines: pd.DataFrame, month_key: str):
    tz_guess = BASE_TZ
    match = deadlines[deadlines["month"] == month_key]
    if match.empty:
        return None, tz_guess
    row = match.iloc[0]
    tz_name = str(row["timezone"]).strip() or tz_guess
    dl = parse_deadline_local(str(row["deadline_local"]).strip(), tz_name)
    return dl, tz_name


deadline_dt, deadline_tz = get_deadline_for_target_month(deadlines_df, target_month_key)

is_closed = True
if deadline_dt is not None:
    now_local = get_now_in_tz(deadline_tz)
    is_closed = (deadline_dt - now_local).total_seconds() <= 0

if is_closed:
    target_month_dt = datetime.strptime(target_month_key, "%Y-%m")
    target_month_name = target_month_dt.strftime("%B")
    next_cycle_dt = add_one_month(target_month_dt)
    next_cycle_name = next_cycle_dt.strftime("%B")
    open_date_str = target_month_dt.strftime("1 %B")

    st.markdown(
        f"""
        ## 🔒 {target_month_name} availability submissions are now closed.

        If you have not submitted your dates, please contact your director.

        <hr style="margin:30px 0;">

        <div style="
            color: #1e7e34;
            font-size: 24px;
            font-weight: 600;
        ">
            {next_cycle_name} availability submissions will open on {open_date_str}.
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

# Countdown + policy note
now_local = get_now_in_tz(deadline_tz)
remaining_seconds = (deadline_dt - now_local).total_seconds()

st.info(
    f"🗓️ Submitting availability for **{target_month_key}**.\n\n"
    f"✅ Each date has a **Morning** and an **Evening** service. "
    f"You must select **YES** for at least **{required_sessions} Morning** service(s) "
    f"and **{required_sessions} Evening** service(s).\n\n"
    f"⏳ Form closes at **{deadline_dt.strftime('%Y-%m-%d %H:%M')}** ({deadline_tz}). "
    f"Time remaining: **{format_minutes_remaining(remaining_seconds)}**\n\n"
    f"🔁 You are welcome to submit this form more than once. "
    f"We will use your most recent submission for scheduling. "
    f"Please remember to send a screenshot of your final submission to your director."
)

if st.button("Refresh timer"):
    st.rerun()

# ─────────────────────────────────────────────────────────────
# UI state
# ─────────────────────────────────────────────────────────────
if "answers" not in st.session_state:
    st.session_state.answers = {}
answers = st.session_state.answers

# ─────────────────────────────────────────────────────────────
# Form UI
# ─────────────────────────────────────────────────────────────
st.subheader("Your details")
directors = sorted([d for d in serving_map.keys() if d])
answers["Q1"] = st.selectbox("Please select your director’s name", options=[""] + directors, index=0)

if answers.get("Q1"):
    girls = serving_map.get(answers["Q1"], [])
    answers["Q2"] = st.selectbox("Please select your name", options=[""] + girls, index=0)
else:
    answers["Q2"] = ""

st.subheader(f"Availability for {target_month_key}")
st.caption("Each date below has a Morning and an Evening service — please answer both.")

radio_options = ["Yes", "No"]
for lbl in date_labels:
    st.markdown(f'<div class="date-block"><strong>{lbl}</strong></div>', unsafe_allow_html=True)
    col_am, col_pm = st.columns(2)

    saved_am = answers.get(f"{lbl}__{SESSION_AM}")
    idx_am = radio_options.index(saved_am) if saved_am in radio_options else None
    with col_am:
        choice_am = st.radio(
            f"{SESSION_LABELS[SESSION_AM]}",
            options=radio_options,
            index=idx_am,
            key=f"avail_{target_month_key}_{lbl}_{SESSION_AM}",
            horizontal=True,
        )

    saved_pm = answers.get(f"{lbl}__{SESSION_PM}")
    idx_pm = radio_options.index(saved_pm) if saved_pm in radio_options else None
    with col_pm:
        choice_pm = st.radio(
            f"{SESSION_LABELS[SESSION_PM]}",
            options=radio_options,
            index=idx_pm,
            key=f"avail_{target_month_key}_{lbl}_{SESSION_PM}",
            horizontal=True,
        )

    answers[f"{lbl}__{SESSION_AM}"] = choice_am
    answers[f"{lbl}__{SESSION_PM}"] = choice_pm

morning_yes = session_yes_count(answers, date_labels, SESSION_AM)
evening_yes = session_yes_count(answers, date_labels, SESSION_PM)
needs_reason = morning_yes < required_sessions or evening_yes < required_sessions

if needs_reason:
    answers["Q_REASON"] = st.text_area(
        f"Please provide a reason why you cannot serve **{required_sessions} Morning** and "
        f"**{required_sessions} Evening** service(s) this month:",
        value=answers.get("Q_REASON", ""),
    )
else:
    answers["Q_REASON"] = answers.get("Q_REASON", "")

st.subheader("Review")
c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    st.metric("Director", answers.get("Q1") or "—")
with c2:
    st.metric("Name", answers.get("Q2") or "—")
with c3:
    st.metric("Morning YES", f"{morning_yes}/{required_sessions}")
with c4:
    st.metric("Evening YES", f"{evening_yes}/{required_sessions}")
with c5:
    st.metric("Required", required_sessions)

# ─────────────────────────────────────────────────────────────
# Submit (sticky)
# ─────────────────────────────────────────────────────────────
errors = {}
st.markdown('<div class="sticky-submit">', unsafe_allow_html=True)
submitted = st.button("Submit")
st.markdown("</div>", unsafe_allow_html=True)

if submitted:
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

            <hr style="margin:30px 0;">

            <div style="
                color: #1e7e34;
                font-size: 24px;
                font-weight: 600;
            ">
                {next_cycle_name} availability submissions will open on {open_date_str}.
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.stop()

    if not answers.get("Q1"):
        errors["Q1"] = "Please select a director."
    if not answers.get("Q2"):
        errors["Q2"] = "Please select your name."
    if needs_reason and (not answers.get("Q_REASON") or len(str(answers["Q_REASON"]).strip()) < 5):
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
            row_map[f"{lbl} - Morning"] = (answers.get(f"{lbl}__{SESSION_AM}") or "No").title()
            row_map[f"{lbl} - Evening"] = (answers.get(f"{lbl}__{SESSION_PM}") or "No").title()

        desired_header = ["timestamp", "Availability month", "Director", "Serving Girl", "Reason"]
        for lbl in date_labels:
            desired_header += [f"{lbl} - Morning", f"{lbl} - Evening"]

        try:
            append_response_row(desired_header, row_map)
            clear_caches()
            st.success("Submission saved to Google Sheets.")
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

# ─────────────────────────────────────────────────────────────
# Admin: exports + non-responders + diagnostics
# ─────────────────────────────────────────────────────────────
def compute_nonresponders_with_breaks(serving_base_df: pd.DataFrame, responses_df: pd.DataFrame) -> pd.DataFrame:
    if serving_base_df is None or serving_base_df.empty:
        return pd.DataFrame(columns=["Director", "Serving Girl", "Status", BREAK_WEEKS_COL, BREAK_SINCE_COL])

    sb = serving_base_df[["Director", "Serving Girl", BREAK_WEEKS_COL, BREAK_SINCE_COL]].copy()
    sb["Director"] = sb["Director"].astype(str).str.strip()
    sb["Serving Girl"] = sb["Serving Girl"].astype(str).str.strip()
    sb[BREAK_WEEKS_COL] = sb[BREAK_WEEKS_COL].astype(str).str.strip()
    sb[BREAK_SINCE_COL] = sb[BREAK_SINCE_COL].astype(str).str.strip()
    sb = sb[(sb["Director"] != "") & (sb["Serving Girl"] != "")].drop_duplicates()

    if responses_df is None or responses_df.empty:
        out = sb.copy()
        out["Responded"] = False
        out["Last submission"] = ""
    else:
        cols = list(responses_df.columns)
        ts_col = "timestamp" if "timestamp" in cols else (cols[0] if cols else "timestamp")
        use_cols = [c for c in cols if c in ["Director", "Serving Girl"] or c == ts_col]
        resp = responses_df[use_cols].copy()
        if ts_col not in resp.columns:
            resp[ts_col] = ""
        resp.rename(columns={ts_col: "Last submission"}, inplace=True)

        resp["Director"] = resp["Director"].astype(str).str.strip()
        resp["Serving Girl"] = resp["Serving Girl"].astype(str).str.strip()
        resp = resp.sort_values("Last submission").drop_duplicates(subset=["Director", "Serving Girl"], keep="last")

        out = sb.merge(resp, on=["Director", "Serving Girl"], how="left")
        out["Responded"] = out["Last submission"].notna() & (out["Last submission"] != "")

    out = out[~out["Responded"]].copy()
    out["_break_weeks_num"] = out[BREAK_WEEKS_COL].map(_to_float_or_none)
    out["Status"] = out["_break_weeks_num"].apply(lambda x: "On break" if (x is not None and x > 0) else "Non-responder")

    def _break_display(row):
        w = row["_break_weeks_num"]
        if w is None or w <= 0:
            return ""
        if abs(w - int(w)) < 1e-9:
            w = int(w)
        return f"{w} week(s)"

    out["Break duration"] = out.apply(_break_display, axis=1)

    cols_out = ["Director", "Serving Girl", "Status", "Break duration"]
    if BREAK_WEEKS_COL in out.columns:
        cols_out.append(BREAK_WEEKS_COL)
    if BREAK_SINCE_COL in out.columns:
        cols_out.append(BREAK_SINCE_COL)
    cols_out.append("Last submission")

    for c in cols_out:
        if c not in out.columns:
            out[c] = ""

    out = out[cols_out].sort_values(["Status", "Director", "Serving Girl"]).reset_index(drop=True)
    return out


with st.expander("Admin"):
    st.caption("Mode: Google Sheets (one sheet, 4 tabs)")
    if not ADMIN_KEY:
        st.info("To protect exports, set an ADMIN_KEY in Streamlit Secrets (optional).")

    key = st.text_input("Enter admin key to access exports", type="password")
    if ADMIN_KEY and key != ADMIN_KEY:
        if key:
            st.error("Incorrect admin key.")
    else:
        st.success("Admin unlocked.")
        try:
            responses_df = fetch_responses_df()
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

        st.markdown("### ❌ Non-responders (with break info)")
        nonresp_df = compute_nonresponders_with_breaks(serving_base, responses_df)

        all_directors = ["All"] + sorted(serving_base["Director"].unique().tolist())
        sel_dir = st.selectbox("Filter by director", options=all_directors, index=0)
        status_filter = st.radio("Show", options=["Active non-responders", "On break", "All"], horizontal=True)

        view_df = nonresp_df.copy()
        if sel_dir != "All":
            view_df = view_df[view_df["Director"] == sel_dir]

        if status_filter == "Active non-responders":
            view_df = view_df[view_df["Status"] == "Non-responder"]
        elif status_filter == "On break":
            view_df = view_df[view_df["Status"] == "On break"]

        total_expected = len(serving_base[["Director", "Serving Girl"]].dropna().drop_duplicates())
        st.write(f"Shown: **{len(view_df)}**  |  Total expected pairs: **{total_expected}**")

        show_cols = ["Director", "Serving Girl", "Status", "Break duration"]
        if BREAK_WEEKS_COL in view_df.columns:
            show_cols.append(BREAK_WEEKS_COL)
        if BREAK_SINCE_COL in view_df.columns:
            show_cols.append(BREAK_SINCE_COL)
        st.dataframe(view_df[show_cols], use_container_width=True)

        st.divider()
        st.markdown("#### 🔍 Secrets / Sheets check")
        try:
            s = st.secrets
            gsa = s.get("gcp_service_account", {})
            gs_id = s.get("GSHEET_ID") or s.get("general", {}).get("GSHEET_ID")
            st.write(
                {
                    "GSHEET_ID_present": bool(gs_id),
                    "client_email": gsa.get("client_email", "(missing)"),
                    "private_key_present": bool(gsa.get("private_key")),
                    "gspread_installed": gspread is not None,
                    "tabs_expected": [TAB_RESPONSES, TAB_SERVING, TAB_DEADLINES, TAB_DATES],
                    "servingbase_has_break_weeks": BREAK_WEEKS_COL in serving_base.columns,
                    "servingbase_has_break_since": BREAK_SINCE_COL in serving_base.columns,
                }
            )

            sh = get_spreadsheet()
            ensure_worksheet(sh, TAB_RESPONSES, rows=8000, cols=250)
            ensure_worksheet(sh, TAB_SERVING, rows=4000, cols=20)
            ensure_worksheet(sh, TAB_DEADLINES, rows=500, cols=10)
            ensure_worksheet(sh, TAB_DATES, rows=4000, cols=10)
            st.success(f"✅ Auth OK. Opened sheet: {sh.title}")
        except Exception as e:
            st.error(f"❌ Diagnostics failed: {e}")
