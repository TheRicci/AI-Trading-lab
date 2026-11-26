import azure.functions as func
import logging
import os
import json
import uuid
import datetime
import requests

app = func.FunctionApp()

EVENTGRID_TOPIC_ENDPOINT = os.environ.get("EVENTGRID_TOPIC_ENDPOINT")
EVENTGRID_TOPIC_KEY = os.environ.get("EVENTGRID_TOPIC_KEY")


# ── NYSE Holiday Guard ───────────────────────────────────────────────────────
# Returns the set of NYSE market holidays for a given year as "YYYY-MM-DD"
# strings.  Uses observed-day rules (Mon when holiday falls on Sun, Fri when
# it falls on Sat).  No external packages required.
def _nyse_holidays(year: int) -> set:
    from datetime import date

    def observed(d: date) -> date:
        """Shift a holiday to the nearest weekday when it falls on a weekend."""
        if d.weekday() == 5:   # Saturday → Friday
            return date(d.year, d.month, d.day - 1)
        if d.weekday() == 6:   # Sunday → Monday
            return date(d.year, d.month, d.day + 1)
        return d

    def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
        """Return the n-th occurrence (1-based) of weekday in month/year."""
        first = date(year, month, 1)
        delta = (weekday - first.weekday()) % 7
        return date(year, month, 1 + delta + (n - 1) * 7)

    def last_weekday(year: int, month: int, weekday: int) -> date:
        """Return the last occurrence of weekday in month/year."""
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        last = date(year, month, last_day)
        delta = (last.weekday() - weekday) % 7
        return date(year, month, last_day - delta)

    holidays = {
        observed(date(year, 1, 1)),    # New Year's Day
        observed(date(year, 6, 19)),   # Juneteenth (from 2022 onwards)
        observed(date(year, 7, 4)),    # Independence Day
        observed(date(year, 11, 11)),  # Veterans Day (not NYSE, skip)
        observed(date(year, 12, 25)),  # Christmas Day
        nth_weekday(year, 1, 0, 3),    # MLK Day (3rd Mon Jan)
        nth_weekday(year, 2, 0, 3),    # Presidents' Day (3rd Mon Feb)
        last_weekday(year, 5, 0),      # Memorial Day (last Mon May)
        nth_weekday(year, 9, 0, 1),    # Labor Day (1st Mon Sep)
        nth_weekday(year, 11, 3, 4),   # Thanksgiving (4th Thu Nov)
    }
    # Good Friday: Easter Sunday - 2 days (Butcher-Meeus algorithm)
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    easter = date(year, month, day)
    good_friday = easter - datetime.timedelta(days=2)
    holidays.add(good_friday)

    # Day after Thanksgiving (Black Friday - half day, NYSE is open but many
    # bots skip; keep it normal unless you want to add it here)

    # Remove Veterans Day (NYSE does NOT close for it)
    holidays.discard(observed(date(year, 11, 11)))

    return {d.strftime("%Y-%m-%d") for d in holidays}


def is_nyse_holiday(d: datetime.date | None = None) -> bool:
    """Return True if *d* (default: today in UTC) is a NYSE market holiday."""
    if d is None:
        d = datetime.datetime.now(datetime.timezone.utc).date()
    return d.strftime("%Y-%m-%d") in _nyse_holidays(d.year)


# NOTE ON TIMING: Azure Functions timer triggers run in UTC by default.
# Market open (9:30am ET) / close (4:00pm ET) shift with daylight saving
# time, so these UTC schedules will be off by an hour for roughly half the
# year. A more robust version would check today's date and pick the right
# UTC offset -- a good first improvement once the basic flow works.

# 13:30 UTC = 9:30am ET during daylight saving time (roughly Mar-Nov)
@app.timer_trigger(schedule="0 30 13 * * 1-5", arg_name="openTimer", run_on_startup=False)
def market_open(openTimer: func.TimerRequest) -> None:
    publish_session_event("open")


# 19:30 UTC = 3:30pm ET during daylight saving time (30 minutes before market close)
@app.timer_trigger(schedule="0 30 19 * * 1-5", arg_name="closeTimer", run_on_startup=False)
def market_close(closeTimer: func.TimerRequest) -> None:
    publish_session_event("close")


def publish_session_event(session: str) -> None:
    if is_nyse_holiday():
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        logging.info(f"[daily-timer] {today} is a NYSE holiday — skipping {session} session event.")
        return

    event = [{
        "id": str(uuid.uuid4()),
        "eventType": "Trading.SessionReview",
        "subject": f"session/{session}",
        "eventTime": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data": {"session": session},
        "dataVersion": "1.0",
    }]

    try:
        response = requests.post(
            EVENTGRID_TOPIC_ENDPOINT,
            headers={"aeg-sas-key": EVENTGRID_TOPIC_KEY, "Content-Type": "application/json"},
            data=json.dumps(event),
            timeout=10,
        )
        if response.status_code >= 300:
            logging.error(f"Failed to publish {session} event: {response.status_code} {response.text}")
        else:
            logging.info(f"Published session/{session} event to Event Grid")
    except requests.RequestException as e:
        logging.error(f"Error publishing {session} event: {e}")
