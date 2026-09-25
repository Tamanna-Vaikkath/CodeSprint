"""
A minimal, HackerRank-style judge: runs user-submitted source code as a
subprocess, feeds it stdin from a test case, and compares stdout against
the expected output.

NOTE ON SANDBOXING: this runs code as a real subprocess on the machine
running Streamlit. That is fine for local/personal use (which is what this
project is built for) but it is NOT a hardened multi-tenant sandbox. Do not
expose this app to untrusted public users without adding real sandboxing
(e.g. Docker, gVisor, firejail, or a remote code-execution API).
"""

import os
import subprocess
import sys
import tempfile
import time

# Map of supported languages -> how to run them.
# Extend this dict to add more languages (e.g. Node.js, C++, Java) as long
# as the corresponding interpreter/compiler is installed on your machine.
LANGUAGE_CONFIG = {
    "Python 3": {
        "ext": "py",
        "run_cmd": lambda path: [sys.executable, path],
    },
}


def get_supported_languages():
    return list(LANGUAGE_CONFIG.keys())


def run_code(source_code, language, stdin_input="", timeout=5):
    """
    Execute source_code once against a single stdin_input string.
    Returns a dict: success, stdout, stderr, time_taken, timed_out
    """
    config = LANGUAGE_CONFIG.get(language)
    if not config:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Unsupported language: {language}",
            "time_taken": 0.0,
            "timed_out": False,
        }

    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = os.path.join(tmpdir, f"solution.{config['ext']}")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(source_code)

        cmd = config["run_cmd"](file_path)
        start = time.time()
        try:
            result = subprocess.run(
                cmd,
                input=stdin_input,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmpdir,
            )
            elapsed = time.time() - start
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "time_taken": round(elapsed, 3),
                "timed_out": False,
            }
        except subprocess.TimeoutExpired:
            elapsed = time.time() - start
            return {
                "success": False,
                "stdout": "",
                "stderr": "Time Limit Exceeded",
                "time_taken": round(elapsed, 3),
                "timed_out": True,
            }
        except Exception as e:  # noqa: BLE001
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Execution error: {e}",
                "time_taken": 0.0,
                "timed_out": False,
            }


def evaluate_submission(source_code, language, test_cases, timeout=5):
    """
    Run source_code against every test case.
    test_cases: list of {"input": str, "expected_output": str, "hidden": bool}
    Returns a summary dict with per-test-case results.
    """
    results = []
    for i, tc in enumerate(test_cases):
        r = run_code(source_code, language, tc.get("input", ""), timeout=timeout)
        actual = r["stdout"].strip()
        expected = str(tc.get("expected_output", "")).strip()
        passed = r["success"] and actual == expected

        results.append(
            {
                "test_case": i + 1,
                "input": tc.get("input", ""),
                "expected_output": expected,
                "actual_output": actual,
                "passed": passed,
                "stderr": r["stderr"],
                "time_taken": r["time_taken"],
                "timed_out": r["timed_out"],
                "hidden": tc.get("hidden", False),
            }
        )

    passed_count = sum(1 for r in results if r["passed"])
    total_count = len(results)
    return {
        "results": results,
        "passed_count": passed_count,
        "total_count": total_count,
        "passed_all": total_count > 0 and passed_count == total_count,
        "score_pct": round(100 * passed_count / total_count, 1) if total_count else 0.0,
    }
