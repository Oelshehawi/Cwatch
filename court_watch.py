#!/usr/bin/env python3
"""Court Watch: track free court time on a ClubSpark venue and alert on new openings.

Each run:
  1. Fetches every court's schedule for the next N days (one public request, no login).
  2. Joins back-to-back free 30-minute blocks into free windows.
  3. Writes docs/data.json, which the viewer page (docs/index.html) reads.
  4. Compares with the previous docs/data.json and sends one phone push (ntfy)
     listing any bookable window of min_minutes+ that wasn't bookable-and-free before:
     a cancellation, or a new day being released for booking.

Environment:
  NTFY_TOPIC   ntfy topic to push to (keep it secret; anyone with it can read alerts).
               Without it, alerts are printed instead of sent.
  NTFY_SERVER  defaults to https://ntfy.sh
  DRY_RUN=1    print alerts, never send.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "docs" / "data.json"
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
FREE_CATEGORY = 0  # ClubSpark marks open, bookable time as Category 0 with Capacity > 0


def t12(minutes: int) -> str:
    """12-hour clock, compact: 420 -> '7am', 1170 -> '7:30pm'."""
    h, m = divmod(minutes % (24 * 60), 60)
    suffix = "am" if h < 12 else "pm"
    h = h % 12 or 12
    return f"{h}{suffix}" if m == 0 else f"{h}:{m:02d}{suffix}"


def span12(start: int, end: int) -> str:
    return f"{t12(start)}–{t12(end)}"


def parse_hm(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "court-watch/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_schedule(cfg: dict, start: dt.date) -> dict:
    end = start + dt.timedelta(days=cfg["days_to_fetch"] - 1)
    query = urllib.parse.urlencode(
        {"resourceID": "", "startDate": start.isoformat(), "endDate": end.isoformat(), "roleId": ""}
    )
    return fetch_json(f"https://clubspark.ca/v0/VenueBooking/{cfg['venue']}/GetVenueSessions?{query}")


def free_windows(schedule: dict) -> tuple[list[str], dict[str, dict[str, list[list[int]]]]]:
    """Return (court names, {date: {court: [[start, end], ...]}}) of merged free windows."""
    courts = [r["Name"] for r in schedule["Resources"]]
    days: dict[str, dict[str, list[list[int]]]] = {}
    for res in schedule["Resources"]:
        for day in res["Days"]:
            date = day["Date"][:10]
            windows: list[list[int]] = []
            for s in sorted(day["Sessions"], key=lambda s: s["StartTime"]):
                if s["Category"] != FREE_CATEGORY or (s.get("Capacity") or 0) <= 0:
                    continue
                if windows and windows[-1][1] == s["StartTime"]:
                    windows[-1][1] = s["EndTime"]
                else:
                    windows.append([s["StartTime"], s["EndTime"]])
            days.setdefault(date, {c: [] for c in courts})[res["Name"]] = windows
    return courts, dict(sorted(days.items()))


def bookable_dates(cfg: dict, now: dt.datetime) -> list[str]:
    """Dates members can book now: today .. today+advance_days, the last one opening at release_time."""
    last = now.date() + dt.timedelta(days=cfg["advance_days"])
    if now.hour * 60 + now.minute < parse_hm(cfg["release_time"]):
        last -= dt.timedelta(days=1)
    n = (last - now.date()).days + 1
    return [(now.date() + dt.timedelta(days=i)).isoformat() for i in range(n)]


def alertable(cfg: dict, data: dict, now: dt.datetime) -> set[tuple[str, str, int, int]]:
    """(date, court, start, end) windows that are bookable, upcoming, long enough and match the filters."""
    today = now.date().isoformat()
    now_min = now.hour * 60 + now.minute
    earliest, latest = parse_hm(cfg["alert_earliest_start"]), parse_hm(cfg["alert_latest_end"])
    courts_filter = set(cfg.get("alert_courts") or [])
    out = set()
    for date in data.get("bookable_dates", []):
        weekday = WEEKDAYS[dt.date.fromisoformat(date).weekday()]
        if weekday not in cfg["alert_weekdays"]:
            continue
        for court, windows in data["days"].get(date, {}).items():
            if courts_filter and court not in courts_filter:
                continue
            for start, end in windows:
                if date == today:
                    start = max(start, -(-now_min // 30) * 30)  # drop the part already in the past
                start, end = max(start, earliest), min(end, latest)
                if end - start >= cfg["min_minutes"]:
                    out.add((date, court, start, end))
    return out


def blocks(windows: set[tuple[str, str, int, int]]) -> set[tuple[str, str, int]]:
    """Explode windows into 30-minute blocks so a window that grows also counts as new."""
    return {(d, c, t) for d, c, s, e in windows for t in range(s, e, 30)}


def booking_link(cfg: dict, date: str) -> str:
    return f"https://clubspark.ca/{cfg['venue']}/Booking/BookByDate#?date={date}&role=member"


def notify(cfg: dict, new: list[tuple[str, str, int, int]]) -> None:
    lines = []
    for date, court, start, end in new:
        d = dt.date.fromisoformat(date)
        lines.append(f"{d:%a %b} {d.day} · {court.replace('Bubble ', '')} · {span12(start, end)} ({end - start} min)")
    title = f"{len(new)} court slots opened" if len(new) > 1 else "Court slot opened"
    body = "\n".join(lines[:12]) + (f"\n+{len(lines) - 12} more" if len(lines) > 12 else "")

    # Up to 3 tap-to-book buttons, one per day, each opening ClubSpark's sheet on that day.
    actions, seen_dates = [], set()
    for date, court, start, _ in new:
        if date in seen_dates:
            continue
        seen_dates.add(date)
        d = dt.date.fromisoformat(date)
        actions.append(f"view, Book {d:%a} {t12(start)}, {booking_link(cfg, date)}")
        if len(actions) == 3:
            break
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic or os.environ.get("DRY_RUN"):
        print(f"[alert, not sent] {title}\n{body}\n  buttons: {' | '.join(a.split(', ')[1] for a in actions)}")
        return
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    req = urllib.request.Request(
        f"{server}/{topic}",
        data=body.encode("utf-8"),
        headers={
            "Title": f"{title} - {cfg['venue_name']}",
            "Click": booking_link(cfg, new[0][0]),
            "Tags": "tennis",
            "Actions": "; ".join(actions),
        },
        method="POST",
    )
    urllib.request.urlopen(req, timeout=30).read()
    print(f"[alert sent] {title}\n{body}")


def main() -> int:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    now = dt.datetime.now(ZoneInfo(cfg["timezone"]))

    courts, days = free_windows(fetch_schedule(cfg, now.date()))
    data = {
        "venue": cfg["venue"],
        "venue_name": cfg["venue_name"],
        "courts": courts,
        "bookable_dates": bookable_dates(cfg, now),
        "release_time": cfg["release_time"],
        "min_minutes": cfg["min_minutes"],
        "days": days,
    }

    previous = json.loads(DATA_FILE.read_text(encoding="utf-8")) if DATA_FILE.exists() else None
    current_alertable = alertable(cfg, data, now)

    if previous is None:
        print(f"First run: recording {len(current_alertable)} bookable {cfg['min_minutes']}+ min windows, no alert.")
    else:
        seen = blocks(alertable(cfg, previous, now))
        new = sorted(w for w in current_alertable if blocks({w}) - seen)
        if new:
            notify(cfg, new)
        else:
            print(f"No new openings ({len(current_alertable)} bookable {cfg['min_minutes']}+ min windows).")

    # Only rewrite the file when availability actually changed, so the repo isn't committed every run.
    unchanged = previous is not None and {k: v for k, v in previous.items() if k != "changed_at"} == data
    if unchanged:
        return 0
    data["changed_at"] = now.isoformat(timespec="seconds")
    DATA_FILE.parent.mkdir(exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print("docs/data.json updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
