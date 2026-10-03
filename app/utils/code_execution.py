"""
Executes student-submitted code via Piston (https://github.com/engineer-man/piston),
a free, public code-execution API. Code runs entirely on Piston's own servers, in
their sandbox, completely separate from this backend. This means a student's code
(even buggy or resource-heavy code) can never affect or crash this server — a
deliberate choice after this project's earlier experience with heavy processing
(OCR) exceeding this server's own resources.

IMPORTANT, as of Feb 2026: Piston's public API is no longer freely available
without an approved key (see https://github.com/engineer-man/piston). Until a
paid/approved execution service is configured, run_code() below will fail
gracefully — returning a clear, honest message rather than crashing or exposing
a raw technical error to students. The code editor, question, and submission
flow all still work; only the actual execution is blocked.
"""

import httpx

PISTON_API_URL = "https://emkc.org/api/v2/piston/execute"

# Piston needs an exact (language, version) pair. "*" tells it to use whatever
# version it currently has installed for that language — avoids this breaking
# if Piston upgrades their runtimes later.
LANGUAGE_VERSIONS = {
    "python": "3.10.0",
    "javascript": "18.15.0",
    "java": "15.0.2",
    "c": "10.2.0",
    "cpp": "10.2.0",
}

RUN_TIMEOUT_SECONDS = 10  # how long we wait for Piston to respond


def run_code(language: str, code: str, stdin: str = "") -> dict:
    """
    Runs `code` via Piston and returns:
        {"stdout": str, "stderr": str, "success": bool}

    success is True only if the code ran without a compile/runtime error —
    it says nothing about whether the output was "correct" for a given
    question; that comparison happens separately, in the caller.
    """
    version = LANGUAGE_VERSIONS.get(language)
    if version is None:
        return {
            "stdout": "",
            "stderr": f"Unsupported language: {language}",
            "success": False,
        }

    file_extension = {
        "python": "py", "javascript": "js", "java": "java", "c": "c", "cpp": "cpp",
    }.get(language, "txt")

    payload = {
        "language": language,
        "version": version,
        "files": [{"name": f"main.{file_extension}", "content": code}],
        "stdin": stdin,
    }

    try:
        response = httpx.post(PISTON_API_URL, json=payload, timeout=RUN_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
    except Exception:
        # The public Piston API has moved to a paid/approval-only model as of
        # Feb 2026 — this is a known, honest limitation, not a bug. Shown to
        # the student as a clear message rather than a raw technical error.
        return {
            "stdout": "",
            "stderr": "Code execution is temporarily unavailable. Your code has been saved — please continue with the rest of the test.",
            "success": False,
        }

    run_result = data.get("run", {})
    stdout = run_result.get("stdout", "") or ""
    stderr = run_result.get("stderr", "") or ""
    compile_result = data.get("compile")
    compile_failed = bool(compile_result and compile_result.get("code", 0) != 0)

    success = not compile_failed and run_result.get("code", 1) == 0

    if compile_failed:
        stderr = (compile_result.get("stderr") or "") + stderr

    return {"stdout": stdout, "stderr": stderr, "success": success}


def check_output_match(actual_stdout: str, expected_output: str) -> bool:
    """
    Compares actual program output to the expected output for a coding
    question. Trims trailing whitespace/newlines from both sides, since
    students' print statements commonly differ only in a trailing newline —
    that shouldn't count as wrong.
    """
    return actual_stdout.rstrip() == (expected_output or "").rstrip()
