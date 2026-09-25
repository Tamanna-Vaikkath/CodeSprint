"""
CodeSprint — coding practice platform built with Streamlit.

Run with:
    streamlit run app.py
"""

import base64
import json
import math
import os
import time

import streamlit as st
from streamlit_ace import st_ace

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    # Falls back to a no-op if the package isn't installed yet (see
    # requirements.txt) — the test timer will still count down correctly on
    # every rerun, it just won't tick live every second on its own.
    def st_autorefresh(*args, **kwargs):
        return 0

from utils.storage import (
    load_questions as _load_questions_from_disk,
    save_questions,
    add_questions_from_upload,
    validate_question,
    delete_question,
    get_question,
    clear_all_questions,
    QUESTIONS_FILE,
)
from utils.executor import evaluate_submission, get_supported_languages
from utils.importers import parse_uploaded_file, SUPPORTED_UPLOAD_TYPES
from utils.db import (
    save_submission as db_save_submission,
    get_all_candidate_names,
    get_submissions_for_candidate,
    delete_candidate,
)

st.set_page_config(page_title="CodeSprint", layout="wide")

DIFFICULTY_COLOR = {"Easy": "#1DA362", "Medium": "#C9820A", "Hard": "#D64545"}
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logo.png")


@st.cache_data(show_spinner=False)
def _cached_questions(_mtime):
    """Cached by the question bank file's mtime, so edits (upload/delete/clear)
    are picked up automatically without re-parsing the file on every rerun."""
    return _load_questions_from_disk()


def load_questions():
    try:
        mtime = os.path.getmtime(QUESTIONS_FILE)
    except OSError:
        mtime = 0
    return _cached_questions(mtime)


def _logo_data_uri():
    if not os.path.exists(LOGO_PATH):
        return None
    with open(LOGO_PATH, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return f"data:image/png;base64,{encoded}"


# ----------------------------------------------------------------------------
# Global light-theme styling
# ----------------------------------------------------------------------------
st.markdown(
    """
    <style>
        #MainMenu, footer {visibility: hidden;}

        .stApp {
            background-color: #FFFFFF;
        }

        section[data-testid="stSidebar"] {
            background-color: #F4F6FA;
            border-right: 1px solid #E4E8F0;
        }

        div.stButton > button {
            border-radius: 8px;
            border: 1px solid #D8DEE9;
            background-color: #FFFFFF;
            color: #1F2430;
            font-weight: 500;
            transition: all 0.15s ease-in-out;
        }
        div.stButton > button:hover {
            border-color: #1E6FEB;
            color: #1E6FEB;
            background-color: #EDF3FE;
        }
        div.stButton > button[kind="primary"] {
            background-color: #1E6FEB;
            border-color: #1E6FEB;
            color: #FFFFFF;
        }
        div.stButton > button[kind="primary"]:hover {
            background-color: #1656B8;
            border-color: #1656B8;
            color: #FFFFFF;
        }

        div[data-testid="stMetric"] {
            background-color: #FFFFFF;
            border: 1px solid #E4E8F0;
            border-radius: 10px;
            padding: 10px 14px;
        }

        .cs-logo-row {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 4px;
        }
        .cs-logo-row img {
            height: 34px;
        }
        .cs-logo-row span {
            font-size: 1.35rem;
            font-weight: 700;
            color: #1F2430;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# ----------------------------------------------------------------------------
# Session state initialisation
# ----------------------------------------------------------------------------
if "view" not in st.session_state:
    st.session_state.view = "problems"
if "selected_qid" not in st.session_state:
    st.session_state.selected_qid = None
if "submissions" not in st.session_state:
    st.session_state.submissions = {}  # qid -> list[submission summary dict]
if "editor_code" not in st.session_state:
    st.session_state.editor_code = {}  # qid -> current code in editor
if "last_run" not in st.session_state:
    st.session_state.last_run = {}  # qid -> last run/submit result
if "aiml_test_started" not in st.session_state:
    st.session_state.aiml_test_started = False
if "aiml_test_finished" not in st.session_state:
    st.session_state.aiml_test_finished = False
if "aiml_test_start_time" not in st.session_state:
    st.session_state.aiml_test_start_time = None
if "aiml_test_index" not in st.session_state:
    st.session_state.aiml_test_index = 0
if "aiml_test_answers" not in st.session_state:
    st.session_state.aiml_test_answers = {}  # qid -> {"submitted", "selected"/"text", "is_correct"}
if "candidate_name" not in st.session_state:
    st.session_state.candidate_name = ""


def go_to(view, qid=None):
    st.session_state.view = view
    if qid is not None:
        st.session_state.selected_qid = qid


def _ordered_list_for_origin(origin):
    """The full, stable question order for a given tab, used to figure out
    what 'next' and 'previous' mean on the solve page — independent of any
    search/filter/pagination the user had applied on the list view."""
    qs = load_questions()
    if origin == "aiml":
        return [q for q in qs if q.get("category") == AIML_CATEGORY]
    return [q for q in qs if q.get("category") != AIML_CATEGORY]


def _go_to_question(q):
    st.session_state.editor_code.setdefault(q["id"], q.get("starter_code", ""))
    go_to("solve", q["id"])


AIML_CATEGORY = "Artificial Intelligence & Machine Learning"
AIML_TEST_DURATION_SECONDS = 45 * 60


def _aiml_test_reset():
    st.session_state.aiml_test_started = False
    st.session_state.aiml_test_finished = False
    st.session_state.aiml_test_start_time = None
    st.session_state.aiml_test_index = 0
    st.session_state.aiml_test_answers = {}


def _persist_submission(q, submission):
    """Write a submission to the SQLite database under the current
    candidate's name, in addition to session_state. This is what makes
    results survive an app rerun/restart, and is what powers the Reviewer
    Dashboard (HR looks a candidate up by name and reads straight from
    disk instead of from a session that may no longer exist)."""
    name = st.session_state.get("candidate_name", "").strip()
    if not name:
        return
    try:
        db_save_submission(name, q, submission)
    except Exception as e:
        st.warning(f"Couldn't save this submission to the database: {e}")


def is_solved(qid):
    subs = st.session_state.submissions.get(qid, [])
    return any(s["passed_all"] for s in subs)


def _progress_stats(qlist):
    """Per-question progress across a list: a question counts as
    'attempted' once it has any submission, 'correct' if at least one
    submission passed, and 'incorrect' if it was attempted but never
    passed. Based on session_state, so it stays accurate as the user
    answers more questions instead of a static 'Solved 0'."""
    attempted = correct = incorrect = 0
    for q in qlist:
        subs = st.session_state.submissions.get(q["id"], [])
        if not subs:
            continue
        attempted += 1
        if any(s["passed_all"] for s in subs):
            correct += 1
        else:
            incorrect += 1
    return attempted, correct, incorrect


# ----------------------------------------------------------------------------
# Sidebar navigation
# ----------------------------------------------------------------------------
with st.sidebar:
    logo_uri = _logo_data_uri()
    if logo_uri:
        st.markdown(
            f"<div class='cs-logo-row'><img src='{logo_uri}'/><span>CodeSprint</span></div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown("## CodeSprint")
    st.caption("Race Through Challenges. Master Every Problem.")
    st.divider()

    candidate_name_input = st.text_input(
        "Candidate name",
        value=st.session_state.candidate_name,
        placeholder="Enter your full name",
        key="candidate_name_input",
    )
    st.session_state.candidate_name = candidate_name_input.strip()

    st.divider()

    if st.button("Problems", use_container_width=True):
        go_to("problems")
    if st.button("Artificial Intelligence & Machine Learning", use_container_width=True):
        go_to("aiml")
    if st.button("Upload Question Bank", use_container_width=True):
        go_to("upload")
    if st.button("My Submissions", use_container_width=True):
        go_to("submissions")
    if st.button("Reviewer Dashboard", use_container_width=True):
        go_to("reviewer")

    st.divider()
    all_qs = load_questions()
    general_qs = [q for q in all_qs if q.get("category") != AIML_CATEGORY]
    aiml_qs = [q for q in all_qs if q.get("category") == AIML_CATEGORY]
    general_attempted, general_correct, general_incorrect = _progress_stats(general_qs)
    aiml_attempted, aiml_correct, aiml_incorrect = _progress_stats(aiml_qs)

    st.caption("Problems")
    c1, c2 = st.columns(2)
    c1.metric("Total", len(general_qs))
    c2.metric("Attempted", general_attempted)
    c3, c4 = st.columns(2)
    c3.metric("Correct", general_correct)
    c4.metric("Incorrect", general_incorrect)

    st.divider()

    st.caption("AI/ML MCQs")
    c5, c6 = st.columns(2)
    c5.metric("Total", len(aiml_qs))
    c6.metric("Attempted", aiml_attempted)
    c7, c8 = st.columns(2)
    c7.metric("Correct", aiml_correct)
    c8.metric("Incorrect", aiml_incorrect)

    st.divider()


# ----------------------------------------------------------------------------
# View: Problems list
# ----------------------------------------------------------------------------
PAGE_SIZE_OPTIONS = [25, 50, 100]


def _paginate(items, state_prefix):
    """Slice a (possibly large) list down to a single page and render the
    pagination controls for it.

    Rendering hundreds of rows of columns/buttons in one go is what makes a
    Streamlit app "keep loading" — every rerun has to rebuild every widget on
    the page. Capping each render to a small page keeps reruns fast no matter
    how many questions are in the bank (626 today, or 6000 later)."""
    page_key = f"{state_prefix}_page"
    size_key = f"{state_prefix}_page_size"
    count_key = f"{state_prefix}_last_count"

    st.session_state.setdefault(size_key, PAGE_SIZE_OPTIONS[0])
    st.session_state.setdefault(page_key, 1)

    # Whenever the filtered result set changes size (new search/filter),
    # jump back to page 1 instead of showing a now-meaningless page number.
    if st.session_state.get(count_key) != len(items):
        st.session_state[page_key] = 1
        st.session_state[count_key] = len(items)

    page_size = st.session_state[size_key]
    total = len(items)
    total_pages = max(1, math.ceil(total / page_size))
    st.session_state[page_key] = min(st.session_state[page_key], total_pages)
    page = st.session_state[page_key]

    nav1, nav2, nav3, nav4 = st.columns([1, 1, 2, 1.3])
    with nav1:
        if st.button("← Prev", key=f"{state_prefix}_prev", disabled=(page <= 1), use_container_width=True):
            st.session_state[page_key] -= 1
            st.rerun()
    with nav2:
        if st.button("Next →", key=f"{state_prefix}_next", disabled=(page >= total_pages), use_container_width=True):
            st.session_state[page_key] += 1
            st.rerun()
    with nav3:
        st.markdown(f"Page **{page}** of **{total_pages}** &nbsp;({total} results)")
    with nav4:
        new_size = st.selectbox(
            "Rows per page",
            PAGE_SIZE_OPTIONS,
            index=PAGE_SIZE_OPTIONS.index(page_size),
            key=f"{state_prefix}_size_select",
            label_visibility="collapsed",
        )
        if new_size != page_size:
            st.session_state[size_key] = new_size
            st.session_state[page_key] = 1
            st.rerun()

    start = (page - 1) * page_size
    return items[start:start + page_size]


def _render_question_table(qlist, origin="problems"):
    """Render the Status/Title/Difficulty/Type/Tags/Action table for a list of questions.
    `origin` records which tab ("problems" or "aiml") the Solve button was clicked
    from, so the "← Back to problems" button on the solve page returns to it."""
    h1, h2, h3, h4, h5, h6 = st.columns([0.5, 3, 1, 0.8, 1.5, 1])
    h1.markdown("**Status**")
    h2.markdown("**Title**")
    h3.markdown("**Difficulty**")
    h4.markdown("**Type**")
    h5.markdown("**Tags**")
    h6.markdown("**Action**")

    for q in qlist:
        c1, c2, c3, c4, c5, c6 = st.columns([0.5, 3, 1, 0.8, 1.5, 1])
        if is_solved(q["id"]):
            c1.markdown(
                "<span style='color:#1DA362; font-weight:700;'>Solved</span>",
                unsafe_allow_html=True,
            )
        else:
            c1.markdown("<span style='color:#9AA3B2;'>&mdash;</span>", unsafe_allow_html=True)
        c2.markdown(f"**{q['title']}**")
        color = DIFFICULTY_COLOR.get(q["difficulty"], "#888")
        c3.markdown(
            f"<span style='color:{color}; font-weight:600'>{q['difficulty']}</span>",
            unsafe_allow_html=True,
        )
        c4.markdown("🧩 MCQ" if q.get("type") == "mcq" else "💻 Coding")
        c5.markdown(", ".join(q.get("tags", [])) or "—")
        if c6.button("Solve →", key=f"solve_{origin}_{q['id']}"):
            st.session_state.editor_code.setdefault(q["id"], q.get("starter_code", ""))
            st.session_state.solve_origin = origin
            go_to("solve", q["id"])
            st.rerun()


def render_problems():
    st.title("Problem List")

    questions = [q for q in load_questions() if q.get("category") != AIML_CATEGORY]
    if not questions:
        st.info(
            "No questions in the bank yet. Head to **Upload Question Bank** "
            "in the sidebar to add some, or use the bundled sample set."
        )
        return

    # --- filters ---
    fc1, fc2, fc3 = st.columns([2, 1, 1])
    with fc1:
        search = st.text_input("Search by title or tag", "", key="problems_search")
    with fc2:
        diff_filter = st.multiselect(
            "Difficulty", ["Easy", "Medium", "Hard"], default=[], key="problems_diff"
        )
    with fc3:
        all_tags = sorted({t for q in questions for t in q.get("tags", [])})
        tag_filter = st.multiselect("Tags", all_tags, default=[], key="problems_tags")

    filtered = []
    for q in questions:
        if search:
            haystack = (q["title"] + " " + " ".join(q.get("tags", []))).lower()
            if search.lower() not in haystack:
                continue
        if diff_filter and q["difficulty"] not in diff_filter:
            continue
        if tag_filter and not set(tag_filter).intersection(q.get("tags", [])):
            continue
        filtered.append(q)

    st.caption(f"Showing {len(filtered)} of {len(questions)} problems")
    st.divider()

    page_items = _paginate(filtered, "problems")
    _render_question_table(page_items, origin="problems")


# ----------------------------------------------------------------------------
# View: Artificial Intelligence & Machine Learning — timed aptitude test
# ----------------------------------------------------------------------------
def render_aiml_test():
    st.title(AIML_CATEGORY)

    questions = [q for q in load_questions() if q.get("category") == AIML_CATEGORY]
    if not questions:
        st.info("No AI/ML aptitude test questions found in the bank.")
        return

    # --- not started yet: intro / start screen ---
    if not st.session_state.aiml_test_started and not st.session_state.aiml_test_finished:
        st.subheader("AI/ML Engineer — Aptitude Test")
        mcq_count = sum(1 for q in questions if q.get("type") == "mcq")
        scenario_count = sum(1 for q in questions if q.get("type") == "scenario")
        st.markdown(
            f"This is a timed assessment — **{len(questions)} questions** "
            f"({mcq_count} multiple-choice, {scenario_count} scenario/written) "
            f"to be completed in **45 minutes**. The timer starts the moment you "
            f"click **Start Test** and keeps running until you finish or time "
            f"runs out."
        )
        with st.container(border=True):
            st.markdown("**What to expect**")
            st.markdown(
                "- Answer each question, then click **Submit Answer** to lock it in.\n"
                "- For multiple-choice questions you'll immediately see whether you "
                "were correct, the correct option, and an explanation.\n"
                "- For scenario questions, type your answer in the text box — these "
                "are recorded for manual review rather than auto-graded.\n"
                "- Use **← Previous / Next →** to move between questions; you can "
                "revisit already-answered questions at any time before finishing.\n"
                "- Click **Finish Test** any time, or let the 45-minute timer run out."
            )
        if st.button("Start Test", type="primary"):
            st.session_state.aiml_test_started = True
            st.session_state.aiml_test_start_time = time.time()
            st.session_state.aiml_test_index = 0
            st.session_state.aiml_test_answers = {}
            st.rerun()
        return

    # --- compute remaining time ---
    elapsed = time.time() - st.session_state.aiml_test_start_time
    remaining = AIML_TEST_DURATION_SECONDS - elapsed
    if remaining <= 0 and not st.session_state.aiml_test_finished:
        st.session_state.aiml_test_finished = True
        remaining = 0

    # --- finished: summary / review screen ---
    if st.session_state.aiml_test_finished:
        _render_aiml_test_summary(questions)
        return

    # --- active test: keep the timer ticking live, once per second ---
    st_autorefresh(interval=1000, key="aiml_test_autorefresh")

    mins, secs = divmod(int(remaining), 60)
    timer_color = "#D64545" if remaining < 5 * 60 else "#1B2A4A"
    tcol1, tcol2 = st.columns([3, 1])
    with tcol2:
        st.markdown(
            f"<div style='text-align:right; font-size:1.6em; font-weight:700; "
            f"color:{timer_color};'>⏱ {mins:02d}:{secs:02d}</div>",
            unsafe_allow_html=True,
        )
    with tcol1:
        answered = sum(1 for a in st.session_state.aiml_test_answers.values() if a.get("submitted"))
        st.progress(answered / len(questions), text=f"{answered} of {len(questions)} answered")

    idx = max(0, min(st.session_state.aiml_test_index, len(questions) - 1))
    st.session_state.aiml_test_index = idx
    q = questions[idx]

    st.markdown(f"#### Question {idx + 1} of {len(questions)}")
    color = DIFFICULTY_COLOR.get(q["difficulty"], "#888")
    type_badge = "🧩 MCQ" if q.get("type") == "mcq" else "📝 Scenario"
    st.markdown(
        f"### {q['title']}  "
        f"<span style='font-size:0.5em; color:{color}; border:1px solid {color}; "
        f"border-radius:6px; padding:2px 8px;'>{q['difficulty']}</span>  "
        f"<span style='font-size:0.5em; color:#555; border:1px solid #D8DEE9; "
        f"border-radius:6px; padding:2px 8px;'>{type_badge}</span>",
        unsafe_allow_html=True,
    )
    if q.get("tags"):
        st.caption(" · ".join(f"`{t}`" for t in q["tags"]))

    if q.get("type") == "mcq":
        _render_aiml_test_mcq(q)
    else:
        _render_aiml_test_scenario(q)

    st.divider()
    nav_prev, nav_next, nav_finish = st.columns([1, 1, 1.3])
    with nav_prev:
        if st.button("← Previous", disabled=(idx <= 0), use_container_width=True):
            st.session_state.aiml_test_index -= 1
            st.rerun()
    with nav_next:
        if st.button("Next →", disabled=(idx >= len(questions) - 1), use_container_width=True):
            st.session_state.aiml_test_index += 1
            st.rerun()
    with nav_finish:
        if st.button("Finish Test", type="primary", use_container_width=True):
            st.session_state.aiml_test_finished = True
            st.rerun()


def _render_aiml_test_mcq(q):
    qid = q["id"]
    with st.container(border=True):
        st.markdown(q["description"])

    state = st.session_state.aiml_test_answers.setdefault(
        qid, {"submitted": False, "selected": None, "is_correct": None}
    )

    options = q.get("options", [])
    option_display = [f"{o['label']}. {o['text']}" for o in options]
    display_to_letter = {d: o["label"] for d, o in zip(option_display, options)}

    choice_display = st.radio(
        "Select your answer",
        option_display,
        index=None,
        key=f"aimltest_choice_{qid}",
        disabled=state["submitted"],
    )

    if not state["submitted"]:
        if st.button("Submit Answer", type="primary", key=f"aimltest_submit_{qid}"):
            if choice_display is None:
                st.warning("Please select an option before submitting.")
            else:
                selected_letter = display_to_letter[choice_display]
                correct_letter = str(q.get("correct_answer", "")).strip().upper()
                is_correct = selected_letter.strip().upper() == correct_letter
                state["submitted"] = True
                state["selected"] = selected_letter
                state["is_correct"] = is_correct

                option_text = {o["label"]: o["text"] for o in options}
                submission = {
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "language": "MCQ",
                    "passed_count": 1 if is_correct else 0,
                    "total_count": 1,
                    "passed_all": is_correct,
                    "score_pct": 100.0 if is_correct else 0.0,
                    "points": q.get("points", 1) if is_correct else 0,
                    "points_possible": q.get("points", 1),
                    "selected_letter": selected_letter,
                    "selected_text": option_text.get(selected_letter, selected_letter),
                    "correct_letter": correct_letter,
                    "correct_text": option_text.get(correct_letter, correct_letter),
                    "explanation": q.get("explanation", ""),
                }
                st.session_state.submissions.setdefault(qid, []).append(submission)
                _persist_submission(q, submission)
                st.rerun()

    if not st.session_state.candidate_name:
        st.caption("⚠️ Enter your name in the sidebar so this answer gets saved for review.")

    if state["submitted"]:
        option_text = {o["label"]: o["text"] for o in options}
        correct_letter = str(q.get("correct_answer", "")).strip().upper()
        if state["is_correct"]:
            st.success(f"Correct! The answer is **{correct_letter}. {option_text.get(correct_letter, '')}**.")
        else:
            st.error(
                f"Not quite — you selected **{state['selected']}. "
                f"{option_text.get(state['selected'], '')}**; the correct answer is "
                f"**{correct_letter}. {option_text.get(correct_letter, '')}**."
            )
        if q.get("explanation"):
            with st.expander("Explanation", expanded=True):
                st.markdown(q["explanation"])


def _render_aiml_test_scenario(q):
    qid = q["id"]
    with st.container(border=True):
        st.markdown(q["description"])

    state = st.session_state.aiml_test_answers.setdefault(qid, {"submitted": False, "text": ""})

    text = st.text_area(
        "Your answer",
        value=state.get("text", ""),
        height=220,
        key=f"aimltest_text_{qid}",
        disabled=state["submitted"],
        placeholder="Type your response here (4–8 sentences)...",
    )

    if not state["submitted"]:
        if st.button("Submit Answer", type="primary", key=f"aimltest_submit_{qid}"):
            if not text or not text.strip():
                st.warning("Please write an answer before submitting.")
            else:
                state["submitted"] = True
                state["text"] = text
                submission = {
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "language": "Scenario",
                    "passed_count": 0,
                    "total_count": 1,
                    "passed_all": None,
                    "score_pct": 0.0,
                    "points": 0,
                    "points_possible": q.get("points", 1),
                    "answer_text": text,
                }
                st.session_state.submissions.setdefault(qid, []).append(submission)
                _persist_submission(q, submission)
                st.rerun()
    else:
        st.success("Your answer has been recorded for review.")

    if not state["submitted"] and not st.session_state.candidate_name:
        st.caption("⚠️ Enter your name in the sidebar so this answer gets saved for review.")


def _render_aiml_test_summary(questions):
    st.subheader("Test complete")

    answers = st.session_state.aiml_test_answers
    mcq_qs = [q for q in questions if q.get("type") == "mcq"]
    scenario_qs = [q for q in questions if q.get("type") == "scenario"]

    mcq_correct = sum(1 for q in mcq_qs if answers.get(q["id"], {}).get("is_correct"))
    mcq_attempted = sum(1 for q in mcq_qs if answers.get(q["id"], {}).get("submitted"))
    scenario_attempted = sum(1 for q in scenario_qs if answers.get(q["id"], {}).get("submitted"))

    points_earned = sum(q.get("points", 1) for q in mcq_qs if answers.get(q["id"], {}).get("is_correct"))
    points_possible = sum(q.get("points", 1) for q in questions)

    elapsed = time.time() - (st.session_state.aiml_test_start_time or time.time())
    used_min, used_sec = divmod(int(min(max(elapsed, 0), AIML_TEST_DURATION_SECONDS)), 60)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("MCQ correct", f"{mcq_correct}/{len(mcq_qs)}" if mcq_qs else "—")
    c2.metric("MCQ attempted", f"{mcq_attempted}/{len(mcq_qs)}" if mcq_qs else "—")
    c3.metric("Scenarios submitted", f"{scenario_attempted}/{len(scenario_qs)}" if scenario_qs else "—")
    c4.metric("Time used", f"{used_min}m {used_sec:02d}s")

    if points_possible:
        st.progress(
            min(points_earned / points_possible, 1.0),
            text=f"Score: {points_earned}/{points_possible} pts "
                 f"(auto-graded MCQs — scenario answers need manual review)",
        )

    st.divider()
    st.markdown("#### Review your answers")
    for i, q in enumerate(questions):
        state = answers.get(q["id"], {})
        with st.expander(f"Q{i + 1}. {q['title']}"):
            st.markdown(q["description"])
            if q.get("type") == "mcq":
                options = q.get("options", [])
                option_text = {o["label"]: o["text"] for o in options}
                correct_letter = str(q.get("correct_answer", "")).strip().upper()
                if state.get("submitted"):
                    sel = state.get("selected")
                    if state.get("is_correct"):
                        st.success(f"Your answer: **{sel}. {option_text.get(sel, '')}** — Correct")
                    else:
                        st.error(
                            f"Your answer: **{sel}. {option_text.get(sel, '')}** — Incorrect. "
                            f"Correct answer: **{correct_letter}. {option_text.get(correct_letter, '')}**"
                        )
                else:
                    st.warning("Not answered.")
                if q.get("explanation"):
                    st.caption(f"Explanation: {q['explanation']}")
            else:
                if state.get("submitted"):
                    st.write(state.get("text", ""))
                else:
                    st.warning("Not answered.")

    st.divider()
    if st.button("Retake Test", type="primary"):
        _aiml_test_reset()
        st.rerun()


# ----------------------------------------------------------------------------
# View: Upload question bank
# ----------------------------------------------------------------------------
def render_upload():
    st.title("Upload Question Bank")
    st.write(
        "Upload one or more question bank files at once — **JSON, TXT, CSV, Excel "
        "(.xlsx/.xls), Word (.docx), or PDF**. You can select multiple files "
        "and import them all in a single click."
    )
    with st.expander("Supported formats & layouts"):
        st.markdown(
            "- **JSON/TXT** — an array of question objects.\n"
            "- **CSV/Excel (generic)** — header row with columns `id`, `title`, "
            "`difficulty`, `tags`, `description`, `starter_code`, `test_cases`, "
            "`time_limit`.\n"
            "- **Excel (MCQ template)** — auto-detected when the sheet has columns "
            "like `Question Type`, `Question Text`, `Option (A)`...`Option (E)`, "
            "`Correct Answer`, `Score`, `Topics`.\n"
            "- **Word (structured coding format)** — auto-detected when each "
            "question is a heading (`Question N: Title`) followed by `Topic:`, "
            "`Difficulty Level:`, `Problem Statement:`, `Starter Code "
            "(Boilerplate):`, `Solution Code:`, `Explanation / Evaluation Notes:`, "
            "`Language:`, `Question Type:`, `Points:`. The reference solution is "
            "run automatically to derive the expected test-case output.\n"
            "- **Word (table)** — a table with a header row matching the generic "
            "column layout above."
        )

    with open("data/question_template.json", "r", encoding="utf-8") as f:
        template_bytes = f.read()
    st.download_button(
        "Download JSON template",
        data=template_bytes,
        file_name="question_template.json",
        mime="application/json",
    )

    st.divider()

    mode = st.radio(
        "Import mode",
        ["Append to existing bank", "Replace entire bank"],
        horizontal=True,
    )
    mode_key = "append" if mode.startswith("Append") else "replace"

    uploaded_files = st.file_uploader(
        "Choose one or more question bank files",
        type=SUPPORTED_UPLOAD_TYPES,
        accept_multiple_files=True,
    )

    if uploaded_files:
        all_questions = []
        for uf in uploaded_files:
            content, parse_errors = parse_uploaded_file(uf.name, uf.getvalue())
            for e in parse_errors:
                st.error(f"**{uf.name}:** {e}")
            if content:
                n_coding = sum(1 for q in content if q.get("type", "coding") == "coding")
                n_mcq = sum(1 for q in content if q.get("type") == "mcq")
                st.write(
                    f"**{uf.name}:** found **{len(content)}** question(s) "
                    f"({n_coding} coding, {n_mcq} MCQ)."
                )
                all_questions.extend(content)

        if all_questions:
            st.success(
                f"**{len(all_questions)}** question(s) total across "
                f"{len(uploaded_files)} file(s), ready to import."
            )
            with st.expander("Preview raw data (first 5)"):
                st.json(all_questions[:5])

            if st.button("Import all into question bank", type="primary"):
                added, errors = add_questions_from_upload(all_questions, mode=mode_key)
                if added:
                    st.success(
                        f"Imported {added} question(s) successfully from "
                        f"{len(uploaded_files)} file(s)."
                    )
                if errors:
                    st.warning("Some entries were skipped or need review:")
                    for e in errors:
                        st.markdown(f"- {e}")
                if added:
                    time.sleep(1)
                    st.rerun()

    st.divider()

    # --- manual single-question form ---
    with st.expander("Add a single question manually"):
        with st.form("manual_add_form", clear_on_submit=True):
            m_id = st.text_input("ID (unique, no spaces)")
            m_title = st.text_input("Title")
            m_diff = st.selectbox("Difficulty", ["Easy", "Medium", "Hard"])
            m_tags = st.text_input("Tags (comma-separated)")
            m_desc = st.text_area("Description (Markdown supported)", height=150)
            m_starter = st.text_area("Starter code", height=120, value="# your code here\n")
            st.caption("Test cases: one per block below. Add more via the number input.")
            n_tests = st.number_input("Number of test cases", min_value=1, max_value=10, value=2)
            test_cases = []
            for i in range(int(n_tests)):
                st.markdown(f"**Test case {i + 1}**")
                tc_in = st.text_area(f"Input #{i + 1}", key=f"m_in_{i}", height=60)
                tc_out = st.text_area(f"Expected output #{i + 1}", key=f"m_out_{i}", height=60)
                tc_hidden = st.checkbox(f"Hidden test case #{i + 1}", key=f"m_hidden_{i}")
                test_cases.append({"input": tc_in, "expected_output": tc_out, "hidden": tc_hidden})

            submitted = st.form_submit_button("Add question", type="primary")
            if submitted:
                q = {
                    "id": m_id.strip(),
                    "title": m_title.strip(),
                    "difficulty": m_diff,
                    "tags": [t.strip() for t in m_tags.split(",") if t.strip()],
                    "description": m_desc,
                    "starter_code": m_starter,
                    "time_limit": 5,
                    "type": "coding",
                    "test_cases": test_cases,
                }
                problems = validate_question(q)
                if problems:
                    st.error("Could not add question: " + "; ".join(problems))
                else:
                    added, errors = add_questions_from_upload([q], mode="append")
                    if added:
                        st.success(f"Added '{q['title']}' to the question bank.")
                    for e in errors:
                        st.error(e)

    st.divider()

    # --- manage existing questions ---
    st.subheader("Manage existing questions")
    questions = load_questions()
    if not questions:
        st.info("Question bank is empty.")
    else:
        manage_page = _paginate(questions, "manage")
        for q in manage_page:
            c1, c2, c3 = st.columns([3, 1, 1])
            c1.write(f"**{q['title']}**  ·  `{q['id']}`  ·  {q['difficulty']}")
            if q.get("type") == "mcq":
                c2.write(f"MCQ · {len(q.get('options', []))} options")
            elif q.get("type") == "scenario":
                c2.write("Scenario · written answer")
            else:
                c2.write(f"Coding · {len(q.get('test_cases', []))} test case(s)")
            if c3.button("Delete", key=f"del_{q['id']}"):
                delete_question(q["id"])
                st.rerun()

        st.divider()
        if st.button("Clear entire question bank", type="secondary"):
            st.session_state["confirm_clear"] = True

        if st.session_state.get("confirm_clear"):
            st.warning("This will permanently delete all questions. Are you sure?")
            cc1, cc2 = st.columns(2)
            if cc1.button("Yes, delete everything"):
                clear_all_questions()
                st.session_state["confirm_clear"] = False
                st.rerun()
            if cc2.button("Cancel"):
                st.session_state["confirm_clear"] = False


# ----------------------------------------------------------------------------
# View: Solve a problem (description + editor)
# ----------------------------------------------------------------------------
def render_solve():
    qid = st.session_state.selected_qid
    q = get_question(qid)
    back_view = st.session_state.get("solve_origin", "problems")
    back_label = "← Back to AI/ML questions" if back_view == "aiml" else "← Back to problems"

    if q is None:
        st.error("Question not found. It may have been deleted.")
        if st.button(back_label):
            go_to(back_view)
            st.rerun()
        return

    ordered = _ordered_list_for_origin(back_view)
    ordered_ids = [oq["id"] for oq in ordered]
    idx = ordered_ids.index(qid) if qid in ordered_ids else None

    if st.button(back_label):
        go_to(back_view)
        st.rerun()

    color = DIFFICULTY_COLOR.get(q["difficulty"], "#888")
    type_badge = "🧩 MCQ" if q.get("type") == "mcq" else "💻 Coding"
    st.markdown(
        f"## {q['title']}  "
        f"<span style='font-size:0.5em; color:{color}; border:1px solid {color}; "
        f"border-radius:6px; padding:2px 8px;'>{q['difficulty']}</span>  "
        f"<span style='font-size:0.5em; color:#555; border:1px solid #D8DEE9; "
        f"border-radius:6px; padding:2px 8px;'>{type_badge}</span>",
        unsafe_allow_html=True,
    )
    if q.get("tags"):
        st.caption(" · ".join(f"`{t}`" for t in q["tags"]))

    if q.get("type") == "mcq":
        render_solve_mcq(q)
    else:
        render_solve_coding(q)

    # --- prev/next navigation, placed below the question & answer so it
    # doesn't compete with the content for attention ---
    st.divider()
    nav_prev, nav_next, nav_pos = st.columns([1, 1, 1.5])
    with nav_prev:
        prev_disabled = idx is None or idx <= 0
        if st.button("← Previous", disabled=prev_disabled, use_container_width=True):
            _go_to_question(ordered[idx - 1])
            st.rerun()
    with nav_next:
        next_disabled = idx is None or idx >= len(ordered) - 1
        if st.button("Next →", disabled=next_disabled, use_container_width=True):
            _go_to_question(ordered[idx + 1])
            st.rerun()
    with nav_pos:
        if idx is not None:
            st.markdown(f"Question **{idx + 1}** of **{len(ordered)}**")


def render_solve_mcq(q):
    qid = q["id"]

    st.markdown("#### Question")
    with st.container(border=True):
        st.markdown(q["description"])

    options = q.get("options", [])
    option_display = [f"{o['label']}. {o['text']}" for o in options]
    display_to_letter = {d: o["label"] for d, o in zip(option_display, options)}

    choice = st.radio(
        "Select your answer",
        option_display,
        index=None,
        key=f"mcq_choice_{qid}",
    )

    submit_clicked = st.button("Submit Answer", type="primary", key=f"mcq_submit_{qid}")

    if submit_clicked:
        if choice is None:
            st.warning("Please select an option before submitting.")
        else:
            selected_letter = display_to_letter[choice]
            correct_letter = str(q.get("correct_answer", "")).strip().upper()
            is_correct = selected_letter.strip().upper() == correct_letter
            st.session_state.last_run[qid] = {
                "mode": "mcq",
                "selected": selected_letter,
                "correct": correct_letter,
                "is_correct": is_correct,
            }
            option_text = {o["label"]: o["text"] for o in options}
            submission = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "language": "MCQ",
                "passed_count": 1 if is_correct else 0,
                "total_count": 1,
                "passed_all": is_correct,
                "score_pct": 100.0 if is_correct else 0.0,
                "points": q.get("points", 1) if is_correct else 0,
                "points_possible": q.get("points", 1),
                "selected_letter": selected_letter,
                "selected_text": option_text.get(selected_letter, selected_letter),
                "correct_letter": correct_letter,
                "correct_text": option_text.get(correct_letter, correct_letter),
                "explanation": q.get("explanation", ""),
            }
            st.session_state.submissions.setdefault(qid, []).append(submission)
            _persist_submission(q, submission)

    if not st.session_state.candidate_name:
        st.caption("⚠️ Enter your name in the sidebar so this answer gets saved for review.")

    last = st.session_state.last_run.get(qid)
    if last and last.get("mode") == "mcq":
        if last["is_correct"]:
            st.success(f"Correct! The answer is **{last['correct']}**.")
        else:
            st.error(
                f"Not quite — you selected **{last['selected']}**; "
                f"the correct answer is **{last['correct']}**."
            )
        if q.get("explanation"):
            with st.expander("Explanation"):
                st.markdown(q["explanation"])


def render_solve_coding(q):
    qid = q["id"]

    left, right = st.columns([1, 1.3])

    with left:
        st.markdown("#### Problem Statement")
        with st.container(height=520, border=True):
            st.markdown(q["description"])
            if q.get("explanation"):
                with st.expander("Explanation / hint (may contain spoilers)"):
                    st.markdown(q["explanation"])

    with right:
        st.markdown("#### Solution Editor")
        lang = st.selectbox("Language", get_supported_languages(), index=0, key=f"lang_{qid}")

        current_code = st.session_state.editor_code.get(qid, q["starter_code"])
        new_code = st_ace(
            value=current_code,
            language="python",
            theme="github",
            font_size=14,
            tab_size=4,
            show_gutter=True,
            wrap=False,
            auto_update=True,
            key=f"ace_{qid}",
            height=350,
        )
        st.session_state.editor_code[qid] = new_code

        bc1, bc2, bc3 = st.columns([1, 1, 1])
        run_clicked = bc1.button("Run Sample Tests", use_container_width=True)
        submit_clicked = bc2.button("Submit", type="primary", use_container_width=True)
        reset_clicked = bc3.button("Reset Code", use_container_width=True)

        if reset_clicked:
            st.session_state.editor_code[qid] = q["starter_code"]
            st.rerun()

        if run_clicked:
            sample_tests = [tc for tc in q["test_cases"] if not tc.get("hidden", False)]
            with st.spinner("Running sample tests..."):
                result = evaluate_submission(
                    new_code, lang, sample_tests, timeout=q.get("time_limit", 5)
                )
            st.session_state.last_run[qid] = {"mode": "run", "result": result}

        if submit_clicked:
            with st.spinner("Running full test suite..."):
                result = evaluate_submission(
                    new_code, lang, q["test_cases"], timeout=q.get("time_limit", 5)
                )
            st.session_state.last_run[qid] = {"mode": "submit", "result": result}
            submission = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "language": lang,
                "passed_count": result["passed_count"],
                "total_count": result["total_count"],
                "passed_all": result["passed_all"],
                "score_pct": result["score_pct"],
                "code": new_code,
                "result": result,
            }
            st.session_state.submissions.setdefault(qid, []).append(submission)
            _persist_submission(q, submission)

        if not st.session_state.candidate_name:
            st.caption("⚠️ Enter your name in the sidebar so a Submit gets saved for review.")

        # --- show results ---
        last = st.session_state.last_run.get(qid)
        if last and last.get("mode") in ("run", "submit"):
            result = last["result"]
            mode_label = "Sample run" if last["mode"] == "run" else "Submission"

            if result["passed_all"]:
                st.success(
                    f"{mode_label}: All {result['total_count']} test case(s) passed! "
                    f"({result['score_pct']}%)"
                )
            else:
                st.error(
                    f"{mode_label}: {result['passed_count']}/{result['total_count']} "
                    f"test case(s) passed ({result['score_pct']}%)"
                )

            for r in result["results"]:
                label = f"Test case {r['test_case']}"
                if r.get("hidden") and last["mode"] == "submit":
                    label += " (hidden)"
                status_text = "PASSED" if r["passed"] else "FAILED"
                with st.expander(f"[{status_text}] {label}  ·  {r['time_taken']}s"):
                    if r.get("hidden") and last["mode"] == "submit":
                        st.caption("Input and expected output are hidden for this test case.")
                    else:
                        st.markdown("**Input:**")
                        st.code(r["input"] or "(empty)")
                        st.markdown("**Expected Output:**")
                        st.code(r["expected_output"] or "(empty)")
                        st.markdown("**Your Output:**")
                        st.code(r["actual_output"] or "(empty)")
                    if r["stderr"]:
                        st.markdown("**stderr:**")
                        st.code(r["stderr"])
                    if r["timed_out"]:
                        st.warning("Time Limit Exceeded")


# ----------------------------------------------------------------------------
# View: Submission history (candidate's own — now sourced from SQLite, so it
# survives app reruns/restarts instead of disappearing with session_state)
# ----------------------------------------------------------------------------
def _submission_row_display(row):
    """Render one saved-to-DB submission row (a dict from get_submissions_for_candidate)."""
    language = row.get("language")
    status = row.get("passed_all")  # "true" / "false" / "pending"

    if language == "MCQ":
        is_correct = status == "true"
        status_color = "#1DA362" if is_correct else "#D64545"
        status_label = "Accepted" if is_correct else "Not solved"
        st.markdown(
            f"<span style='color:{status_color}; font-weight:700;'>{status_label}</span> "
            f"· {row.get('points', 0)}/{row.get('points_possible', 1)} pts "
            f"({row.get('score_pct', 0)}%) · {row.get('timestamp', '')}",
            unsafe_allow_html=True,
        )
        with st.expander("View answer & expected answer"):
            st.write(f"**Candidate's answer:** {row.get('your_answer', '')}")
            st.write(f"**Expected (correct) answer:** {row.get('expected_answer', '')}")
            if row.get("explanation"):
                st.caption(f"Explanation: {row['explanation']}")

    elif language == "Scenario":
        st.markdown(
            f"<span style='color:#64748B; font-weight:700;'>Submitted for review</span> "
            f"· {row.get('timestamp', '')}",
            unsafe_allow_html=True,
        )
        with st.expander("View candidate's answer"):
            st.write(row.get("your_answer", ""))
            st.caption(row.get("expected_answer", ""))

    else:
        is_correct = status == "true"
        status_color = "#1DA362" if is_correct else "#D64545"
        status_label = "Accepted" if is_correct else "Not solved"
        st.markdown(
            f"<span style='color:{status_color}; font-weight:700;'>{status_label}</span> "
            f"· {language} · {row.get('passed_count', 0)}/{row.get('total_count', 0)} "
            f"tests passed ({row.get('score_pct', 0)}%) · {row.get('timestamp', '')}",
            unsafe_allow_html=True,
        )
        with st.expander("View submitted code & expected vs. actual output"):
            st.markdown("**Candidate's submitted code:**")
            st.code(row.get("your_answer", "") or "(empty)", language=(language or "python").lower())
            st.caption(row.get("expected_answer", ""))
            try:
                details = json.loads(row.get("details_json") or "{}")
                test_results = (details.get("result") or {}).get("results", [])
            except (json.JSONDecodeError, TypeError):
                test_results = []
            for r in test_results:
                tc_label = f"Test case {r.get('test_case')}"
                if r.get("hidden"):
                    tc_label += " (hidden)"
                status_text = "PASSED" if r.get("passed") else "FAILED"
                st.markdown(f"**[{status_text}] {tc_label}**")
                if r.get("hidden"):
                    st.caption("Input and expected output are hidden for this test case.")
                else:
                    ic1, ic2, ic3 = st.columns(3)
                    ic1.markdown("**Input:**")
                    ic1.code(r.get("input", "") or "(empty)")
                    ic2.markdown("**Expected output:**")
                    ic2.code(r.get("expected_output", "") or "(empty)")
                    ic3.markdown("**Candidate's output:**")
                    ic3.code(r.get("actual_output", "") or "(empty)")
                if r.get("stderr"):
                    st.caption(f"stderr: {r['stderr']}")


def _render_candidate_report(name, rows):
    """Shared summary + per-question breakdown, used by both 'My
    Submissions' (the candidate viewing their own history) and the
    Reviewer Dashboard (HR looking a candidate up by name)."""
    by_qid = {}
    for r in rows:
        by_qid.setdefault(r["qid"], []).append(r)

    total_attempts = len(rows)
    solved_qids = {qid for qid, subs in by_qid.items() if any(s["passed_all"] == "true" for s in subs)}
    not_solved_qids = set(by_qid) - solved_qids
    mcq_rows = [r for r in rows if r.get("language") == "MCQ"]
    mcq_points_earned = sum(r.get("points") or 0 for r in mcq_rows)
    mcq_points_possible = sum(r.get("points_possible") or 0 for r in mcq_rows)

    st.subheader(name)
    sc1, sc2, sc3, sc4 = st.columns(4)
    sc1.metric("Total submissions", total_attempts)
    sc2.metric("Questions solved", len(solved_qids))
    sc3.metric("Questions not solved", len(not_solved_qids))
    if mcq_points_possible:
        sc4.metric("MCQ score", f"{mcq_points_earned}/{mcq_points_possible} pts")
    st.divider()

    for qid, subs in by_qid.items():
        title = subs[0].get("question_title") or qid
        cat = subs[0].get("category")
        badge = f"  ·  {cat}" if cat else ""
        st.markdown(f"### {title}{badge}")
        for row in reversed(subs):
            _submission_row_display(row)
        st.divider()


def render_submissions():
    st.title("My Submissions")

    name = st.session_state.get("candidate_name", "").strip()
    if not name:
        st.info("Enter your name in the sidebar to see (and start saving) your results.")
        return

    rows = get_submissions_for_candidate(name)
    if not rows:
        st.info("You haven't submitted any solutions yet.")
        return

    _render_candidate_report(name, rows)


# ----------------------------------------------------------------------------
# View: Reviewer Dashboard — HR/reviewer looks up any candidate by name
# ----------------------------------------------------------------------------
def render_reviewer_dashboard():
    st.title("Reviewer Dashboard")
    st.caption(
        "Type a candidate's name to see everything they attempted."
    )

    known_names = get_all_candidate_names()
    if known_names:
        with st.expander(f"Candidates on file ({len(known_names)})"):
            st.write(", ".join(known_names))
    else:
        st.info("No candidate submissions recorded yet.")

    search = st.text_input(
        "Candidate name",
        value="",
        placeholder="Type a candidate's name...",
        key="reviewer_search_name",
    )

    if not search.strip():
        return

    rows = get_submissions_for_candidate(search.strip())
    if not rows:
        st.warning(f"No submissions found for '{search.strip()}'.")
        return

    with st.expander("⚠️ Remove this candidate from the database"):
        st.write(
            f"This permanently deletes all {len(rows)} submission(s) for "
            f"'{search.strip()}'. This cannot be undone."
        )
        confirm = st.checkbox(
            f"Yes, permanently delete '{search.strip()}'",
            key=f"confirm_delete_{search.strip().lower()}",
        )
        if st.button("Delete candidate", type="primary", disabled=not confirm):
            deleted = delete_candidate(search.strip())
            st.success(f"Deleted {deleted} submission(s) for '{search.strip()}'.")
            st.rerun()

    _render_candidate_report(search.strip(), rows)


# ----------------------------------------------------------------------------
# Router
# ----------------------------------------------------------------------------
view = st.session_state.view
if view == "problems":
    render_problems()
elif view == "aiml":
    render_aiml_test()
elif view == "upload":
    render_upload()
elif view == "solve":
    render_solve()
elif view == "submissions":
    render_submissions()
elif view == "reviewer":
    render_reviewer_dashboard()
else:
    render_problems()