# Attendance Predictor (v0.7, in progress)

A small full-stack app that tracks weekday attendance and predicts it from historical records. Each person gets a **Categorical Naive Bayes** model trained on past **day-of-week → status** patterns. Admins see predictions, confidence scores and optional holdout accuracy metrics. A **Today's Attendance** page lets admins drag names into In / Partial Day / Out, and lets each user set their own status each morning before a daily cutoff. Admins can also record past days, add people, create logins (one by one or for many people at once), reset passwords, link logins to people and download a backup, all in the app. Every admin change is recorded in a tamper-evident activity log that admins can read, filter, check and download in the app.

**Status:** personal / learning project — not hardened for public production deployment.

## Features

- **Predictions (admin only)** for a chosen future date (weekdays only), refreshed automatically after attendance is saved. Everyone's names and predicted status are visible to admins only; other users never see them (the server answers 403).
- **Department filter and name search** beside the date picker: department on the left, date, search on the right. On a phone the date and the department filter become square icon buttons next to the search bar. The same filter and search are on the Today's Attendance board.
- **Per-person confidence** and sample size (how many past matching weekdays that person has on record)
- **Model evaluation** (80/20 holdout accuracy vs a simple baseline), admin only
- **Today's Attendance page** — switch to it with the button fixed at the bottom left (admins; everyone else lands on it)
  - **Admins:** a drag-and-drop board (In / Partial Day / Out, plus a *Not Recorded* tray) with a department filter, a name search and a blue **Save** button. Works with mouse, touch (press and hold briefly, then drag) and keyboard. Dragging a saved name back to *Not Recorded* and saving **un-records** it, after a warning that offers to download a backup first.
  - **Everyone else:** their own status, three buttons to change it until the daily cutoff (08:15 UTC+8), and a head-count with no names (only while the login is linked to a person).
  - **Tally** at the bottom right: individual statuses `In, Partial Day, Out`, with total on a second, larger line.
- **Departments** — each person belongs to one (a dropdown; blank means *Holding*)
- **Login ↔ person link** — an admin links each account to the person it belongs to, which is what lets that user set their own attendance
- **Accounts and passwords** (see [Accounts and passwords](#accounts-and-passwords))
  - Admins create logins one at a time (**Manage users**) or **for many people at once** (**Accounts**), linked to their person, with generated temporary passwords
  - A login with a temporary password must choose its own before it can do anything else
  - **Change password** for any user; **Reset password** for admins; a command-line reset for a forgotten admin password
  - Open sign-up is **off by default**; when switched on, it can carry an optional department request that an admin confirms
- **Backup and restore** — download everyone and all attendance as one CSV (admin), and load it back with a command
- **In-app attendance entry** for any past weekday — record or correct each person's status (admin)
- **People** — add someone with a department, rename them, remove them (they become a *former* person: their attendance is kept, they leave the board, predictions and head-counts, and their login is unlinked but kept), and restore them later (admin)
- **User management** — list, add, reset the password of, and remove accounts (admin)
- **Activity log** — every admin change (and every admin command) is recorded: who, when (UTC+8), what, on whom, and the old and new value. Append-only and tamper-evident (a hash chain keyed with a secret). Admins read it in the **Activity log** section: plain-sentence entries, newest first, filters (admin, person, action, from, to), a banner that checks the hash chain, and a CSV download of whatever the filters match. See [Activity log](#activity-log)
- **Last admin protected** — the server refuses to delete the only remaining admin account (`409`), even when two admins try to delete each other at the same moment
- **SQLite storage** — the database is the source of truth; a CSV can optionally seed it on first run
- **Automated checks** — 699 backend tests, and a GitHub Actions workflow that runs them and the frontend build on every push

## Stack

| Layer | Tech |
|-------|------|
| Backend | Python, Flask, Flask-Login, Flask-SQLAlchemy, scikit-learn, pandas |
| Frontend | React 19, Vite, `@dnd-kit/core` (drag and drop) |
| Database | SQLite (`backend/attendance.db`, created locally) |
| Tests | pytest (backend); GitHub Actions runs pytest and `npm run build` |

## Project layout (documentation folder not included) 

```
attendance_tracker_app/
├── .github/workflows/ci.yml   # CI: pytest (backend) and npm run build (frontend) on every push
├── backend/
│   ├── activity_log.py      # Activity log: table, HMAC hash chain, append_entry, verify_chain, anchor, append-only triggers, plain-sentence wording (describe), CSV export
│   ├── app.py               # Flask app factory + API routes + CLI commands + UTC+8 clock and self-service cutoff
│   ├── ml.py                # Training, prediction, evaluation, model cache (incl. invalidate_models)
│   ├── backup.py            # Backup CSV: export every person and record, restore with validation
│   ├── import_csv.py        # One-time CSV → SQLite seed
│   ├── users.py             # User, Person, AttendanceRecord models + the DEPARTMENTS list
│   ├── extensions.py        # db, login_manager (no circular imports)
│   ├── admin.py             # (not created yet) planned home for admin routes; all routes are still in app.py
│   ├── requirements.txt     # what the app needs to run
│   ├── requirements-dev.txt # requirements.txt plus pytest
│   ├── pytest.ini           # makes `pytest` find the backend modules
│   ├── tests/
│   │   ├── conftest.py              # fresh database, model cache and fixed clock for every test
│   │   ├── test_perms.py            # who may call every route (a route missing from the table fails the suite)
│   │   ├── test_auth.py             # login, logout, sign-up (closed by default), account management
│   │   ├── test_accs.py         # change / reset password, forced change, bulk logins, department requests
│   │   ├── test_attendance.py       # admin entry, un-record, model-cache clearing
│   │   ├── test_selfserve.py     # the 08:15 cutoff and the UTC+8 clock
│   │   ├── test_pplNlinks.py # people, departments, login links
│   │   ├── test_former.py    # removing, restoring and renaming people
│   │   ├── test_summary.py          # the head-count
│   │   ├── test_preds.py          # predictions and evaluation
│   │   ├── test_schema.py           # database rules and startup migrations
│   │   ├── test_lastadm.py          # the last admin cannot be deleted, including two admins deleting each other at once
│   │   ├── test_log_core.py         # the log itself: hash, chain, append-only triggers, anchor, count
│   │   ├── test_log_wording.py      # the plain sentence for every action (describe), and unknown actions
│   │   ├── test_log_writes.py       # every admin route and command writes the right log rows (and none when it fails)
│   │   ├── test_log_page.py         # GET /api/activity-log: order, filters, paging, who may call it
│   │   ├── test_log_verify.py       # GET /api/activity-log/verify: intact, edited, removed, inserted
│   │   ├── test_log_csv.py          # the log CSV: filters, spreadsheet-formula protection, encoding, who may call it
│   │   └── test_backup.py           # export / restore tests
│   ├── attendance.db        # generated locally
│   ├── models.joblib        # generated locally (model cache)
│   └── data/
│       └── sample_attendance.csv   # optional seed data
├── frontend/
│   ├── package.json
│   └── src/
│       ├── App.jsx               # UI shell, login, forced password change, Predictions page, page toggle
│       ├── FilterBar.jsx         # department filter + date + name search (Predictions page; the board has no date)
│       ├── icons.jsx             # funnel, calendar and magnifier icons used by the filter bar
│       ├── DailyAttendance.jsx   # Today's Attendance: admin drag-and-drop board; hands everyone else to MyAttendance
│       ├── MyAttendance.jsx      # Non-admin page: own status + head-count
│       ├── AttendanceTally.jsx   # The bottom-right tally box
│       ├── AttendanceUtils.js    # STATUSES list, date helpers, shared fetch/error helpers, downloadFromServer (backup and log CSV)
│       ├── AttendanceForm.jsx    # Admin section "Record attendance"
│       ├── PeopleSection.jsx     # Admin section "People": add, rename, remove, restore, departments and logins
│       ├── AccountsSection.jsx   # Admin section "Accounts": bulk logins, sign-up department requests
│       ├── BackupSection.jsx     # Admin section "Backup"
│       ├── ActivityLogSection.jsx  # Admin section "Activity log": chain banner, filters, paging, CSV download
│       ├── ActivityLogUtils.js   # Log helpers: buildLogQuery, formatLogTime, groupEntries, ACTION_LABELS
│       ├── UserManagement.jsx    # "Manage users": list / add / reset password / remove (admin)
│       ├── ChangePassword.jsx    # Change your own password (also the forced screen for temporary passwords)
│       └── *.css
├── todo.txt
└── README.md
```

## Prerequisites

- **Python 3.11+** (developed on 3.14; CI runs 3.13)
- **Node.js 20.19+ or 22.12+** and npm (what Vite 8 requires; CI runs 22)

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

cd backend
pip install -r requirements.txt        # to run the app
# pip install -r requirements-dev.txt  # instead, if you also want the tests
```

The versions are pinned in `requirements.txt`; the tests were run against exactly these.

Run from the `backend` directory:

```bash
python app.py
```

Start it with `python app.py`: the startup steps below, including the ones that update an older database, run only there.

The database file lives next to `app.py` (`backend/attendance.db`), so `python app.py` and `flask --app app ...` always use the same database regardless of where it is launched from.

On start the app will:

1. Create any missing tables (`user`, `person`, `attendance_record`, `activity_log`)
2. Rebuild `person` / `attendance_record` **if they are from an old schema** (see [Known limitations](#known-limitations) — this drops them, so keep a backup)
3. Add `person.department` (default *Holding*) if the column is missing, and fill in blanks
4. Add `person.former` if it is missing (everyone already there stays active)
5. Add `user.person_id` and its unique index if the column is missing (existing logins stay unlinked until an admin links them)
6. Add `user.must_change_password` and `user.requested_department` if they are missing (existing logins keep their passwords and are not forced to change them)
7. Make sure the `activity_log` table exists and install its two triggers, so the database itself refuses to edit or delete a log row
8. Seed from `data/attendance.csv` **only if** the database has no people or attendance yet and the file exists. Otherwise the import is skipped.
9. Listen on **http://localhost:5000**

Generated locally (do not commit): `attendance.db`, `models.joblib`.

### 3. Create the first admin

Nobody is an admin by default, and admin accounts are what unlock predictions, attendance entry and account management. From `backend/`:

```bash
flask --app app create-admin <username>
```

A prompt for a password will appear. Other accounts are created by an admin in the UI (see [Accounts and passwords](#accounts-and-passwords)). Open sign-up is off unless you switch it on.

### 4. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. The app calls the API at `http://localhost:5000` (see `SERVER_URL` in `frontend/src/App.jsx`).

## Configuration

These are plain constants, not environment variables (the environment variables are listed [below](#environment-variables)). Edit the file and restart the backend (the frontend picks up changes on save).

| What | Where | Default |
|------|-------|---------|
| Department list and the blank-means default | `DEPARTMENTS`, `DEFAULT_DEPARTMENT` in `backend/users.py` | Intelligence, Finance, Human Resources, Manpower, Headquarters; default *Holding* |
| Office timezone. "Today", the future-date check and the cutoff are judged in this timezone, not the server's | `TIMEZONE_OFFSET_HOURS` in `backend/app.py` | `8` (UTC+8) |
| Self-service cutoff (24-hour `HH:MM`, local time) | `SELF_SERVICE_CUTOFF` in `backend/app.py` | `"08:15"` |
| Status names, colours and column order in the UI | `STATUSES` in `frontend/src/AttendanceUtils.js` | In, Partial Day, Out |
| Status codes and the words the activity log uses for them (the codes must match the database `CHECK` and the frontend `STATUSES`) | `STATUSES` in `backend/app.py` | `0` Out, `1` In, `2` Partial Day |
| Where the browser may call the API from (CORS). Change it if the frontend runs on another port or host | `origins=[...]` in `create_app`, `backend/app.py` | `http://localhost:5173` |
| Where the frontend finds the API | `SERVER_URL` in `frontend/src/App.jsx` | `http://localhost:5000` |
| How many log rows one page of the Activity log shows (the server allows up to 500 per request) | `PAGE_SIZE` in `frontend/src/ActivityLogSection.jsx` | 50 |
| Password length a user may choose | `PASSWORD_MIN`, `PASSWORD_MAX` in `backend/app.py` | 8 to 128 characters |
| Length of generated temporary passwords, and how many logins one bulk request may create | `TEMPORARY_PASSWORD_LENGTH`, `BULK_LOGIN_LIMIT` in `backend/app.py` | 12 characters, 200 logins |
| Minimum history before a person gets a model / before they are evaluated | `ml.py` | 5 records / 10 records |
| Year of the seed CSV | `YEAR` in `backend/import_csv.py` and `backend/ml.py` | `2026` |

Changing the department list does not rewrite existing people: someone whose department was removed from the list keeps it (and still shows) until an admin changes it.

## Data

### Starting with a CSV (optional)

`backend/data/attendance.csv` is **gitignored** (real names). It is used once, to seed an empty database. After that the database is the source of truth and the CSV is not read again. The file needs these columns:

- Meta: `Day`, `Month`, `Day (absolute)`, `Day of Week`
- One column per person (attendance codes: `0` = Out, `1` = In, `2` = Partial Day)
- `Day of Week`: `1` = Mon … `5` = Fri (matches the ML encoding). Weekend rows are not allowed.

Set `YEAR` in `backend/import_csv.py` and `backend/ml.py` to match the sheet. Imported people start in *Holding*; set their departments under **People → Departments and logins**.

This seed format is **not** the backup format: it covers a single year, has no departments, and needs every cell filled in. Use the backup CSV below for backups.

### Starting without a CSV

Start with an empty database, then use the admin section **People → Add a person** to build the roster, and record days as they happen. A person needs some history before they get predictions (see below).

### Backup and restore

The database is the only copy of attendance, and un-recording deletes permanently, so keep backups.

**Make a backup** (either way gives the same file, named `attendance-backup-YYYY-MM-DD.csv`):

- In the app (admin): **Backup → Download backup (CSV)**. The un-record warning also has a **Download backup first** button.
- From `backend/`: `flask --app app export-backup backup.csv`

**File format:** one header row `person,department,date,status,former`, then one row per record, e.g. `Ana,Finance,2026-10-12,1,0`. Dates are ISO, so history can span any number of years. Status is `0` Out, `1` In, `2` Partial Day. `former` is `1` for a person an admin has removed, otherwise `0`; the column is optional when loading, so a backup made before it existed restores everyone as active. A person with no records gets one row with an empty date and status, so the roster is kept. The file is UTF-8 with a byte-order mark so Excel opens it correctly. **Logins and their links are not included** (no password hashes in a spreadsheet); after a restore, create the logins again (**Accounts → Create logins for people** links each one to its person) or link existing ones under **People**.

**Restore** (from `backend/`):

```bash
# into an empty database
flask --app app import-backup backup.csv

# replacing everything currently in the database (asks first; add --yes to skip the question)
flask --app app import-backup backup.csv --replace
```

The whole file is checked before anything is written, so a bad file changes nothing. Every problem is listed with its line number (wrong date format, weekend date, status not 0/1/2, duplicate record, a person with two departments, and so on). Without `--replace` the command refuses a database that already has people or attendance. `--replace` unlinks every login from its person. After a restore the model cache is cleared, so predictions retrain from the restored data.

Before a schema change or a `--replace`, `backend/attendance.db` can also be copied.

**Backups contain real names, so keep them out of git.** The downloaded file goes wherever the browser saves it, but `export-backup backup.csv` run inside the repo does not. Add `attendance-backup-*.csv`, `backup*.csv` and `activity-log-*.csv` to `.gitignore`.

## First use

1. Start backend and frontend.
2. Create an admin (step 3 above) and **log in**.
3. Under **People → Add a person**, add the people that need to be tracked, each with a department (skip if seeded from a CSV).
4. Optionally, under **Record attendance**, record past days: pick a weekday, set each person's status (In / Out / Partial day), and save.
5. Under **Accounts → Create logins for people**, tick the people who should have a login and create them. Download the CSV of usernames and temporary passwords and hand them out. (Or add logins one at a time under **Manage users**, and link them under **People → Departments and logins**.)
6. Click **Today's Attendance** (bottom left) to mark today, and **Predictions** to switch back.
7. Pick a **future weekday** at the top of the Predictions page to see predictions.

## Today's Attendance

The page always shows **today** in the office timezone (UTC+8), never a date picked by the user. On a weekend it shows `Sat 17 Oct is a Saturday. No attendance on weekends.` and nothing else.

### Admin board

- Pick a department to filter, or leave it on *All departments*. Type in the search box to narrow the names further; searching only changes what is shown, never what **Save** sends.
- Drag a name between **In**, **Partial Day** and **Out**, or down into **Not Recorded**. Moved names get a dashed outline until saved, and the button reads *N unsaved changes*.
- Press **Save**. Moving names between columns saves straight away. If the Save would **delete** records (names dragged back to *Not Recorded*), a red warning lists them first and offers **Download backup first**, **Cancel** (the default; Escape also cancels) and **Remove and save**. Nothing is deleted until confirmed.
- The page then confirms, e.g. `Saved attendance for 2 people and removed 1.`, and the Predictions page refreshes.
- The tally follows the department filter (not the search) and includes unsaved moves.

### Users

- **Link first.** A login only gets a status of its own after an admin links it: **People → Departments and logins → Login linked to {name}**. One login per person and one person per login. The list only offers logins that are not linked yet (plus that person's current one). Logins made with **Accounts → Create logins for people** are linked as they are created.
- An account that is not linked (one added under **Manage users**, a sign-up, or the login of someone who was removed) can use the page but sees `Your account isn't linked to a person…`, no buttons and **no head-count**: the server answers `403` to the head-count for a login with no person behind it. Linking is admin-only on purpose, so nobody can claim someone else's attendance.
- A linked user sees their name and department, **In / Partial Day / Out** buttons, and the current status.
- Changes are allowed **on weekdays until 08:15 (UTC+8)**. The window resets by itself every day, and the buttons lock on their own when it passes. After the cutoff only an admin can change an entry. Before it, a user can overwrite an entry an admin made for them.
- The tally on this page (for linked users) shows counts only, never names. It defaults to the user's own department, can be switched, and refreshes about every 30 seconds.

## Accounts and passwords

### Who can have a login

Only admins create logins, unless sign-up is switched on (see below).

- **Accounts → Create logins for people** (bulk): tick the people who have no login yet (a search box helps with 50 names) and press **Create N logins**. Each login gets a username made from the person's name (`José Luis` becomes `jose.luis`; a clash gets a number, `jose.luis2`), a random 12-character temporary password and a link to its person. The batch is **all or nothing**: a person who already has a login stops the whole batch. The passwords are shown **once**, with a **Download CSV** button (`person,username,temporary_password`). They are not stored anywhere readable, so download or print them before pressing *Done*. Keep that file out of git (add `new-logins-*.csv` to `.gitignore`).
- **Manage users → Add a user**: one login with a password the admin types. It is not linked to a person yet.

### Temporary passwords

A login made by an admin, or whose password an admin reset, has a **temporary password** (the user list shows *temporary password* next to it). The user can log in with it but must choose their own password straight away. Until they do, the **server** refuses everything else from that login with `403` and `password_change_required: true`; the only calls it may make are `/api/login`, `/api/me`, `/api/logout` and `/api/me/password`. An admin can create a login without this rule through the API (`must_change_password: false`).

### Changing and resetting passwords

- **Any user:** **Change password** in the top bar (current password required; 8 to 128 characters; it must differ from the current one). Every *other* browser logged in as that user is logged out; this one stays in.
- **Admin, for someone else:** **Manage users → Reset password** on that login. Leave the box empty to generate a password, or type a temporary one. The user is logged out everywhere and must choose their own password at next login. The new password is shown once, with a **Copy** button. Admins cannot reset their own password this way (use *Change password*).
- **A forgotten admin password:** from `backend/`, `flask --app app set-password <username>`. It asks for the new password and does not force a change.

### Open sign-up (off by default)

`/api/register` answers `403` and the login screen shows no *Register* button, unless the backend is started with `ALLOW_REGISTRATION=1` (or `true` / `yes`). When it is on, the form can carry an optional **department request**. A sign-up never creates a person. It creates an unlinked, non-admin login, with the requested department stored beside it. Under **Accounts → Sign-up requests** (the *Accounts* heading shows a count) an admin can **Confirm** (the person linked to that login moves to that department; link the login first) or **Dismiss**.

## Activity log

Every admin change is written to the `activity_log` table **in the same transaction as the change**, so the change and its row are saved together or not at all. Each row holds:

- **who:** the admin's id and name, or `system` for a command run on the server
- **when:** the server clock in UTC+8, e.g. `2026-10-14T11:00:00+08:00`
- **what:** an action name
- **on whom:** the person and/or login, as ids and as the names they had at the time
- **old and new value**

Names are copies, so a row still reads correctly after that admin's login or that person is gone.

| Action | Old → new |
|--------|-----------|
| `login_created` (once per login in a bulk creation, and from `create-admin`) | → `user` or `admin` |
| `login_deleted` | `user` or `admin` → |
| `password_reset` (also from `set-password`) | never the password |
| `department_request_confirmed` / `department_request_dismissed` | old department → new / requested department → |
| `person_added` | → department |
| `department_changed`, `person_renamed` | old → new |
| `person_removed` (names the login it unlinked), `person_restored` | |
| `login_linked` / `login_unlinked` | person it was linked to before → person / person → |
| `attendance_set` / `attendance_removed` | `2026-10-13 Out` → `2026-10-13 In` / `2026-10-13 In` → |
| `backup_restored` (from `import-backup`) | `replaced existing data` → `N people, M attendance records` |

Saving something that did not change writes nothing, and neither does a request that fails or is refused. **Not logged:** a user setting their own attendance or changing their own password, open sign-up, logging in and out, and anything that only reads (downloading a backup or the log included). `tests/test_log_writes.py` lists every route and command that writes, and fails if a new one is neither logged nor given a reason. Each row records one fact; the screen groups neighbouring rows by the same admin, the same action and the same second (a bulk save of ten people shows as one entry) only when it displays them.

**The Activity log section (admins only)** sits with the other admin sections. It shows:

- **A chain banner** at the top: *Log intact: all N entries match their hashes*, or a red *The log has been altered* with the first entry that does not match. While no copy of the newest hash is kept off the server, the intact banner adds that entries removed from the very end cannot be detected yet.
- **Entries, newest first**, as plain sentences, 50 at a time, with **Load older entries** for more.
- **Filters:** admin (the name the admin had when the row was written), person (contains, case-insensitive), action, and *From* / *To* (inclusive days). **Search** applies them, **Clear** removes them.
- **Refresh** and **Download CSV**. The CSV holds everything the filters match, not only the page on screen, oldest first, with the columns `seq, logged_at, admin, action, text, person, login, old, new, row_hash`. It opens correctly in Excel (UTF-8 with a byte-order mark), and a cell that starts with `=`, `+`, `-`, `@`, a tab or a carriage return gets a `'` in front so a spreadsheet cannot run it as a formula.

**Tamper evidence:**
- **The chain:** each row stores the previous row's hash and its own. Its own hash is an HMAC-SHA256 over every other column, keyed with `ACTIVITY_LOG_KEY`. Editing any column, or deleting or inserting a row, breaks the chain, and without the key nobody can rebuild it.
- **The triggers:** two database triggers refuse `UPDATE` and `DELETE` on the table. They stop admins and bugs in the app.
- **What it doesn't stop:** someone with root on the server can read the key and rewrite everything.
- **Cut-off rows at the end:** removing the newest rows leaves a valid, shorter chain. `activity_log.anchor()` returns the newest `(seq, hash)` for a copy kept off the server, and `verify_chain(anchor=...)` checks against it. Saving that copy with the scheduled backup is Roadmap item 6.

**Reading and checking it from the server** (without the screen), from `backend/`:

```bash
flask --app app shell
```

```python
from activity_log import ActivityLog, verify_chain
print(verify_chain())   # None = intact; a number = the first row that is wrong
print(*[(r.seq, r.logged_at, r.admin_name, r.action, r.person_name, r.login_name, r.old_value, r.new_value) for r in ActivityLog.query.order_by(ActivityLog.seq.desc()).limit(20)], sep="\n")
```

**Keep `ACTIVITY_LOG_KEY` the same for the life of the database.** Rows are checked with the current key, so changing it makes every older row fail verification.

## API (summary)

All endpoints except register, login and auth options need an authenticated session. Browser requests must use **`credentials: 'include'`** (already set in the frontend). A login that still has a temporary password may only use the endpoints marked † (everything else answers `403`).

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/register` | No | Create an unlinked, non-admin account: `{username, password, confirmPassword, department?}`. **`403` unless `ALLOW_REGISTRATION` is on.** `department` is a request an admin confirms |
| GET | `/api/auth/options` | No | `{registration_open, departments, default_department}` for the login screen (`departments` is empty while sign-up is closed) |
| POST | `/api/login` | No | Start session (cookie) † |
| POST | `/api/logout` | User | End session † |
| GET | `/api/me` | User | Current `username`, `is_admin` and `must_change_password` † |
| POST | `/api/me/password` | User | Change own password: `{current_password, new_password}`. Ends the user's other sessions † |
| GET | `/api/predict?date=YYYY-MM-DD` | **Admin** | Predictions for a date |
| GET | `/api/evaluate` | **Admin** | Holdout accuracy per person |
| GET | `/api/people` | **Admin** | People as `[{id, name, department, former, user_id}]` (`user_id` is the linked login). Former people are left out unless `?include_former=1` |
| GET | `/api/departments` | User | `{departments, default}` — the allowed choices |
| GET | `/api/today` | User | `{date, weekend, timezone}` — today in the office timezone |
| GET | `/api/attendance?date=YYYY-MM-DD` | Admin | Recorded statuses for a date |
| PUT | `/api/attendance` | Admin | Upsert or remove statuses: `{date, records: [{person_id, status}]}` |
| GET | `/api/attendance/summary?department=` | Admin or linked login | Today's head-counts, no names, former people left out: `{by_status, not_recorded, total}`. `403` for a login with no person |
| GET | `/api/me/attendance` | User | Own status for today, whether it can be edited, seconds left to the cutoff, link state |
| PUT | `/api/me/attendance` | User | Set own status for today: `{status}` |
| GET | `/api/export/attendance.csv` | Admin | The backup file (people, departments, all records); sent as a download, never cached |
| GET | `/api/activity-log` | Admin | Log entries, newest first: `{entries: [{seq, logged_at, admin, action, text, person, login, old, new}], more}`. Optional `admin`, `person` (contains), `action`, `from`, `to` (`YYYY-MM-DD`, inclusive; a bad date answers `400`), `limit` (1 to 500, default 100) and `before=<seq>` for the next page |
| GET | `/api/activity-log/verify` | Admin | Checks the whole hash chain: `{ok, first_bad_seq, rows, tail_checked}`. `tail_checked` stays `false` until a copy of the newest hash is kept off the server |
| GET | `/api/activity-log.csv` | Admin | The log as a download (`activity-log-YYYY-MM-DD.csv`), with the same filters as the list, no paging, oldest first, never cached |
| POST | `/api/table_persons` | Admin | Add a person: `{name, department?}`. A name held by a former person answers `409` (restore them instead) |
| PATCH | `/api/people/<id>` | Admin | Change `{name?, department?, user_id?, former?}`. `user_id` links a login (or `null` unlinks); `former: true` removes the person (their login is unlinked and kept, `unlinked_username` says which), `former: false` restores them. A former person cannot be linked. All or nothing |
| GET | `/api/users` | Admin | List accounts (`person_id`, `must_change_password`, `requested_department`) |
| POST | `/api/users` | Admin | Create an account: `{username, password, is_admin?, must_change_password?}` (the last defaults to `true`) |
| POST | `/api/users/bulk` | Admin | One linked login per person: `{person_ids}` (up to 200). Returns `{created: [{person_id, person, username, temporary_password}]}` once. All or nothing |
| POST | `/api/users/<id>/password` | Admin | Temporary password: `{password?}` (blank generates one). Returns it once; the user must change it and is logged out everywhere. Not for your own account |
| POST | `/api/users/<id>/department-request` | Admin | Answer a sign-up department request: `{action: "confirm" \| "dismiss"}` |
| DELETE | `/api/users/<id>` | Admin | Remove an account. Not your own (`400`), and not the last admin (`409`, "At least one admin is required for app operation.") |

**Recording rules for `PUT /api/attendance`:** weekdays only, not in the future (judged in UTC+8), each `person_id` must exist, belong to a current (not former) person and appear once, and the request is all-or-nothing. Every record needs a `status` key: `0`, `1` or `2` (integers only), or `null` to **remove** that person's record for the day. The response is `{status, date, saved, removed}`. Removing anything clears the model cache (see below).

**Rules for `PUT /api/me/attendance`:** the person is taken from the account's admin-made link, never from the request. `status` must be `0`, `1` or `2`. It returns `403` for an unlinked account or after the cutoff, and `400` on a weekend.

The UI hides the admin sections and the Predictions page from non-admins, but the server enforces the admin check on every admin endpoint (`tests/test_perms.py` lists every route and fails if one is missing). Every admin endpoint that changes data also writes to the [activity log](#activity-log), in the same transaction.

## Environment variables

| Variable | Default | Notes |
|----------|---------|--------|
| `DATABASE_URL` | `sqlite:///<backend>/attendance.db` | SQLAlchemy URI |
| `SECRET_KEY` | dev placeholder | **Required** when `ENV=production` |
| `ENV` | unset | Set to `production` to enforce `SECRET_KEY`, `ACTIVITY_LOG_KEY` and secure cookies |
| `ALLOW_REGISTRATION` | unset (sign-up closed) | `1`, `true` or `yes` opens `/api/register` and shows *Register* on the login screen |
| `ACTIVITY_LOG_KEY` | dev placeholder (a warning is logged) | The secret that keys the activity log's hashes. **Required** when `ENV=production`: without it every admin change fails. Keep it out of git, and never change it for an existing database |

## How the ML pipeline works

1. **Seed (optional):** on first start only, a wide CSV → long rows in `attendance_record` (linked to `person`). After that, attendance comes from the app.
2. **Load:** SQL → pivot to a wide DataFrame for training (one column per person + `Day of Week`). Days with no record for a person are empty.
3. **Train:** one `CategoricalNB` model per person, using only the days that person has a record for. People with fewer than 5 records get no model yet. Models are cached in `models.joblib`.
4. **Retrain:** whenever the cache is older than the latest `updated_at` in `attendance_record` (compared in UTC), so added and edited records show up in predictions. **Deleting a record changes neither value**, so removing attendance (and restoring a backup) also deletes the cache file (`invalidate_models()` in `ml.py`). The next prediction request retrains from the rows that are left.
5. **Predict:** given a calendar date, use weekday (Mon–Fri) as the only feature. People without a model are left out of the results.
6. **Evaluate:** an 80/20 holdout per person versus a majority-class baseline. People with fewer than 10 records are skipped.

Weekend dates return an empty prediction list with an explanatory message.

## Tests

Backend tests use pytest. From `backend/`:

```bash
pip install -r requirements-dev.txt
pytest
```

Each test gets its own empty SQLite file, its own model cache and a fixed clock, so it never touches `attendance.db`. 699 tests cover:
- the permission of every route (a new route that is not listed fails the suite)
- logins and accounts: sign-up closed by default, change and reset password, the forced password change on every route, bulk logins, department requests
- attendance entry and un-record, with model-cache clearing
- the 08:15 cutoff and the UTC+8 clock at their edges
- people and login links; removing, restoring and renaming people
- the tally, and predictions (admin only)
- database constraints and startup migrations
- backup and restore
- the last-admin rule, including two admins deleting each other at once
- the activity log: the exact bytes that are hashed, the chain, the append-only triggers and the anchor; the plain sentence for every action; every admin route and command writing the right rows, or none when it fails, is refused or changes nothing; and the three log routes (list and filters, chain check, CSV)

The tests were also checked by breaking the code on purpose: 21 deliberate bugs in the attendance code, 6 in the account code, 17 in the remove / rename code, 16 in the activity-log module and 34 in its wiring into the routes. Every one was caught. The last-admin rule and the three log routes were checked the same way; the few changes that cannot alter behaviour (for example a missing `rollback()`, which Flask does anyway) are not counted.

**CI:** `.github/workflows/ci.yml` runs `pytest` (Python 3.13) and `npm ci` + `npm run build` (Node 22) on every push and pull request.

The frontend has no tests yet (the pure helpers in `ActivityLogUtils.js` are written so they are easy to test); `npm run build` (from `frontend/`) is the check that it compiles. `npm run lint` still reports 4 older errors (the React rule against setting state inside an effect, in `App.jsx`, `AttendanceForm.jsx` and `UserManagement.jsx`), so CI does not run it yet.

## Manual smoke test

After a change, this is the quick check for the parts that are not under test yet. Steps 1–2 can be done in the browser; the API calls below are for Windows PowerShell, where `curl` is an alias for `Invoke-WebRequest` (use `Invoke-RestMethod`, or `curl.exe`).

1. **Start:** backend starts without errors, `npm run build` passes.
2. **Admin board:** drag names into each column and back to *Not Recorded*, Save, reload, and confirm the board matches. Un-recording must show the warning, and Cancel or Escape must leave the data untouched. Check the tally against the dropdown. On a phone-sized window (touch emulation), hold a chip briefly and drag; the tally must not overlap the toggle button.
3. **Link and self-service:** link a login to a person (or create one with **Accounts → Create logins for people**, log in with its temporary password and choose your own), log in as that user, change status, and check the tally updates. To test the cutoff without waiting, temporarily set `SELF_SERVICE_CUTOFF` to `"23:59"` (open) and `"00:01"` (closed) and restart. **Set it back afterwards.**
4. **Un-record and retrain:** open Predictions so `models.joblib` exists, un-record someone, confirm, and check the file is gone, then reappears after reopening Predictions.
5. **Activity log:** make an admin change (rename someone, say), then open the **Activity log** section. The newest entry must describe the change and the banner must say *Log intact*. Filter by that person, press **Download CSV** and check the file holds only those rows. (The `flask --app app shell` lines under [Activity log](#activity-log) do the same from the server.)
6. **Permissions, from PowerShell:**

```powershell
# log in as a non-admin
$body = @{ username = "USER"; password = "PW" } | ConvertTo-Json
Invoke-RestMethod http://localhost:5000/api/login -Method Post -ContentType "application/json" -Body $body -SessionVariable s2
Invoke-RestMethod http://localhost:5000/api/me -WebSession $s2            # is_admin should be False

# expect 403 (admin-only: attendance, predictions, people)
try { Invoke-RestMethod "http://localhost:5000/api/attendance?date=2026-10-15" -WebSession $s2 } catch { $_.Exception.Response.StatusCode.value__ }
try { Invoke-RestMethod "http://localhost:5000/api/predict?date=2030-01-07" -WebSession $s2 } catch { $_.Exception.Response.StatusCode.value__ }

# own attendance: 200 before the cutoff if linked; otherwise 403 (read the message with $_.ErrorDetails.Message)
$b = @{ status = 1 } | ConvertTo-Json
Invoke-RestMethod http://localhost:5000/api/me/attendance -Method Put -ContentType "application/json" -Body $b -WebSession $s2
```

A `401` means the session is not logged in at all (for example a failed login), not that the user lacks permission.

## Troubleshooting

- **A CORS error on `/api/predict` in the browser console** usually means the server returned a 500, and error responses can lack the CORS headers. Read the traceback in the Flask terminal for the real cause.
- **Everyone was logged out after an upgrade.** Expected once: the session cookie now also carries a fingerprint of the password (so a password change or reset ends old sessions), and cookies from before do not have it. Log in again.
- **`no such column: must_change_password`** (or `requested_department`) means the backend was started some other way than `python app.py`, so the startup step that adds the columns did not run. Start it with `python app.py` once.
- **A user is stuck on "Choose your own password".** That is the temporary-password rule. They enter the temporary password as the current one and choose a new one (8 to 128 characters). An admin can give them a new temporary password under **Manage users → Reset password**.
- **The only admin forgot their password:** `flask --app app set-password <username>` from `backend/`.
- **There is no *Register* button.** Sign-up is off by default; start the backend with `ALLOW_REGISTRATION=1` to turn it on (see [Accounts and passwords](#accounts-and-passwords)).
- **`HTTP 404` on Today's Attendance** means the running backend does not have the newer routes (`/api/today`, `/api/me/attendance`, `/api/attendance/summary`). Replace `app.py` and restart the backend; check the failing URL in the browser's Network tab.
- **`import-backup` says "The backup file has problems"** and lists lines. Fix those lines and run it again; nothing was written. The most common causes are a date that Excel reformatted (it must be `YYYY-MM-DD`) and a weekend date.
- **`import-backup` says the database already has people or attendance.** That is the safety check. Use `--replace` only if you really want to delete what is there.
- **`no such column: ...`** means the existing database predates a schema change. `db.create_all()` never alters existing tables — back up the file, then add the column manually or rebuild the table. (`person.department`, `person.former`, `user.person_id`, `user.must_change_password` and `user.requested_department` are added automatically at startup, and the `activity_log` table and its triggers are created, but only when you start with `python app.py`.)
- **`no such table: activity_log`** means the backend was started some other way than `python app.py` on a database that predates the log. Start it with `python app.py` once.
- **Every admin change answers `500` and the Flask terminal says `ACTIVITY_LOG_KEY must be set`:** `ENV=production` is set without the key. Set `ACTIVITY_LOG_KEY` to a long random string and restart (and keep it the same from then on).
- **"At least one admin is required for app operation."** (`409`) means two admins deleted each other at the same moment and yours came second: nothing was removed. It cannot happen one request at a time, because you cannot delete your own account. The tests cover it, so the smoke test does not.
- **The Activity log banner says "Could not check the log" or the list will not load.** Check the Flask terminal. A `403` means the login is not an admin; a `500` is usually `ACTIVITY_LOG_KEY` missing under `ENV=production`.
- **The Activity log banner says "The log has been altered".** A row was edited, removed or inserted, or `ACTIVITY_LOG_KEY` is not the key the rows were written with. If you changed the key, put the old one back; do not rebuild the log.
- **`ModuleNotFoundError: No module named 'activity_log'`:** `backend/activity_log.py` is missing.
- **CI fails with "Some specified paths were not resolved, unable to cache dependencies".** `frontend/package-lock.json` is missing from the commit (it was deleted or never committed). Run `npm install` in `frontend/` and commit the new lockfile; `npm ci` in CI needs it.
- **A blank page** in the browser is usually a JavaScript error. Check the DevTools console. Hooks (`useState` etc.) must be called inside a component.
- **An import fails with a missing `./AttendanceUtils`:** the file must be named exactly `AttendanceUtils.js` (capital `A`) and every import must spell it the same way. Windows ignores filename case, but Linux, macOS and CI do not. If git still tracks the old spelling after a rename on Windows, rename it with `git mv` through a temporary name.
- **"Your account isn't linked to a person yet"** is expected for a new login. An admin links it under **People → Departments and logins**.
- **Login works but every request is refused with a CORS error, or the cookie is never set** when the frontend runs on a port other than 5173 (Vite picks the next free one if 5173 is taken). The allowed origin is fixed in `app.py` (see Configuration); change it to match the address in the browser bar and restart the backend.
- **Two different databases**: use `flask --app app` and `python app.py` from any directory; both resolve the same file. If data is "missing", check which `attendance.db` is being referenced.

## Known limitations

- **Not production-ready:** no CSRF protection, no login rate limiting or lockout, no 2FA, and `python app.py` runs Flask's development server with `debug=True`. Open sign-up is off by default.
- **The board is not kept fresh.** With several admins the last save of a day still silently wins, and the board does not refresh or warn when another admin saves (Roadmap item 4).
- **The log's limits:**
  - The newest hash is not copied off the server yet, so rows cut off the end of the log go unnoticed.
  - The `import-backup` row is written just after the restore commits, so a crash between the two leaves the restore unlogged.
  - Root on the server can read `ACTIVITY_LOG_KEY` and rewrite the log.
  - Without the key in production the app still starts, but every admin change fails.
  - The *From* / *To* filters compare the stored time as text, which is correct while every row carries the same `+08:00` offset. If `TIMEZONE_OFFSET_HOURS` ever becomes configurable and is changed on a database that already has rows, the filters will be off by the difference.
  - The CSV of the **log** protects against spreadsheet formulas; the **backup** CSV cannot (it must restore exactly what it holds), so open a backup in a spreadsheet only if you trust every name in it.
- **The last-admin rule only counts accounts.** There is no way to demote an admin, so deleting is the only way to lose one. `flask --app app create-admin` and `set-password` remain the way back in if everyone is locked out.
- **Un-recording is a permanent delete.** The app warns first and can download a backup, but nothing is backed up automatically. Backups do not include logins or their person links.
- **The stale-schema rebuild drops `person` and `attendance_record`** when the attendance table is missing expected columns. Back up before upgrading an old database.
- **No Alembic migrations.** Startup adds the columns listed under *Setup* itself, but any other schema change needs manual SQL or a rebuild.
- **People are never deleted.** *Remove* makes someone a former person (kept in the database, the backup and the Former people list). Their name stays reserved, so a new person cannot reuse it; restore them or rename them first. Deleting a login simply unlinks it.
- **Passwords are reset by an admin only** (there is no email or self-service "forgot password"). Temporary passwords are shown once; if one is lost, reset it again.
- **Only three statuses (0 / 1 / 2).** They are fixed by a database `CHECK` constraint, so splitting Partial Day into AM and PM needs a table rebuild (see Roadmap).
- **Small departments:** a head-count for a department of one or two people effectively identifies them.
- **`/api/predict`'s "today or later" check uses the server's date**, not the UTC+8 office date.
- **Login timing leak:** `/api/login` answers faster for a username that does not exist, so usernames can be guessed from response time.
- **Weekdays only:** weekends cannot be recorded or predicted.

## Roadmap

The plan is the TO DO list in `todo.txt`. When all eight items are done, the project is finished. Done so far: 1 (CI), 2 (admin-only Predictions, search and department filter), 3 (account management, remove / rename / restore people) and most of 4 (the activity log, its screen, and last-admin protection). Left: the stale-board warning in 4, then 5 to 8.

- **4 — Multi-admin safety and the activity log.** *Done:* every admin change recorded and attributed (who, when in UTC+8, what, on whom, old and new value), append-only and tamper-evident; the log read, filtered, chain-checked and downloaded in the app; the last admin cannot be deleted (two admins deleting each other at once included). *Left:* refresh or warn when the board is stale.
- **5 — Split Partial Day into AM and PM**, before real data exists (start at `STATUSES` in `AttendanceUtils.js` and `STATUSES` in `app.py`; then the `CHECK` constraint, the ML status mapping, the backup restore and the CSV import).
- **6 — Deployment prep.** `.env` settings (CORS origin, timezone offset, cutoff, departments), gunicorn, a Dockerfile, HTTPS, SQLite WAL mode, a scheduled backup that includes the log and an off-server copy of its newest hash (`anchor()`), which is what lets the chain check notice rows cut off the end; refusing to start in production without `ACTIVITY_LOG_KEY`; CSRF protection and login rate limiting (droppable only on a private network or VPN); an atomic model-cache write; optionally 2FA for creating admin accounts.
- **7 — Frontend tests** (Vitest component tests, a few Playwright browser flows), added to CI.
- **8 — Housekeeping.** The licence (the README currently says both "All rights reserved" and "GPL v3.0 when public"), a README screenshot, `.gitignore` additions, small naming tidy-ups.

Later, if ever: Flask-Migrate for schema changes; a Vite proxy so API and UI share one origin in development; consistent response shapes and `create_app` split into blueprints; making `/api/predict` use the UTC+8 date.

## Version history
- **v0.7** (in progress) — Done: last-admin protection (`409`, decided while holding SQLite's write lock so two admins cannot delete each other); the Activity log section (plain-sentence entries, filters, paging, chain check, CSV download); `GET /api/activity-log`, `/api/activity-log/verify` and `/api/activity-log.csv`; one log row per fact, grouped only on screen; backend tests grew from 569 to 699 and the log tests were split into six files. Left: the stale-board warning.
- **v0.6** — CI (GitHub Actions: pytest and the frontend build) and `requirements.txt`. Predictions, accuracy and the people list are admin-only; department filter and name search beside the date picker (icon buttons on phones) and on the board. Account management: sign-up off by default (optional department request an admin confirms), bulk creation of linked logins with one-time temporary passwords and a CSV, forced password change for temporary passwords (enforced by the server), change password, admin reset password, `set-password` command, sessions ended by a password change. Remove, rename and restore people: a removed person becomes *former* (history kept, login unlinked but kept, left out of the board, predictions, head-counts and new logins) and can be restored from the Former people list; the head-count is for admins and linked logins only; the backup carries a `former` column. Activity log: every admin change and admin command is recorded (who, when in UTC+8, what, on whom, old and new value) in the same transaction as the change, with an HMAC hash chain, triggers that make the table append-only, `verify_chain` and `anchor`; status codes and their names now live in one `STATUSES` table in `app.py`. Backend tests grew from 31 to 569. New endpoints: `/api/auth/options`, `/api/me/password`, `/api/users/bulk`, `/api/users/<id>/password`, `/api/users/<id>/department-request`; `PATCH /api/people/<id>` now also takes `name` and `former`.
- **v0.5** — Backup and restore: admin download of everyone and all attendance as one CSV, export-backup / import-backup commands with whole-file validation. A warning (with Download backup first) before Save deletes records. The admin screen split into separate Record attendance, People and Backup sections. The backend test suite (pytest).
- **v0.4** — Un-record attendance (drag back to *Not Recorded*, plus forced model retrain). Self-service: users set their own status until 08:15 UTC+8, enforced by the server. Login ↔ person link, managed by admins. Tally with total at the bottom right (admin and user pages). Office clock fixed to UTC+8. `GET /api/attendance` and `PUT /api/attendance` are admin-only. New endpoints: `/api/today`, `/api/me/attendance`, `/api/attendance/summary`; `PATCH /api/people/<id>` now links logins.
- **v0.3** — Today's Attendance page with a bottom-left toggle, department dropdown, drag-and-drop columns with a *Not Recorded* tray, and a blue Save button (mouse, touch, keyboard). Departments (dropdown, default *Holding*, NOT NULL). Predictions refresh after attendance is saved.
- **v0.2** — Predictions and evaluation, in-app attendance entry, add people, user accounts and user management, SQLite as the source of truth.

## License

All rights reserved by the author.

When public, GPL v3.0