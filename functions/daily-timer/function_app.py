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
