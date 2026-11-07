"""
Amazon Calgary job watcher - GitHub Actions edition (REGULAR jobs only).
Runs ONE check each time it is called, then exits. GitHub calls it every ~5 minutes.
The ntfy topic comes from the NTFY_TOPIC secret, never from this file.
"""
import datetime
import json
import os
import sys
from pathlib import Path

import requests

RADIUS_KM = 50
REGULAR_ONLY = True      # False = also alert on seasonal jobs

BASE = "https://hiring.amazon.ca"
SEEN_FILE = Path(__file__).resolve().parent / "seen_jobs.json"
QUERY = (
    "query searchJobCardsByLocation($searchJobRequest: SearchJobRequest!) { "
    "searchJobCardsByLocation(searchJobRequest: $searchJobRequest) { nextToken "
    "jobCards { jobId jobTitle employmentType city state locationName totalPayRateMax } } }"
)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept": "*/*", "Origin": BASE, "Referer": BASE + "/app"})


def notify(topic, title, msg, click=None, priority="high"):
    headers = {"Title": title.encode("utf-8"), "Priority": priority}
    if click:
        headers["Click"] = click
    r = requests.post(f"https://ntfy.sh/{topic}", data=msg.encode("utf-8"), headers=headers, timeout=15)
    r.raise_for_status()


def get_token():
    r = session.get(f"{BASE}/authorize/api/csrf?countryCode=CA", timeout=30)
    r.raise_for_status()
    return r.json()["token"]


def build_body():
    equal = [{"key": "employmentType", "val": "Regular"}] if REGULAR_ONLY else []
    return {
        "operationName": "searchJobCardsByLocation",
        "variables": {"searchJobRequest": {
            "locale": "en-CA", "country": "Canada", "keyWords": "",
            "equalFilters": equal,
            "containFilters": [{"key": "isPrivateSchedule", "val": ["false"]}],
            "rangeFilters": [], "orFilters": [],
            "dateFilters": [{"key": "firstDayOnSite",
                             "range": {"startDate": datetime.date.today().isoformat()}}],
            "sorters": [{"fieldName": "totalPayRateMax", "ascending": "false"}],
            "pageSize": 100,
            "geoQueryClause": {"lat": 51.045113, "lng": -114.057141,
                               "unit": "km", "distance": RADIUS_KM},
        }},
        "query": QUERY,
    }


def fetch_jobs(token):
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer Status|unauthenticated|Session|{token}",
        "country": "Canada",
        "iscanary": "false",
    }
    r = session.post(f"{BASE}/graphql", headers=headers, data=json.dumps(build_body()), timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        raise RuntimeError(str(data["errors"])[:300])
    cards = data["data"]["searchJobCardsByLocation"]["jobCards"] or []
    return {c["jobId"]: c for c in cards}


def main():
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("ERROR: NTFY_TOPIC secret is not set.")
        return 1

    try:
        jobs = fetch_jobs(get_token())
    except Exception as e:
        # A failed run makes GitHub email you, so you will know if Amazon blocks it.
        print("ERROR fetching jobs:", e)
        return 1

    first_run = not SEEN_FILE.exists()
    seen = set(json.loads(SEEN_FILE.read_text())) if not first_run else set()
    kind = "regular" if REGULAR_ONLY else "all"

    if first_run:
        notify(topic, "Amazon watcher is running",
               f"Watching Calgary for {kind} jobs. {len(jobs)} listed right now.", priority="default")
        print(f"First run: baseline of {len(jobs)} job(s) saved.")
    else:
        new = {k: v for k, v in jobs.items() if k not in seen}
        for jid, j in new.items():
            link = f"{BASE}/app#/jobDetail?jobId={jid}&locale=en-CA"
            pay = f" - ${j['totalPayRateMax']}/h" if j.get("totalPayRateMax") else ""
            notify(topic, "New Amazon job in Calgary",
                   f"{j['jobTitle']} ({j['locationName']}){pay}", click=link)
            print("NEW JOB:", jid, j["jobTitle"])
        if not new:
            print(f"Checked: nothing new ({len(jobs)} listed).")

    updated = seen | set(jobs)
    if first_run or updated != seen:
        SEEN_FILE.write_text(json.dumps(sorted(updated)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
