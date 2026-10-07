"""
Executes student-submitted Python code directly on this server.

HONEST LIMITS — read before relying on this:
- The code runs as a separate subprocess with a time limit, a memory cap,
  and a block on spawning further processes. That stops runaway or
  fork-bomb style code from hanging or crashing the backend.
- It runs with an EMPTY environment, so it cannot read this server's
  secrets (database URL, JWT secret, API keys) from environment variables,
  and in Python's isolated mode (-I) so it ignores PYTHON* settings.
- It is NOT a real sandbox. The code still runs as the same operating-system
  user on the same machine, so it can read files on disk, including other
  students' uploaded documents and this application's source code. Closing
  that needs real isolation (a container or a dedicated execution service).
  Do not treat this as safe for untrusted strangers once real users have
  uploaded documents.

Only Python is supported — this server has no other interpreters installed.
It always runs as a separate subprocess (never exec() in-process), which the
OS can reliably kill if it misbehaves.
"""

import os
import resource
import subprocess
import sys
import tempfile

RUN_TIMEOUT_SECONDS = 5
MAX_MEMORY_BYTES = 128 * 1024 * 1024  # 128 MB — enough for a short script, not for anything heavy
MAX_CODE_LENGTH = 10_000  # characters — a sane cap for a test-question answer

# The ONLY environment the student's code gets. Deliberately tiny: no
# DATABASE_URL, JWT_SECRET, RESEND_API_KEY, SMTP_PASS or anything else.
SAFE_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}


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
                [sys.executable, "-I", script_path],
                input=stdin,
                capture_output=True,
                text=True,
                timeout=RUN_TIMEOUT_SECONDS,
                cwd=tmp_dir,
                env=SAFE_ENV,
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
