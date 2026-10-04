"""
Executes student-submitted Python code directly on this server, with strict
safety limits — not a full sandbox like a dedicated execution service, but a
deliberately constrained subprocess: a short time limit, a memory cap, no
ability to spawn further processes, and no access to this server's own files
or database. This is a genuine, understood tradeoff: meaningfully safer than
running code with no limits at all, but not as strong a guarantee as an
external, purpose-built sandbox like Piston (which, as of Feb 2026, requires
a paid/approved key — see earlier project notes for that history).

Only Python is supported this way — this server doesn't have compilers/
interpreters installed for other languages, and installing them is a bigger,
separate decision (similar in kind to this project's earlier experience
with heavy OCR dependencies exceeding what the free hosting tier can hold).

This code deliberately avoids true exec()-in-process execution (which could
crash or hang this entire backend on a bad script) — it always runs as a
separate subprocess, which the OS can reliably kill if it misbehaves.
"""

import os
import resource
import subprocess
import sys
import tempfile

RUN_TIMEOUT_SECONDS = 5
MAX_MEMORY_BYTES = 128 * 1024 * 1024  # 128 MB — enough for a short script, not for anything heavy
MAX_CODE_LENGTH = 10_000  # characters — a sane cap for a test-question answer


def _apply_resource_limits():
    """
    Runs just before the subprocess's own code starts (via subprocess's
    preexec_fn). Caps CPU time, memory, and process creation for THIS
    subprocess only — never affects the main server process.
    """
    resource.setrlimit(resource.RLIMIT_CPU, (RUN_TIMEOUT_SECONDS, RUN_TIMEOUT_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY_BYTES, MAX_MEMORY_BYTES))
    # Blocks the script from spawning further processes (e.g. a fork bomb).
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))


def run_code(language: str, code: str, stdin: str = "") -> dict:
    """
    Runs `code` and returns:
        {"stdout": str, "stderr": str, "success": bool}

    success is True only if the code ran without a runtime error AND
    finished within the time/memory limits — it says nothing about whether
    the output was "correct" for a given question; that's a separate check.
    """
    if language != "python":
        return {
            "stdout": "",
            "stderr": f"This server can currently only run Python code (got: {language}).",
            "success": False,
        }

    if len(code) > MAX_CODE_LENGTH:
        return {
            "stdout": "",
            "stderr": "Code is too long.",
            "success": False,
        }

    # Write the student's code to a real temporary file rather than piping
    # it in, so error messages/tracebacks reference a real, readable path
    # instead of <stdin>.
    with tempfile.TemporaryDirectory() as tmp_dir:
        script_path = os.path.join(tmp_dir, "submission.py")
        with open(script_path, "w") as f:
            f.write(code)

        try:
            result = subprocess.run(
                [sys.executable, script_path],
                input=stdin,
                capture_output=True,
                text=True,
                timeout=RUN_TIMEOUT_SECONDS,
                cwd=tmp_dir,
                preexec_fn=_apply_resource_limits,
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "success": result.returncode == 0,
            }
        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": f"Code took longer than {RUN_TIMEOUT_SECONDS} seconds to run and was stopped.",
                "success": False,
            }
        except Exception as e:
            return {
                "stdout": "",
                "stderr": f"Could not run code: {e}",
                "success": False,
            }


def check_output_match(actual_stdout: str, expected_output: str) -> bool:
    """
    Compares actual program output to the expected output for a coding
    question. Trims trailing whitespace/newlines from both sides, since
    students' print statements commonly differ only in a trailing newline —
    that shouldn't count as wrong.
    """
    return actual_stdout.rstrip() == (expected_output or "").rstrip()
