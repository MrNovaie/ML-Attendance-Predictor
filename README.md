# Attendance Predictor (v0.4)

A small full-stack app that tracks weekday attendance and predicts it from historical records. Each person gets a **Categorical Naive Bayes** model trained on past **day-of-week → status** patterns. The UI shows predictions, confidence scores, and optional holdout accuracy metrics. A **Today's Attendance** page lets admins drag names into In / Partial Day / Out, and lets each user set their own status each morning before a daily cutoff. Admins can also record past days, add people, link logins to people, and manage user accounts directly in the app.

**Status:** personal / learning project — not hardened for public production deployment.

## Features

- **Predictions** for a chosen future date (weekdays only), refreshed automatically after attendance is saved
- **Per-person confidence** and sample size (how many past matching weekdays that person has on record)
- **Model evaluation** (80/20 holdout accuracy vs a simple baseline)
- **Today's Attendance page** — switch to it with the button fixed at the bottom left
  - **Admins:** a drag-and-drop board (In / Partial Day / Out, plus a *Not recorded* tray) with a department dropdown and a blue **Save** button. Works with mouse, touch (press and hold briefly, then drag) and keyboard. Dragging a saved name back to *Not recorded* and saving **un-records** it.
  - **Everyone else:** their own status, three buttons to change it until the daily cutoff (08:15 UTC+8), and a head-count with no names.
  - **Tally** at the bottom right: individual statuses `In, Partial Day, Out`, with total on a second, larger line.
- **Departments** — each person belongs to one (a dropdown; blank means *In holding*)
- **Login ↔ person link** — an admin links each account to the person it belongs to, which is what lets that user set their own attendance
- **In-app attendance entry** for any past weekday — record or correct each person's status (admin)
- **Add people to track** from the UI, with a department (admin)
- **User accounts** with an admin role (register, login, session cookies)
- **User management** — list, add, and remove accounts (admin)
- **SQLite storage** — the database is the source of truth; a CSV can optionally seed it on first run

## Stack

| Layer | Tech |
|-------|------|
| Backend | Python, Flask, Flask-Login, Flask-SQLAlchemy, scikit-learn, pandas |
| Frontend | React 19, Vite, `@dnd-kit/core` (drag and drop) |
| Database | SQLite (`backend/attendance.db`, created locally) |

## Project layout

```
attendance_tracker_app/
├── backend/
│   ├── app.py           # Flask app factory + API routes + CLI commands + UTC+8 clock and self-service cutoff
│   ├── ml.py            # Training, prediction, evaluation, model cache (incl. invalidate_models)
│   ├── import_csv.py    # One-time CSV → SQLite seed
│   ├── users.py         # User, Person, AttendanceRecord models + the DEPARTMENTS list
│   ├── extensions.py    # db, login_manager (no circular imports)
│   ├── admin.py         # (not created yet) planned home for admin routes; all routes are still in app.py
│   ├── attendance.db    # generated locally
│   ├── models.joblib    # generated locally (model cache)
│   └── data/
│       └── attendance.csv   # optional seed data (gitignored — add your own)
├── frontend/
│   ├── package.json
│   └── src/
│       ├── App.jsx               # UI shell, auth, predictions, evaluation, page toggle
│       ├── DailyAttendance.jsx   # Today's Attendance: admin drag-and-drop board; hands everyone else to MyAttendance
│       ├── MyAttendance.jsx      # Non-admin page: own status + head-count
│       ├── AttendanceTally.jsx   # The bottom-right tally box
│       ├── attendanceUtils.js    # STATUSES list, date helpers, shared fetch/error helpers
│       ├── AttendanceForm.jsx    # Record attendance, add people, departments and logins (admin)
│       ├── UserManagement.jsx    # List / add / remove users (admin)
│       └── *.css
├── todo.txt
└── README.md
```

## Prerequisites

- **Python 3.11+** (developed on 3.14)
- **Node.js 18+** and npm

## Setup

### 1. Clone and open the repo

```bash
git clone <your-repo-url>
cd attendance_tracker_app
```

### 2. Backend

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
# source .venv/bin/activate

pip install flask flask-cors flask-login flask-sqlalchemy werkzeug pandas scikit-learn joblib
```

Run from the `backend` directory:

```bash
cd backend
python app.py
```

The database file lives next to `app.py` (`backend/attendance.db`), so `python app.py` and `flask --app app ...` always use the same database regardless of where it is launched from.

On start the app will:

1. Create any missing tables (`user`, `person`, `attendance_record`)
2. Rebuild `person` / `attendance_record` **if they are from an old schema** (see [Known limitations](#known-limitations) — this drops them, so keep a backup)
3. Add `person.department` (default *Holding*) if the column is missing, and fill in blanks
4. Add `user.person_id` and its unique index if the column is missing (existing logins stay unlinked until an admin links them)
5. Seed from `data/attendance.csv` **only if** the database has no people or attendance yet and the file exists. Otherwise the import is skipped.
6. Listen on **http://localhost:5000**

Generated locally (do not commit): `attendance.db`, `models.joblib`.

### 3. Create the first admin

Nobody is an admin by default, and admin accounts are what unlock attendance entry and user management. From `backend/`:

```bash
flask --app app create-admin <username>
```

A prompt for a password will appear. Other accounts can be created by an admin in the UI, or through open registration (see limitations).

### 4. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. The app calls the API at `http://localhost:5000` (see `SERVER_URL` in `frontend/src/App.jsx`).

## Configuration

These are plain constants, not environment variables. Edit the file and restart the backend (the frontend picks up changes on save).

| What | Where | Default |
|------|-------|---------|
| Department list and the blank-means default | `DEPARTMENTS`, `DEFAULT_DEPARTMENT` in `backend/users.py` | Intelligence, Finance, Human Resources, Manpower, Headquarters; default *Holding* |
| Office timezone. "Today", the future-date check and the cutoff are judged in this timezone, not the server's | `TIMEZONE_OFFSET_HOURS` in `backend/app.py` | `8` (UTC+8) |
| Self-service cutoff (24-hour `HH:MM`, local time) | `SELF_SERVICE_CUTOFF` in `backend/app.py` | `"08:15"` |
| Status names, colours and column order in the UI | `STATUSES` in `frontend/src/attendanceUtils.js` | In, Partial Day, Out |

Changing the department list does not rewrite existing people: someone whose department was removed from the list keeps it (and still shows) until an admin changes it.

## Data

### Starting with a CSV (optional)

`backend/data/attendance.csv` is **gitignored** (real names). It is used once, to seed an empty database. After that the database is the source of truth and the CSV is not read again. The file needs these columns:

- Meta: `Day`, `Month`, `Day (absolute)`, `Day of Week`
- One column per person (attendance codes: `0` = Out, `1` = In, `2` = Partial Day)
- `Day of Week`: `1` = Mon … `5` = Fri (matches the ML encoding). Weekend rows are not allowed.

Set `YEAR` in `backend/import_csv.py` and `backend/ml.py` to match the sheet. Imported people start in *Holding*; set their departments under **Record attendance → Departments and logins**.

### Starting without a CSV

Start with an empty database, then use the admin section **Record attendance → Add a person to track** to build a customised roster, and record days as they happen. A person needs some history before they get predictions (see below).

### Backing up

Because edits live only in the database, **copy `backend/attendance.db` before any rebuild or schema change.** There is no CSV export yet. Un-recording an attendance entry deletes it permanently, so this matters more now.

## First use

1. Start backend and frontend.
2. Create an admin (step 3 above) and **log in**.
3. Under **Record attendance**, add the people you want to track, each with a department (skip if seeded from a CSV).
4. Optionally record past days: pick a weekday, set each person's status (In / Out / Partial day), and save.
5. Under **Record attendance → Departments and logins**, link each user's login to their person (see below).
6. Click **Today's Attendance** (bottom left) to mark today, and **Predictions** to switch back.
7. Pick a **future weekday** at the top of the Predictions page to see predictions.

## Today's Attendance

The page always shows **today** in the office timezone (UTC+8), never a date picked by the user. On a weekend it shows `Sat 17 Oct is a Saturday. No attendance on weekends.` and nothing else.

### Admin board

- Pick a department to filter, or leave it on *All departments*.
- Drag a name between **In**, **Partial Day** and **Out**, or down into **Not recorded**. Moved names get a dashed outline until saved, and the button reads *N unsaved changes*.
- Press **Save**. Names moved to *Not recorded* are removed from the database. The page confirms, e.g. `Saved attendance for 2 people and removed 1.`, and the Predictions page refreshes.
- The tally follows the dropdown and includes unsaved moves.

### Users

- **Link first.** A login only gets a status of its own after an admin links it: **Record attendance → Departments and logins → Login linked to {name}**. One login per person and one person per login. The list only offers logins that are not linked yet (plus that person's current one).
- A new registration is an **unlinked** account. It can use the page but sees `Your account isn't linked to a person yet…` and no buttons. Linking is admin-only on purpose, so nobody can claim someone else's attendance.
- A linked user sees their name and department, **In / Partial Day / Out** buttons, and the current status.
- Changes are allowed **on weekdays until 08:15 (UTC+8)**. The window resets by itself every day, and the buttons lock on their own when it passes. After the cutoff only an admin can change an entry. Before it, a user can overwrite an entry an admin made for them.
- The tally on this page shows counts only, never names. It defaults to the user's own department, can be switched, and refreshes about every 30 seconds.

## API (summary)

All endpoints except register and login need an authenticated session. Browser requests must use **`credentials: 'include'`** (already set in the frontend).

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/register` | No | Create account (an unlinked, non-admin login) |
| POST | `/api/login` | No | Start session (cookie) |
| POST | `/api/logout` | User | End session |
| GET | `/api/me` | User | Current username and `is_admin` |
| GET | `/api/predict?date=YYYY-MM-DD` | User | Predictions for a date |
| GET | `/api/evaluate` | User | Holdout accuracy per person |
| GET | `/api/people` | User | People as `[{id, name, department}]`; admins also get `user_id` (linked login) |
| GET | `/api/departments` | User | `{departments, default}` — the allowed choices |
| GET | `/api/today` | User | `{date, weekend, timezone}` — today in the office timezone |
| GET | `/api/attendance?date=YYYY-MM-DD` | **Admin** | Recorded statuses for a date |
| PUT | `/api/attendance` | Admin | Upsert or remove statuses: `{date, records: [{person_id, status}]}` |
| GET | `/api/attendance/summary?department=` | User | Today's head-counts, no names: `{by_status, not_recorded, total}` |
| GET | `/api/me/attendance` | User | Own status for today, whether it can be edited, seconds left to the cutoff, link state |
| PUT | `/api/me/attendance` | User | Set own status for today: `{status}` |
| POST | `/api/table_persons` | Admin | Add a person: `{name, department?}` |
| PATCH | `/api/people/<id>` | Admin | Change `{department?, user_id?}` — `user_id` links a login (or `null` unlinks) |
| GET | `/api/users` | Admin | List accounts (includes `person_id`) |
| POST | `/api/users` | Admin | Create account: `{username, password, is_admin?}` |
| DELETE | `/api/users/<id>` | Admin | Remove an account (cannot remove ownself) |

**Recording rules for `PUT /api/attendance`:** weekdays only, not in the future (judged in UTC+8), each `person_id` must exist and appear once, and the request is all-or-nothing. Every record needs a `status` key: `0`, `1` or `2` (integers only), or `null` to **remove** that person's record for the day. The response is `{status, date, saved, removed}`. Removing anything clears the model cache (see below).

**Rules for `PUT /api/me/attendance`:** the person is taken from the account's admin-made link, never from the request. `status` must be `0`, `1` or `2`. It returns `403` for an unlinked account or after the cutoff, and `400` on a weekend.

The UI hides the admin sections from non-admins, but the server enforces the admin check on every admin endpoint.

## Environment variables

| Variable | Default | Notes |
|----------|---------|--------|
| `DATABASE_URL` | `sqlite:///<backend>/attendance.db` | SQLAlchemy URI |
| `SECRET_KEY` | dev placeholder | **Required** when `ENV=production` |
| `ENV` | unset | Set to `production` to enforce `SECRET_KEY` and secure cookies |

## How the ML pipeline works

1. **Seed (optional):** on first start only, a wide CSV → long rows in `attendance_record` (linked to `person`). After that, attendance comes from the app.
2. **Load:** SQL → pivot to a wide DataFrame for training (one column per person + `Day of Week`). Days with no record for a person are empty.
3. **Train:** one `CategoricalNB` model per person, using only the days that person has a record for. People with fewer than 5 records get no model yet. Models are cached in `models.joblib`.
4. **Retrain:** whenever the cache is older than the latest `updated_at` in `attendance_record` (compared in UTC), so added and edited records show up in predictions. **Deleting a record changes neither value**, so removing attendance also deletes the cache file (`invalidate_models()` in `ml.py`). The next prediction request retrains from the remaining rows.
5. **Predict:** given a calendar date, use weekday (Mon–Fri) as the only feature. People without a model are left out of the results.
6. **Evaluate:** an 80/20 holdout per person versus a majority-class baseline. People with fewer than 10 records are skipped.

Weekend dates return an empty prediction list with an explanatory message.

## Manual smoke test

There is no automated test suite yet. After a change, this is the quick check. Steps 1–2 can be done in the browser; the API calls below are for Windows PowerShell, where `curl` is an alias for `Invoke-WebRequest` (use `Invoke-RestMethod`, or `curl.exe`).

1. **Start:** backend starts without errors, `npm run build` passes.
2. **Admin board:** drag names into each column and back to *Not recorded*, Save, reload, and confirm the board matches. Check the tally against the dropdown. On a phone-sized window (touch emulation), hold a chip briefly and drag; the tally must not overlap the toggle button.
3. **Link and self-service:** link a login to a person, log in as that user, change status, and check the tally updates. To test the cutoff without waiting, temporarily set `SELF_SERVICE_CUTOFF` to `"23:59"` (open) and `"00:01"` (closed) and restart. **Set it back afterwards.**
4. **Un-record and retrain:** open Predictions so `models.joblib` exists, un-record someone, Save, and confirm the file is gone, then reappears after reopening Predictions.
5. **Permissions, from PowerShell:**

```powershell
# log in as a non-admin
$body = @{ username = "USER"; password = "PW" } | ConvertTo-Json
Invoke-RestMethod http://localhost:5000/api/login -Method Post -ContentType "application/json" -Body $body -SessionVariable s2
Invoke-RestMethod http://localhost:5000/api/me -WebSession $s2            # is_admin should be False

# expect 403
try { Invoke-RestMethod "http://localhost:5000/api/attendance?date=2026-10-15" -WebSession $s2 } catch { $_.Exception.Response.StatusCode.value__ }

# own attendance: 200 before the cutoff if linked; otherwise 403 (read the message with $_.ErrorDetails.Message)
$b = @{ status = 1 } | ConvertTo-Json
Invoke-RestMethod http://localhost:5000/api/me/attendance -Method Put -ContentType "application/json" -Body $b -WebSession $s2
```

A `401` means the session is not logged in at all (for example a failed login), not that the user lacks permission.

## Troubleshooting

- **A CORS error on `/api/predict` in the browser console** usually means the server returned a 500, and error responses can lack the CORS headers. Read the traceback in the Flask terminal for the real cause.
- **`HTTP 404` on Today's Attendance** means the running backend does not have the newer routes (`/api/today`, `/api/me/attendance`, `/api/attendance/summary`). Replace `app.py` and restart the backend; check the failing URL in the browser's Network tab.
- **`no such column: ...`** means the existing database predates a schema change. `db.create_all()` never alters existing tables — back up the file, then add the column manually or rebuild the table. (`person.department` and `user.person_id` are added automatically at startup.)
- **A blank page** in the browser is usually a JavaScript error. Check the DevTools console. Hooks (`useState` etc.) must be called inside a component.
- **An import fails with a missing `./attendanceUtils`:** the file must be named exactly `attendanceUtils.js` (lowercase `a`). Windows ignores filename case, but Linux, macOS and CI do not.
- **"Your account isn't linked to a person yet"** is expected for a new login. An admin links it under **Record attendance → Departments and logins**.
- **Two different databases**: use `flask --app app` and `python app.py` from any directory; both resolve the same file. If data is "missing", check which `attendance.db` is being referenced.

## Known limitations

- **Not production-ready:** open registration, no CSRF protection, no login rate limiting.
- **The database is the only copy of attendance.** The CSV is just a seed, and there is no DB → CSV export yet. Un-recording is a permanent delete.
- **The stale-schema rebuild drops `person` and `attendance_record`** when the attendance table is missing expected columns. Back up before upgrading an old database.
- **No Alembic migrations.** Startup adds `person.department` and `user.person_id` itself, but any other schema change needs manual SQL or a rebuild.
- **People can be added but not removed or renamed** from the UI. Deleting a login simply unlinks it; person deletion is not available.
- **Only three statuses (0 / 1 / 2).** They are fixed by a database `CHECK` constraint, so splitting Partial Day into AM and PM needs a table rebuild (see Roadmap).
- **Predictions are visible to every logged-in user.** `/api/predict`, `/api/evaluate` and `/api/people` are not admin-only, and the Predictions page lists every person with a predicted status, unlike the names-free tally on the user page.
- **Small departments:** a head-count for a department of one or two people effectively identifies them.
- **`/api/predict`'s "today or later" check uses the server's date**, not the UTC+8 office date.
- **Weekdays only:** weekends cannot be recorded or predicted.

## Roadmap (ideas)

Next:

- Split **Partial Day into AM and PM** (start at `STATUSES` in `attendanceUtils.js` and `ALLOWED_STATUSES` in `app.py`; then the `CHECK` constraint, the ML status mapping and the CSV import)
- Decide who can see Predictions (admin-only, or each user sees only their own)
- Registration with a department *request* that an admin confirms (rather than creating a person at sign-up)
- DB → CSV export, and a backup prompt before un-recording
- Close open registration (admins create accounts)
- Remove / rename people

Later:

- Flask-Migrate for schema changes
- `requirements.txt` / lockfile for Python deps
- Automated tests for import, attendance, self-service and predict (the manual smoke test above is the starting point)
- Read the CORS origin from an environment variable; tie `debug` to `ENV`
- Make `/api/predict` use the UTC+8 date
- Consistent response shapes (some include `code`, others don't); split `create_app` into blueprints
- Fix the login timing leak; write the model cache atomically
- Optional Vite proxy so API and UI share one origin in dev

## Version history

- **v0.4** — Un-record attendance (drag back to *Not Recorded*, plus forced model retrain). Self-service: users set their own status until 08:15 UTC+8, enforced by the server. Login ↔ person link, managed by admins. Tally with total at the bottom right (admin and user pages). Office clock fixed to UTC+8. `GET /api/attendance` and `PUT /api/attendance` are admin-only. New endpoints: `/api/today`, `/api/me/attendance`, `/api/attendance/summary`; `PATCH /api/people/<id>` now links logins.
- **v0.3** — Today's Attendance page with a bottom-left toggle, department dropdown, drag-and-drop columns with a *Not recorded* tray, and a blue Save button (mouse, touch, keyboard). Departments (dropdown, default *Holding*, NOT NULL). Predictions refresh after attendance is saved.
- **v0.2** — Predictions and evaluation, in-app attendance entry, add people, user accounts and user management, SQLite as the source of truth.

## License

All rights reserved by the author.

When public, GPL v3.0