# Attendance Predictor (v0.2)

A small full-stack app that tracks weekday attendance and predicts it from historical records. Each person gets a **Categorical Naive Bayes** model trained on past **day-of-week → status** patterns. The UI shows predictions, confidence scores, and optional holdout accuracy metrics. Admins can record attendance, add people, and manage user accounts directly in the app.

**Status:** personal / learning project — not hardened for public production deployment.

## Features

- **Predictions** for a chosen future date (weekdays only)
- **Per-person confidence** and sample size (how many past matching weekdays that person has on record)
- **Model evaluation** (80/20 holdout accuracy vs a simple baseline)
- **In-app attendance entry** — record or correct a weekday's status for each person (admin)
- **Add people to track** from the UI (admin)
- **User accounts** with an admin role (register, login, session cookies)
- **User management** — list, add, and remove accounts (admin)
- **SQLite storage** — the database is the source of truth; a CSV can optionally seed it on first run

## Stack

| Layer | Tech |
|-------|------|
| Backend | Python, Flask, Flask-Login, Flask-SQLAlchemy, scikit-learn, pandas |
| Frontend | React 19, Vite |
| Database | SQLite (`backend/attendance.db`, created locally) |

## Project layout

```
attendance_tracker_app/
├── backend/
│   ├── app.py           # Flask app factory + API routes + CLI commands
│   ├── ml.py            # Training, prediction, evaluation
│   ├── import_csv.py    # One-time CSV → SQLite seed
│   ├── users.py         # User, Person, AttendanceRecord models
│   ├── extensions.py    # db, login_manager (no circular imports)
│   ├── admin.py         # (placeholder) future home for admin routes
│   └── data/
│       └── attendance.csv   # optional seed data (gitignored — add your own)
├── frontend/
│   └── src/
│       ├── App.jsx              # UI shell, auth, predictions, evaluation
│       ├── AttendanceForm.jsx   # Record attendance + add people (admin)
│       ├── UserManagement.jsx   # List / add / remove users (admin)
│       └── *.css
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

The database file lives next to `app.py` (`backend/attendance.db`), so `python app.py` and `flask --app app ...` always use the same database regardless of where you launch them from.

On start the app will:

1. Create any missing tables (`user`, `person`, `attendance_record`)
2. Rebuild `person` / `attendance_record` **if they are from an old schema** (see [Known limitations](#known-limitations) — this drops them, so keep a backup)
3. Seed from `data/attendance.csv` **only if** the database has no people or attendance yet and the file exists. Otherwise the import is skipped.
4. Listen on **http://localhost:5000**

Generated locally (do not commit): `attendance.db`, `models.joblib`.

### 3. Create the first admin

Nobody is an admin by default, and admin accounts are what unlock attendance entry and user management. From `backend/`:

```bash
flask --app app create-admin <username>
```

You'll be prompted for a password. Other accounts can be created by an admin in the UI, or through open registration (see limitations).

### 4. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. The app calls the API at `http://localhost:5000` (see `SERVER_URL` in `frontend/src/App.jsx`).

## Data

### Starting with a CSV (optional)

`backend/data/attendance.csv` is **gitignored** (real names). It is used once, to seed an empty database. After that the database is the source of truth and the CSV is not read again. The file needs these columns:

- Meta: `Day`, `Month`, `Day (absolute)`, `Day of Week`
- One column per person (attendance codes: `0` = Out, `1` = In, `2` = Partial Day)
- `Day of Week`: `1` = Mon … `5` = Fri (matches the ML encoding). Weekend rows are not allowed.

Set `YEAR` in `backend/import_csv.py` and `backend/ml.py` to match your sheet.

### Starting without a CSV

Start with an empty database, then use the admin section **Record attendance → Add a person to track** to build your roster, and record days as they happen. A person needs some history before they get predictions (see below).

### Backing up

Because edits live only in the database, **copy `backend/attendance.db` before any rebuild or schema change.** There is no CSV export yet.

## First use

1. Start backend and frontend.
2. Create an admin (step 3 above) and **log in**.
3. Under **Record attendance**, add the people you want to track (skip if you seeded from a CSV).
4. Pick a weekday, set each person's status (In / Out / Partial day), and save.
5. Pick a **future weekday** at the top of the page to see predictions.

## API (summary)

All endpoints except register and login need an authenticated session. Browser requests must use **`credentials: 'include'`** (already set in the frontend).

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/register` | No | Create account |
| POST | `/api/login` | No | Start session (cookie) |
| POST | `/api/logout` | User | End session |
| GET | `/api/me` | User | Current username and `is_admin` |
| GET | `/api/predict?date=YYYY-MM-DD` | User | Predictions for a date |
| GET | `/api/evaluate` | User | Holdout accuracy per person |
| GET | `/api/people` | User | People as `[{id, name}]` |
| GET | `/api/attendance?date=YYYY-MM-DD` | User | Recorded statuses for a date |
| PUT | `/api/attendance` | Admin | Upsert statuses: `{date, records: [{person_id, status}]}` |
| POST | `/api/table_persons` | Admin | Add a person: `{name}` |
| GET | `/api/users` | Admin | List accounts |
| POST | `/api/users` | Admin | Create account: `{username, password, is_admin?}` |
| DELETE | `/api/users/<id>` | Admin | Remove an account (you cannot remove yourself) |

**Recording rules for `PUT /api/attendance`:** weekdays only, not in the future, `status` must be `0`, `1`, or `2`, each `person_id` must exist and appear once, and the request is all-or-nothing. Sending a date that already has a record for a person overwrites it.

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
4. **Retrain:** whenever the cache is older than the latest `updated_at` in `attendance_record` (compared in UTC), so edits show up in predictions.
5. **Predict:** given a calendar date, use weekday (Mon–Fri) as the only feature. People without a model are left out of the results.
6. **Evaluate:** an 80/20 holdout per person versus a majority-class baseline. People with fewer than 10 records are skipped.

Weekend dates return an empty prediction list with an explanatory message.

## Troubleshooting

- **A CORS error on `/api/predict` in the browser console** usually means the server returned a 500, and error responses can lack the CORS headers. Read the traceback in the Flask terminal for the real cause.
- **`no such column: ...`** means the existing database predates a schema change. `db.create_all()` never alters existing tables — back up the file, then add the column manually or rebuild the table.
- **A blank page** in the browser is usually a JavaScript error. Check the DevTools console. Hooks (`useState` etc.) must be called inside a component.
- **Two different databases**: use `flask --app app` and `python app.py` from any directory; both resolve the same file. If you ever see data "missing", check which `attendance.db` you are looking at.

## Known limitations

- **Not production-ready:** open registration, no CSRF protection, no login rate limiting.
- **The database is the only copy of your attendance.** The CSV is just a seed, and there is no DB → CSV export yet.
- **The stale-schema rebuild drops `person` and `attendance_record`** when the attendance table is missing expected columns. Back up before upgrading an old database.
- **No Alembic migrations** — schema changes (such as `user.is_admin`) need manual SQL or a rebuild.
- **People can be added but not removed or renamed** from the UI.
- **Predictions don't refresh after saving attendance** until the date changes or the page reloads.
- **Weekdays only:** weekends cannot be recorded or predicted.

## Roadmap (ideas)

- Daily view: pick any date and see who is in / out (weekend rules as usual)
- DB → CSV export
- Close open registration (admins create accounts)
- Remove / rename people
- Refresh predictions after saving attendance
- Flask-Migrate for schema changes
- `requirements.txt` / lockfile for Python deps
- Smoke tests for import, attendance, and predict
- Read the CORS origin from an environment variable; tie `debug` to `ENV`
- Optional Vite proxy so API and UI share one origin in dev

## License

All rights reserved by the author.

When public, GPL v3.0