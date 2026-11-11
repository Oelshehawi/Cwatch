# Court Watch

Watches court availability at Tennis BC Hub @ Richmond (ClubSpark) every ~10 minutes,
shows it on a clean web page, and pushes a phone alert when a 60+ minute slot opens up
(a cancellation, or a new day released for booking). Booking itself still happens on ClubSpark.

- `court_watch.py`: fetches the public availability feed, writes `docs/data.json`, sends alerts
- `docs/index.html`: the viewer page (served by GitHub Pages)
- `config.json`: what counts as worth an alert
- `.github/workflows/watch.yml`: runs the check every 10 minutes on GitHub, free

## One-time setup

1. **Phone alerts:** install the **ntfy** app (iOS/Android), tap **+**, and subscribe to a long,
   unguessable topic name, e.g. `courts-7fq2k9xw3m`. Anyone who knows the name can read your alerts,
   so treat it like a password.
2. **Repo:** push this folder to a new GitHub repository. It needs to be **public** for the
   free GitHub Pages viewer. Court availability is already public, and your topic stays secret.
3. **Secret:** repo → Settings → Secrets and variables → Actions → New repository secret:
   `NTFY_TOPIC` = your topic name.
4. **Viewer:** repo → Settings → Pages → Source "Deploy from a branch" → `main` / `/docs`.
   Your page will be at `https://<your-username>.github.io/<repo-name>/`.
5. **First run:** repo → Actions → Court Watch → **Run workflow**. The first run only records
   the current state (no alert). After that it runs every ~10 minutes by itself.

## Tuning alerts (`config.json`)

| Setting | Meaning |
|---|---|
| `min_minutes` | Shortest free window worth an alert (default 60) |
| `alert_weekdays` | e.g. `["Mon","Tue","Wed","Thu"]` for weeknights only |
| `alert_earliest_start` / `alert_latest_end` | e.g. `"17:00"` / `"22:00"` for evenings only |
| `alert_courts` | e.g. `["Bubble Court 1"]`; empty = all courts |
| `advance_days` / `release_time` | Members' booking window: today + 4 days, new day opens 07:30 |

Edit the file on GitHub (pencil icon) and commit; the next run uses it.

## Notes

- `advance_days`/`release_time` come from the venue's settings (new day opens at 07:30) and its
  "book up to 96 hours ahead" rule. If alerts show days you can't book yet, adjust these.
- GitHub pauses scheduled workflows in a repo with no activity for 60 days. The bot's
  availability commits count as activity, so this should keep itself alive. If it ever
  pauses, GitHub emails you and one click re-enables it.
- Run locally: `python court_watch.py` (add `DRY_RUN=1` to print alerts instead of sending).
