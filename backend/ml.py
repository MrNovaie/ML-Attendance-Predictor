"""
ml.py — Per-person attendance prediction with CategoricalNB.

Public API (what the rest of your app should use):

    predict_for_one(person, dow) -> dict     # JSON-friendly result for one person+weekday
    evaluate(person) -> dict                 # accuracy + baseline for one person
    train_all_models(data, name_list)        # (re)train and save the models dict
    load_models()                            # load cached models (or train + cache)
    invalidate_models()                      # drop the cache; call after deleting attendance rows or renaming / removing / restoring a person
    get_name_list(data)                      # derive name list from data columns

"""

import datetime
import logging
from pathlib import Path

import joblib
import pandas as pd
from datetime import timezone
from sklearn.naive_bayes import CategoricalNB
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
from sqlalchemy import text

from extensions import db
from users import AttendanceRecord, STATUSES
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
MODELS_PATH = BASE_DIR / "models.joblib"


DOW_COL = "Day of Week"
DOW_NAMES = {"Mon": 1, "Tue": 2, "Wed": 3, "Thu": 4, "Fri": 5}

# Inverse of CategoricalNB class labels: the status codes and their names, from users.STATUSES.
VERDICT = STATUSES

# Columns that are NOT people. Anything else is treated as a person. Must match import_csv.long_format()
META_COLS = ["Day", "Month", "Day (absolute)", "Day of Week"]

YEAR = 2026


def load_data() -> pd.DataFrame:
    # Former people (removed by an admin) keep their records but are left out of every model and prediction.
    query = text("""
    SELECT p.name AS person_name, a.date, a.day_of_week, a.status
    FROM attendance_record a
    JOIN person p ON p.id = a.person_id
    WHERE p.former = :former
    ORDER BY a.date
    """)
    long_df = pd.read_sql(query, db.engine, params={"former": False})

    wide = long_df.pivot(index="date", columns="person_name", values="status")
    wide["Day of Week"] = long_df.groupby("date")["day_of_week"].first()
    return wide.reset_index(drop=True)

def get_name_list(data: pd.DataFrame) -> list[str]:
    return [c for c in data.columns if c not in META_COLS and data[c].notna().any()]


def train_one_model(X, y) -> CategoricalNB:
    model = CategoricalNB(min_categories=6)  # 5 weekdays + 1 for unseen during inference
    model.fit(X, y)
    return model


def train_all_models(
    data: pd.DataFrame, name_list: list[str]
) -> dict[str, CategoricalNB]:
    """One model per person, trained on ALL of that person's data.

    No train/test split here — this is the model used for live predictions.
    """
    X = data[[DOW_COL]]
    models: dict[str, CategoricalNB] = {}
    for name in name_list:
        if name not in data.columns:
            logger.warning("Skipped %s: not in CSV. Add a column to train a model.", name)
            continue
        y = data[name]
        has_record = y.notna()              # True on days this person has a record
        if has_record.sum() < 5:            # too little history, so skip for now
            logger.warning("Skipped %s: not enough history yet.", name)
            continue
        models[name] = train_one_model(X[has_record], y[has_record].astype(int))
    MODELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(models, MODELS_PATH)
    return models




def load_models() -> dict[str, CategoricalNB]:
    """Return per-person models, retraining when DB is newer than the cache.

    "Newer" means a row was added or changed (max updated_at). A deleted row changes neither
    that nor the cache's mtime, so deleting code must call invalidate_models().

    Returns {} when there is no training data yet — callers fall through the
    "no model for X" branch in predict_for_one().
    """
    latest = db.session.query(db.func.max(AttendanceRecord.updated_at)).scalar()
    if latest is None:
        return {}

    latest_ts = latest.replace(tzinfo=timezone.utc).timestamp()
    try:
        cache_fresh = MODELS_PATH.stat().st_mtime > latest_ts + 1
        if cache_fresh:
            return joblib.load(MODELS_PATH)
    except FileNotFoundError:
        pass  # no cache yet, or invalidate_models() just removed it: retrain below

    data = load_data()
    return train_all_models(data, get_name_list(data))


def invalidate_models() -> None:
    """Throw away the cached models so the next load_models() retrains from the current rows.

    Needed after deleting attendance records, and after renaming, removing or restoring a person:
    load_models() only compares max(updated_at) with the cache file's mtime, and none of those
    change it, so a stale model would keep being served. Retraining happens lazily on the next
    prediction, not here.
    """
    MODELS_PATH.unlink(missing_ok=True)

def input_to_date(raw: str) -> datetime.date:
    if not raw:
        return datetime.date.today() + datetime.timedelta(days=1)
    return datetime.date.fromisoformat(raw)


def date_info_for_one(target) -> dict:
    day_short = target.strftime("%a")
    return {
        "Shortened DOW": day_short,
        "Date format": target.strftime("%b %d, %Y"),
        "Is weekday": day_short in DOW_NAMES,
    }


def predict_attendance(model, new_data):
    """Returns (verdict_str, confidence_in_winner_pct)."""
    prediction = model.predict(new_data)
    proba = model.predict_proba(new_data)[0]
    p_winner = float(max(proba)) * 100
    verdict = VERDICT.get(int(prediction[0]), "Unknown")
    return verdict, p_winner


def accuracy(y_test, y_pred) -> float:
    return accuracy_score(y_test, y_pred) * 100 # (TP +TN)/m where n is the total number of data points in y_test


def predict_for_one(
    person: str,
    dow: int,
    *,
    data: pd.DataFrame | None = None,
    models: dict[str, CategoricalNB] | None = None,
) -> dict:
    """One person, one weekday → JSON-friendly result.

    `data` and `models` are optional injection points so batch callers like
    predict_all_for_date can avoid N+1 SQL reads / repeated joblib loads.
    """
    if models is None:
        models = load_models()
    if person not in models:
        return {"error": f"No model for {person}"}

    new_data = pd.DataFrame({DOW_COL: [dow]})
    verdict, p_winner = predict_attendance(models[person], new_data)

    if data is None:
        data = load_data()
    sample_size = int(((data[DOW_COL] == dow) & data[person].notna()).sum())

    return {
        "person": person,
        "dow": dow,
        "verdict": verdict,
        "p_winner": round(p_winner, 1),
        "sample_size": sample_size,
    }


def predict_all_for_date(target: datetime.date) -> dict:
    info = date_info_for_one(target)
    if not info["Is weekday"]:
        return {
            "predictions": [],
            "message": f"{info['Date format']} is a {info['Shortened DOW']}. No predictions for weekends."
        }
    data = load_data()
    models = load_models()
    dow = DOW_NAMES[info["Shortened DOW"]]
    names = [n for n in get_name_list(data) if n in models]
    predictions = [
        predict_for_one(name, dow, data=data, models=models) for name in names
    ]
    return {
        "predictions": predictions,
        "message": f"Predictions for {info['Date format']}, {info['Shortened DOW']}."
    }


def evaluate(person: str) -> dict:
    """Accuracy + majority-class baseline for one person.

    Trains its OWN model on 80% to score on 20%. This is independent
    from the live model in load_models().
    """
    data = load_data()
    if person not in data.columns:
        return {"error": f"No data for {person}"}
    person_df = data[[DOW_COL, person]].dropna()
    if len(person_df) < 10:
        return {"error": f"Not enough history for {person} to evaluate."}
    X = person_df[[DOW_COL]]
    y = person_df[person].astype(int)
    # random_state=0 is deliberate: the same holdout indices are reused across
    # every person, so evaluate_all() can report a single representative
    # `n_samples` for the whole dataset instead of a per-person split size.
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=0
    )
    model = train_one_model(X_train, y_train)
    acc_pct = accuracy(y_test, model.predict(X_test))
    # Baseline: accuracy of always predicting the most common class. A useful
    # model should beat this number; if it doesn't, the feature isn't helping.
    baseline_pct = float((y == y.mode()[0]).mean()) * 100
    return {
        "person": person,
        "accuracy_pct": round(acc_pct, 1),
        "baseline_pct": round(baseline_pct, 1),
        "n_samples": int(len(y_test)),
        "total_samples": int(len(y)),
    }


def evaluate_all() -> dict:
    data = load_data()
    name_list = get_name_list(data)
    evaluations = [ev for ev in (evaluate(name) for name in name_list) if "error" not in ev]
    holdout_rows = max((ev["n_samples"] for ev in evaluations), default=0)  # ev is 0 if no one has enough history yet
    return {
        "evaluations": evaluations,
        "total_samples": int(len(data)),
        "n_samples": holdout_rows,
    }