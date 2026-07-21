"""
Handles loading, saving, and validating the question bank.
Questions are persisted as JSON on disk in /data/questions.json so they
survive across app restarts.
"""

import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
QUESTIONS_FILE = os.path.join(DATA_DIR, "questions.json")

REQUIRED_FIELDS_CODING = ["id", "title", "difficulty", "description", "starter_code", "test_cases"]
REQUIRED_FIELDS_MCQ = ["id", "title", "difficulty", "description", "options", "correct_answer"]
VALID_DIFFICULTIES = {"Easy", "Medium", "Hard"}
VALID_TYPES = {"coding", "mcq"}


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_questions():
    """Load all questions from disk. Returns [] if file missing or corrupt."""
    ensure_data_dir()
    if not os.path.exists(QUESTIONS_FILE):
        return []
    try:
        with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_questions(questions):
    ensure_data_dir()
    with open(QUESTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(questions, f, indent=2, ensure_ascii=False)


def validate_question(q):
    """Return a list of problems found with a question dict. Empty list = valid."""
    errors = []
    if not isinstance(q, dict):
        return ["Question entry is not a JSON object"]

    qtype = q.get("type", "coding")
    if qtype not in VALID_TYPES:
        errors.append(f"type must be one of {sorted(VALID_TYPES)}, got '{qtype}'")
        qtype = "coding"

    required = REQUIRED_FIELDS_MCQ if qtype == "mcq" else REQUIRED_FIELDS_CODING
    missing = [f for f in required if f not in q or q[f] in (None, "", [])]
    if missing:
        errors.append(f"missing required field(s): {', '.join(missing)}")

    if "difficulty" in q and q["difficulty"] not in VALID_DIFFICULTIES:
        errors.append(f"difficulty must be one of {sorted(VALID_DIFFICULTIES)}, got '{q['difficulty']}'")

    if qtype == "mcq":
        options = q.get("options")
        if not isinstance(options, list) or len(options) < 2:
            errors.append("options must be a list with at least 2 choices")
        else:
            labels = set()
            for i, opt in enumerate(options):
                if not isinstance(opt, dict) or "label" not in opt or "text" not in opt:
                    errors.append(f"options[{i}] must have 'label' and 'text'")
                else:
                    labels.add(str(opt["label"]).strip().upper())
            correct = str(q.get("correct_answer", "")).strip().upper()
            if correct and labels and correct not in labels:
                errors.append(
                    f"correct_answer '{correct}' does not match any option label {sorted(labels)}"
                )
    else:
        if "test_cases" in q:
            tcs = q["test_cases"]
            if not isinstance(tcs, list) or len(tcs) == 0:
                errors.append("test_cases must be a non-empty list")
            else:
                for i, tc in enumerate(tcs):
                    if not isinstance(tc, dict) or "input" not in tc or "expected_output" not in tc:
                        errors.append(f"test_cases[{i}] must have 'input' and 'expected_output'")

    return errors


def add_questions_from_upload(new_questions, mode="append"):
    """
    Import a list of question dicts into the bank.
    mode: 'append' (keep existing, add new, skip duplicate ids)
          'replace' (wipe existing bank first)
    Returns (added_count, errors: list[str])
    """
    existing = [] if mode == "replace" else load_questions()
    existing_ids = {q["id"] for q in existing}
    errors = []
    added = 0

    for idx, q in enumerate(new_questions):
        problems = validate_question(q)
        if problems:
            label = q.get("title", f"item #{idx + 1}") if isinstance(q, dict) else f"item #{idx + 1}"
            errors.append(f"'{label}': {'; '.join(problems)}")
            continue
        if q["id"] in existing_ids:
            errors.append(f"'{q.get('title')}' (id='{q['id']}') skipped: duplicate id already in bank")
            continue
        # normalize optional fields
        q.setdefault("tags", [])
        q.setdefault("type", "coding")
        q.setdefault("points", 5)
        q.setdefault("explanation", "")
        if q["type"] == "coding":
            q.setdefault("time_limit", 5)
        existing.append(q)
        existing_ids.add(q["id"])
        added += 1

    save_questions(existing)
    return added, errors


def delete_question(qid):
    questions = load_questions()
    questions = [q for q in questions if q["id"] != qid]
    save_questions(questions)


def get_question(qid):
    for q in load_questions():
        if q["id"] == qid:
            return q
    return None


def clear_all_questions():
    save_questions([])
