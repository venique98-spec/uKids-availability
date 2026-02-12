# app_fixed.py
import time
import random
from io import BytesIO
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

try:
    import gspread
    from gspread.exceptions import APIError, WorksheetNotFound
except Exception:
    gspread = None
    class APIError(Exception): ...
    class WorksheetNotFound(Exception): ...

# ─────────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────────
st.set_page_config(page_title="Availability Form", page_icon="🗓️", layout="centered")
st.title("🗓️ Availability Form")

# ─────────────────────────────────────────────────────────────
# GOOGLE SHEETS SETUP (Option A)
# ─────────────────────────────────────────────────────────────
TAB_RESPONSES = "Responses"
TAB_SERVING = "ServingBase"
TAB_DEADLINES = "Deadlines"
TAB_DATES = "ServiceDates"

def _get_secret_any(*paths):
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

def sheets_enabled():
    if gspread is None:
        return False
    return bool(
        _get_secret_any(["gcp_service_account"]) and
        _get_secret_any(["GSHEET_ID"])
    )

SHEETS_MODE = sheets_enabled()

@st.cache_resource
def get_sheet():
    sa = _get_secret_any(["gcp_service_account"])
    sid = _get_secret_any(["GSHEET_ID"])
    gc = gspread.service_account_from_dict(sa)
    return gc.open_by_key(sid)

def get_tab_df(tab):
    sh = get_sheet()
    try:
        ws = sh.worksheet(tab)
    except WorksheetNotFound:
        ws = sh.add_worksheet(title=tab, rows=2000, cols=50)
        return pd.DataFrame()
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()
    return pd.DataFrame(values[1:], columns=values[0])

# ─────────────────────────────────────────────────────────────
# LOAD CONFIG
# ─────────────────────────────────────────────────────────────
if not SHEETS_MODE:
    st.error("Google Sheets is not configured.")
    st.stop()

serving_df = get_tab_df(TAB_SERVING)
deadlines_df = get_tab_df(TAB_DEADLINES)
dates_df = get_tab_df(TAB_DATES)

# ─────────────────────────────────────────────────────────────
# TIME LOGIC
# ─────────────────────────────────────────────────────────────
BASE_TZ = "Africa/Johannesburg"

def now_local():
    if ZoneInfo:
        return datetime.now(ZoneInfo(BASE_TZ))
    return datetime.utcnow()

def add_month(dt):
    y, m = dt.year, dt.month
    if m == 12:
        return datetime(y + 1, 1, 1)
    return datetime(y, m + 1, 1)

target_month_key = add_month(now_local()).strftime("%Y-%m")

# ─────────────────────────────────────────────────────────────
# SERVICE DATES
# ─────────────────────────────────────────────────────────────
dates_df = dates_df.copy()
dates_df = dates_df[
    (dates_df["target_month"] == target_month_key) &
    (dates_df["is_service_day"] == "1")
]

if dates_df.empty:
    st.error("No service dates configured.")
    st.stop()

dates_df["date"] = pd.to_datetime(dates_df["date"])
dates_df = dates_df.sort_values("date")
date_labels = dates_df["label"].tolist()

required_yes = 3 if len(date_labels) >= 5 else 2

# ─────────────────────────────────────────────────────────────
# DEADLINE CHECK
# ─────────────────────────────────────────────────────────────
deadline_row = deadlines_df[deadlines_df["month"] == target_month_key]

if deadline_row.empty:
    is_closed = True
else:
    deadline_str = deadline_row.iloc[0]["deadline_local"]
    deadline_dt = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M")
    is_closed = now_local() >= deadline_dt

if is_closed:
    target_name = datetime.strptime(target_month_key, "%Y-%m").strftime("%B")
    next_name = add_month(datetime.strptime(target_month_key, "%Y-%m")).strftime("%B")
    open_str = datetime.strptime(target_month_key, "%Y-%m").strftime("1 %B")

    st.markdown(
        f"""
        ## 🔒 {target_name} availability submissions are now closed.

        If you have not submitted your dates, please contact your director.

        <hr style="margin:30px 0;">

        <div style="
            color: #1e7e34;
            font-size: 24px;
            font-weight: 600;
        ">
            {next_name} availability submissions will open on {open_str}.
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

# ─────────────────────────────────────────────────────────────
# COUNTDOWN DISPLAY
# ─────────────────────────────────────────────────────────────
remaining = deadline_dt - now_local()
mins = int(remaining.total_seconds() // 60)

st.info(
    f"🗓️ Submitting availability for **{target_month_key}**.\n\n"
    f"✅ You must select **YES** for at least **{required_yes}** date(s).\n\n"
    f"⏳ Form closes at **{deadline_dt.strftime('%Y-%m-%d %H:%M')}** ({BASE_TZ}). "
    f"Time remaining: **{mins}m**\n\n"
    f"🔁 You are welcome to submit this form more than once. "
    f"We will use your most recent submission for scheduling. "
    f"Please remember to send a screenshot of your final submission to your director."
)

# ─────────────────────────────────────────────────────────────
# FORM
# ─────────────────────────────────────────────────────────────
serving_map = serving_df.groupby("Director")["Serving Girl"].apply(list).to_dict()

director = st.selectbox("Please select your director’s name", [""] + sorted(serving_map.keys()))

if director:
    name = st.selectbox("Please select your name", [""] + serving_map[director])
else:
    name = ""

st.subheader(f"Availability for {target_month_key}")

answers = {}
for lbl in date_labels:
    answers[lbl] = st.radio(f"Are you available {lbl}?", ["Yes", "No"])

yes_count = sum(1 for v in answers.values() if v == "Yes")

if yes_count < required_yes:
    reason = st.text_area(
        f"Please provide a reason why you cannot serve {required_yes} time(s) this month:"
    )
else:
    reason = ""

# ─────────────────────────────────────────────────────────────
# SUBMIT
# ─────────────────────────────────────────────────────────────
if st.button("Submit"):

    if not director or not name:
        st.error("Please select both director and name.")
    elif yes_count < required_yes and len(reason.strip()) < 5:
        st.error("Please provide a brief reason (minimum 5 characters).")
    else:
        row = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "Availability month": target_month_key,
            "Director": director,
            "Serving Girl": name,
            "Reason": reason,
        }
        row.update(answers)

        sh = get_sheet()
        ws = sh.worksheet(TAB_RESPONSES)
        header = ws.row_values(1)
        if not header:
            header = list(row.keys())
            ws.update("1:1", [header])
        ws.append_row([row.get(col, "") for col in header])

        st.success("Submission saved successfully.")
