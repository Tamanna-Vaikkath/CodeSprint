"""
CodeSprint — coding practice platform built with Streamlit.

Run with:
    streamlit run app.py
"""

import base64
import json
import os
import time

import streamlit as st
from streamlit_ace import st_ace

from utils.storage import (
    load_questions,
    save_questions,
    add_questions_from_upload,
    validate_question,
    delete_question,
    get_question,
    clear_all_questions,
)
from utils.executor import evaluate_submission, get_supported_languages
from utils.importers import parse_uploaded_file, SUPPORTED_UPLOAD_TYPES

st.set_page_config(page_title="CodeSprint", layout="wide")

DIFFICULTY_COLOR = {"Easy": "#1DA362", "Medium": "#C9820A", "Hard": "#D64545"}
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logo.png")


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


def go_to(view, qid=None):
    st.session_state.view = view
    if qid is not None:
        st.session_state.selected_qid = qid


def is_solved(qid):
    subs = st.session_state.submissions.get(qid, [])
    return any(s["passed_all"] for s in subs)


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

    if st.button("Problems", use_container_width=True):
        go_to("problems")
    if st.button("Upload Question Bank", use_container_width=True):
        go_to("upload")
    if st.button("My Submissions", use_container_width=True):
        go_to("submissions")

    st.divider()
    all_qs = load_questions()
    solved_count = sum(1 for q in all_qs if is_solved(q["id"]))
    c1, c2 = st.columns(2)
    c1.metric("Problems", len(all_qs))
    c2.metric("Solved", solved_count)

    st.divider()


# ----------------------------------------------------------------------------
# View: Problems list
# ----------------------------------------------------------------------------
def render_problems():
    st.title("Problem List")

    questions = load_questions()
    if not questions:
        st.info(
            "No questions in the bank yet. Head to **Upload Question Bank** "
            "in the sidebar to add some, or use the bundled sample set."
        )
        return

    # --- filters ---
    fc1, fc2, fc3 = st.columns([2, 1, 1])
    with fc1:
        search = st.text_input("Search by title or tag", "")
    with fc2:
        diff_filter = st.multiselect("Difficulty", ["Easy", "Medium", "Hard"], default=[])
    with fc3:
        all_tags = sorted({t for q in questions for t in q.get("tags", [])})
        tag_filter = st.multiselect("Tags", all_tags, default=[])

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

    # --- table header ---
    h1, h2, h3, h4, h5, h6 = st.columns([0.5, 3, 1, 0.8, 1.5, 1])
    h1.markdown("**Status**")
    h2.markdown("**Title**")
    h3.markdown("**Difficulty**")
    h4.markdown("**Type**")
    h5.markdown("**Tags**")
    h6.markdown("**Action**")

    for q in filtered:
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
        if c6.button("Solve →", key=f"solve_{q['id']}"):
            st.session_state.editor_code.setdefault(q["id"], q.get("starter_code", ""))
            go_to("solve", q["id"])
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
        for q in questions:
            c1, c2, c3 = st.columns([3, 1, 1])
            c1.write(f"**{q['title']}**  ·  `{q['id']}`  ·  {q['difficulty']}")
            if q.get("type") == "mcq":
                c2.write(f"MCQ · {len(q.get('options', []))} options")
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

    if q is None:
        st.error("Question not found. It may have been deleted.")
        if st.button("← Back to problems"):
            go_to("problems")
            st.rerun()
        return

    if st.button("← Back to problems"):
        go_to("problems")
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
            st.session_state.submissions.setdefault(qid, []).append(
                {
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "language": "MCQ",
                    "passed_count": 1 if is_correct else 0,
                    "total_count": 1,
                    "passed_all": is_correct,
                    "score_pct": 100.0 if is_correct else 0.0,
                }
            )

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
            st.session_state.submissions.setdefault(qid, []).append(
                {
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "language": lang,
                    "passed_count": result["passed_count"],
                    "total_count": result["total_count"],
                    "passed_all": result["passed_all"],
                    "score_pct": result["score_pct"],
                }
            )

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
# View: Submission history
# ----------------------------------------------------------------------------
def render_submissions():
    st.title("My Submissions")

    if not st.session_state.submissions:
        st.info("You haven't submitted any solutions yet in this session.")
        return

    questions_by_id = {q["id"]: q for q in load_questions()}

    for qid, subs in st.session_state.submissions.items():
        q = questions_by_id.get(qid, {"title": qid, "difficulty": "?"})
        st.markdown(f"### {q['title']}")
        for s in reversed(subs):
            status = "Accepted" if s["passed_all"] else "Not solved"
            st.write(
                f"{status} · {s['language']} · {s['passed_count']}/{s['total_count']} "
                f"tests passed ({s['score_pct']}%) · {s['timestamp']}"
            )
        st.divider()


# ----------------------------------------------------------------------------
# Router
# ----------------------------------------------------------------------------
view = st.session_state.view
if view == "problems":
    render_problems()
elif view == "upload":
    render_upload()
elif view == "solve":
    render_solve()
elif view == "submissions":
    render_submissions()
else:
    render_problems()
