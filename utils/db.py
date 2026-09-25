"""
SQLite-backed persistent storage for candidate assessment submissions.

Unlike st.session_state (which is wiped whenever the Streamlit app process
restarts, redeploys, or a new session starts), every submission recorded
through this module is written to a SQLite database on disk
(data/submissions.db). That means:

  - A candidate's own "My Submissions" history survives app restarts.
  - HR / reviewers can look up any candidate by name, at any time, from the
    Reviewer Dashboard, and see everything they attempted.

Each row captures the candidate's name, which question it was for, whether
it was solved, their answer, the expected/correct answer, and a full JSON
blob of the original result (test case breakdown, code, etc.) for deep
drill-down.
"""

import json
import os
import sqlite3
from contextlib import contextmanager

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_FILE = os.path.join(DATA_DIR, "submissions.db")


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


@contextmanager
def get_connection():
    ensure_data_dir()
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create the submissions table if it doesn't exist yet. Safe to call
    on every access — CREATE TABLE IF NOT EXISTS is a no-op once it exists."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_name TEXT NOT NULL,
                qid TEXT NOT NULL,
                question_title TEXT,
                category TEXT,
                difficulty TEXT,
                qtype TEXT,
                language TEXT,
                timestamp TEXT,
                passed_count INTEGER,
                total_count INTEGER,
                passed_all TEXT,
                score_pct REAL,
                points INTEGER,
                points_possible INTEGER,
                your_answer TEXT,
                expected_answer TEXT,
                explanation TEXT,
                details_json TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_submissions_candidate "
            "ON submissions(candidate_name)"
        )


def _derive_answer_fields(submission):
    """Work out a human-readable 'your answer' / 'expected answer' pair for
    any of the three submission shapes app.py already builds (MCQ, Scenario,
    coding)."""
    language = submission.get("language")
    if language == "MCQ":
        your_answer = f"{submission.get('selected_letter', '')}. {submission.get('selected_text', '')}"
        expected_answer = f"{submission.get('correct_letter', '')}. {submission.get('correct_text', '')}"
    elif language == "Scenario":
        your_answer = submission.get("answer_text", "")
        expected_answer = "(written answer — no single correct answer, needs manual review)"
    else:
        # Coding submission.
        your_answer = submission.get("code", "")
        total = submission.get("total_count", 0)
        passed = submission.get("passed_count", 0)
        expected_answer = f"All {total} test case(s) expected to pass (passed {passed}/{total})"
    return your_answer, expected_answer


def save_submission(candidate_name, question, submission):
    """Persist one submission record tied to a candidate + question.

    `question` is the full question dict (used for title/category/difficulty/
    type). `submission` is the same summary dict app.py already appends to
    st.session_state.submissions — this just additionally writes it to disk.
    """
    if not candidate_name or not str(candidate_name).strip():
        return
    init_db()

    your_answer, expected_answer = _derive_answer_fields(submission)
    passed_all = submission.get("passed_all")
    passed_all_str = "pending" if passed_all is None else ("true" if passed_all else "false")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO submissions (
                candidate_name, qid, question_title, category, difficulty, qtype,
                language, timestamp, passed_count, total_count, passed_all,
                score_pct, points, points_possible, your_answer, expected_answer,
                explanation, details_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(candidate_name).strip(),
                question.get("id", ""),
                question.get("title", ""),
                question.get("category", ""),
                question.get("difficulty", ""),
                question.get("type", "coding"),
                submission.get("language", ""),
                submission.get("timestamp", ""),
                submission.get("passed_count", 0),
                submission.get("total_count", 0),
                passed_all_str,
                submission.get("score_pct", 0.0),
                submission.get("points", 0),
                submission.get("points_possible", 0),
                your_answer,
                expected_answer,
                submission.get("explanation", ""),
                json.dumps(submission, ensure_ascii=False, default=str),
            ),
        )


def get_all_candidate_names():
    """Distinct list of every candidate who has any submission on file."""
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT candidate_name FROM submissions "
            "ORDER BY candidate_name COLLATE NOCASE"
        ).fetchall()
    return [r["candidate_name"] for r in rows]


def delete_candidate(candidate_name):
    """Delete every submission on file for a candidate (case-insensitive
    match on name). Returns the number of rows deleted."""
    if not candidate_name or not str(candidate_name).strip():
        return 0
    init_db()
    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM submissions WHERE candidate_name = ? COLLATE NOCASE",
            (str(candidate_name).strip(),),
        )
        return cur.rowcount


def get_submissions_for_candidate(candidate_name):
    """All submissions for a candidate (case-insensitive match), oldest first."""
    if not candidate_name or not str(candidate_name).strip():
        return []
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM submissions WHERE candidate_name = ? COLLATE NOCASE "
            "ORDER BY timestamp ASC, id ASC",
            (str(candidate_name).strip(),),
        ).fetchall()
    return [dict(r) for r in rows]