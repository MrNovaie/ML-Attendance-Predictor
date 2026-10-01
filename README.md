# Attendance Predictor (v0.1)

A small full-stack app that predicts weekday attendance from historical records. Each person gets a **Categorical Naive Bayes** model trained on past **day-of-week → status** patterns. The UI shows predictions, confidence scores, and optional holdout accuracy metrics.

**Status:** personal / learning project — not hardened for public production deployment.

## Features

- **Predictions** for a chosen future date (weekdays only)
- **Per-person confidence** and sample size (how many past matching weekdays in the data)
- **Model evaluation** (80/20 holdout accuracy vs a simple baseline)
- **User accounts** (register, login, session cookies)
- **SQLite storage** with attendance imported from a wide CSV into normalized `person` / `attendance_record` tables
- **User accounts** with an admin role (register, login, session cookies)
- **In-app attendance entry** — record or correct a day's status for each person

## Stack

| Layer | Tech |
|-------|------|
| Backend | Python, Flask, Flask-Login, Flask-SQLAlchemy, scikit-learn, pandas |
| Frontend | React 19, Vite |
| Database | SQLite (`attendance.db`, created locally) |

## Project layout

```
attendance_tracker_app/
├── backend/
│   ├── app.py           # Flask app factory + API routes
│   ├── ml.py            # Training, prediction, evaluation
│   ├── import_csv.py    # CSV → SQLite import
│   ├── users.py         # User, Person, AttendanceRecord models
│   ├── extensions.py    # db, login_manager (no circular imports)
│   ├── admin.py         # (placeholder) future admin routes
│   └── data/
│       └── attendance.csv   # source data (gitignored — add your own)
├── frontend/
│   └── src/App.jsx      # UI + auth + predictions
└── README.md
```

## Prerequisites

- **Python 3.11+** (3.14)
- **Node.js 18+** and npm

## Setup

### 1. Clone and open the repo

```bash
git clone <your-repo-url>
cd attendance_tracker_app
```

### 2. Attendance data

`backend/data/attendance.csv` is **gitignored** (real names). Add your own file with columns:

- Meta: `Day`, `Month`, `Day (absolute)`, `Day of Week`
- One column per person (attendance codes: `0` = Out, `1` = In, `2` = Partial Day)
- `Day of Week`: `1` = Mon … `5` = Fri (matches the ML encoding)

Set `YEAR` in `backend/import_csv.py` (and `backend/ml.py` if duplicated) to match your sheet.

### 3. Backend

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
# source .venv/bin/activate

pip install flask flask-cors flask-login flask-sqlalchemy werkzeug pandas scikit-learn joblib
```

Run from the `backend` directory (SQLite path is relative to the working directory):

```bash
cd backend
python app.py
```

On first start the app will:

1. Create tables (`user`, `person`, `attendance_record`)
2. Import `data/attendance.csv` into SQLite (replaces existing attendance rows)
3. Listen on **http://localhost:5000**

Generated locally (do not commit): `attendance.db`, `models.joblib`.

### 4. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. The app calls the API at `http://localhost:5000` (see `SERVER_URL` in `frontend/src/App.jsx`).

## First use
1. Create the first admin from the `backend` directory:
    flask --app app create-admin <username>
2. Start backend and frontend.
3. **Register** a new account in the UI.
4. **Log in** — predictions and evaluation require an authenticated session.
5. Pick a **future weekday** to see predictions.

## API (summary)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/register` | No | Create account |
| POST | `/api/login` | No | Start session (cookie) |
| POST | `/api/logout` | User | End session |
| GET | `/api/me` | User | Current username |
| GET | `/api/predict?date=YYYY-MM-DD` | User | Predictions for date |
| GET | `/api/evaluate` | User | Holdout accuracy per person |
| GET | `/api/people` | User | Names from attendance data |
| PUT | `/api/attendance` | User | Upsert statuses for a date: `{date, records:[{person_id, status}]}` |
| GET | `/api/attendance?date=YYYY-MM-DD` | User | Recorded statuses for a date |
| POST | `/api/users` | Admin | Create a user (optionally `is_admin`) |
| DELETE | `/api/users/<id>` | Admin | Remove a user (cannot delete yourself) |

All authenticated requests from the browser must use **`credentials: 'include'`** (already set in the frontend).

## Environment variables

| Variable | Default | Notes |
|----------|---------|--------|
| `DATABASE_URL` | `sqlite:///attendance.db` | SQLAlchemy URI |
| `SECRET_KEY` | dev placeholder | **Required** when `ENV=production` |
| `ENV` | unset | Set to `production` to enforce `SECRET_KEY` and secure cookies |

## How the ML pipeline works

1. **Import:** wide CSV → long rows in `attendance_record` (linked to `person`).
2. **Load:** SQL → pivot to wide DataFrame for training (one column per person + `Day of Week`).
3. **Train:** one `CategoricalNB` model per person; cached in `models.joblib`.
4. **Retrain:** if the cache is older than the latest row in `attendance_record` (by `created_at`).
5. **Predict:** given a calendar date, use weekday (Mon–Fri) as the only feature.

Weekend dates return an empty prediction list with an explanatory message.

## Known limitations

- **Not production-ready:** open registration, no CSRF, no login rate limiting.
- **Startup import** runs `import_csv(replace_existing=True)` — overwrites attendance in the DB from CSV every backend start.
- **No admin UI yet** — attendance is read-only in the app; editing requires DB/CSV changes or future admin work.
- **No Alembic migrations** — schema changes may require deleting `attendance.db` locally.

## Roadmap (ideas)

- Admin role + CRUD on `attendance_record`
- Export DB → CSV after edits
- `requirements.txt` / lockfile for Python deps
- Smoke tests for import + predict
- Optional Vite proxy so API and UI share one origin in dev

## License

All rights reserved by the author.

When public, GPL v3.0