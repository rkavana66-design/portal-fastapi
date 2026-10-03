import uuid
import random
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.deps import require_role
from app.models.models import User, UserRole, Student, Test, Question, TestAttempt
from app.schemas.assessment import (
    TestCreate, TestResponse,
    TestListItem, StartAttemptResponse, QuestionForStudent,
    SubmitAttemptRequest, SubmitAttemptResponse,
    RunCodeRequest, RunCodeResponse,
    ProctoringEventRequest, ProctoringEventResponse,
    SnapshotUploadResponse,
    AssessmentLanguageSummaryResponse,
)
from app.core.config import settings
from app.utils.proctoring import record_event_and_check_disqualification
from app.utils.code_execution import run_code, check_output_match
from app.services.language_scores import compute_language_scores, top_language_scores
from app.services.recommendations import generate_recommendations
from app.schemas.recommendation import RecommendationsResponse

router = APIRouter(prefix="/api/assessment", tags=["assessment"])

SNAPSHOT_ROOT = Path(settings.upload_dir) / "snapshots"
SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)

MAX_SNAPSHOT_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_SNAPSHOT_TYPES = {"image/jpeg", "image/png"}


def get_current_student(
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
) -> Student:
    student = db.query(Student).filter(Student.user_id == current_user.id).first()
    if student is None:
        raise HTTPException(status_code=404, detail="Student profile not found for this account")
    return student


def _get_attempt_or_404(attempt_id: UUID, student: Student, db: Session) -> TestAttempt:
    attempt = (
        db.query(TestAttempt)
        .filter(TestAttempt.id == attempt_id)
        .filter(TestAttempt.student_id == student.id)
        .first()
    )
    if attempt is None:
        raise HTTPException(status_code=404, detail="Attempt not found")
    return attempt


# ============================================================
# Admin: create tests (simple bulk creation — a test with all
# its questions in one call, for fast seeding of demo content)
# ============================================================

@router.post("/admin/tests", response_model=TestResponse, status_code=status.HTTP_201_CREATED)
def create_test(
    payload: TestCreate,
    current_user: User = Depends(require_role(UserRole.admin)),
    db: Session = Depends(get_db),
):
    test = Test(
        title=payload.title,
        discipline=payload.discipline,
        category=payload.category,
        subcategory=payload.subcategory,
        topic=payload.topic,
        duration_minutes=payload.duration_minutes,
        passing_percent=payload.passing_percent,
    )
    db.add(test)
    db.flush()  # get test.id before creating questions

    for q in payload.questions:
        db.add(Question(
            test_id=test.id,
            question_type=q.question_type,
            text=q.text,
            options=q.options,
            correct_option=q.correct_option,
            starter_code=q.starter_code,
            language=q.language,
            expected_output=q.expected_output,
            marks=q.marks,
            order_index=q.order_index,
        ))

    db.commit()
    db.refresh(test)

    return TestResponse(
        id=test.id,
        title=test.title,
        discipline=test.discipline,
        category=test.category,
        subcategory=test.subcategory,
        topic=test.topic,
        duration_minutes=test.duration_minutes,
        passing_percent=test.passing_percent,
        is_active=test.is_active,
        question_count=len(test.questions),
    )


# ============================================================
# Student: browse, take, and submit tests
# ============================================================

@router.get("/tests", response_model=list[TestListItem])
def list_tests(
    discipline: str | None = None,
    student: Student = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    query = db.query(Test).filter(Test.is_active.is_(True))
    if discipline:
        query = query.filter(Test.discipline == discipline)
    return query.order_by(Test.title).all()


@router.post("/tests/{test_id}/start", response_model=StartAttemptResponse)
def start_test(
    test_id: UUID,
    student: Student = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    test = db.query(Test).filter(Test.id == test_id, Test.is_active.is_(True)).first()
    if test is None:
        raise HTTPException(status_code=404, detail="Test not found or not active")

    # Shuffle question order for this attempt. MCQ options are independently
    # shuffled too; coding questions have no options to shuffle, so they're
    # left as-is (only their position in the test changes).
    shuffled_questions = list(test.questions)
    random.shuffle(shuffled_questions)

    question_order = [str(q.id) for q in shuffled_questions]
    option_orders: dict[str, list[int]] = {}
    student_facing_questions = []

    for q in shuffled_questions:
        if q.question_type == "coding":
            student_facing_questions.append(
                QuestionForStudent(
                    id=q.id,
                    question_type="coding",
                    text=q.text,
                    starter_code=q.starter_code,
                    language=q.language,
                )
            )
            continue

        # original_positions[i] tells us which ORIGINAL option index is now
        # shown at shuffled position i — this is what lets us grade correctly
        # later, no matter how the options were shuffled for this student.
        original_positions = list(range(len(q.options)))
        random.shuffle(original_positions)
        shuffled_options = [q.options[i] for i in original_positions]

        option_orders[str(q.id)] = original_positions
        student_facing_questions.append(
            QuestionForStudent(id=q.id, question_type="mcq", text=q.text, options=shuffled_options)
        )

    attempt = TestAttempt(
        student_id=student.id,
        test_id=test.id,
        status="in_progress",
        question_order=question_order,
        option_orders=option_orders,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    return StartAttemptResponse(
        attempt_id=attempt.id,
        test_id=test.id,
        title=test.title,
        duration_minutes=test.duration_minutes,
        started_at=attempt.started_at,
        questions=student_facing_questions,
    )


@router.post("/attempts/{attempt_id}/run-code", response_model=RunCodeResponse)
def run_code_endpoint(
    attempt_id: UUID,
    payload: RunCodeRequest,
    student: Student = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    """
    Lets a student run their code and see the output WITHOUT submitting or
    grading anything — the "Run" button while still working on the problem,
    as opposed to final submission. Requires an active attempt so this can't
    be used as a free-standing code playground outside a real test.
    """
    attempt = _get_attempt_or_404(attempt_id, student, db)
    if attempt.status != "in_progress":
        raise HTTPException(status_code=400, detail="This attempt is no longer in progress.")

    result = run_code(payload.language, payload.code, payload.stdin or "")
    return RunCodeResponse(**result)


@router.post("/attempts/{attempt_id}/submit", response_model=SubmitAttemptResponse)
def submit_attempt(
    attempt_id: UUID,
    payload: SubmitAttemptRequest,
    student: Student = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    attempt = _get_attempt_or_404(attempt_id, student, db)

    if attempt.status != "in_progress":
        raise HTTPException(status_code=400, detail=f"This attempt is already {attempt.status}.")

    test = db.query(Test).filter(Test.id == attempt.test_id).first()
    questions = {str(q.id): q for q in test.questions}
    option_orders = attempt.option_orders or {}

    total_marks = sum(q.marks for q in questions.values())
    score = 0

    # ---- Grade MCQ answers ----
    for question_id, selected_shuffled_index in payload.answers.items():
        question = questions.get(question_id)
        if question is None or question.question_type != "mcq":
            continue
        # The student selected a position in THEIR shuffled option list —
        # translate it back to the original option index before comparing,
        # since correct_option always refers to the original, unshuffled order.
        positions = option_orders.get(question_id)
        if positions and 0 <= selected_shuffled_index < len(positions):
            actual_original_index = positions[selected_shuffled_index]
        else:
            actual_original_index = selected_shuffled_index

        if actual_original_index == question.correct_option:
            score += question.marks

    # ---- Grade coding answers: actually run the code via Piston and compare output ----
    for question_id, submitted_code in payload.code_answers.items():
        question = questions.get(question_id)
        if question is None or question.question_type != "coding":
            continue
        result = run_code(question.language or "python", submitted_code)
        if result["success"] and check_output_match(result["stdout"], question.expected_output or ""):
            score += question.marks

    percent = round((score / total_marks) * 100, 1) if total_marks > 0 else 0.0

    # Store both answer types together so the attempt record is complete.
    attempt.answers = {**payload.answers, **{k: v for k, v in payload.code_answers.items()}}
    attempt.score = float(score)
    attempt.total_marks = float(total_marks)
    attempt.percent = percent
    attempt.status = "completed"
    attempt.submitted_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(attempt)

    return SubmitAttemptResponse(
        attempt_id=attempt.id,
        status=attempt.status,
        score=attempt.score,
        total_marks=attempt.total_marks,
        percent=attempt.percent,
        passed=attempt.percent >= test.passing_percent,
    )


# ============================================================
# Proctoring: tab-switch / fullscreen events and camera snapshots
# ============================================================

@router.post("/attempts/{attempt_id}/event", response_model=ProctoringEventResponse)
def log_proctoring_event(
    attempt_id: UUID,
    payload: ProctoringEventRequest,
    student: Student = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    attempt = _get_attempt_or_404(attempt_id, student, db)

    if attempt.status != "in_progress":
        # Attempt already finished/disqualified — nothing more to enforce,
        # but we still record the event for the audit trail.
        disqualified, tab_switch_count = False, 0
    else:
        disqualified, tab_switch_count = record_event_and_check_disqualification(
            db, attempt, payload.event_type, payload.event_data
        )

    return ProctoringEventResponse(
        logged=True, disqualified=disqualified, tab_switch_count=tab_switch_count
    )


@router.post("/attempts/{attempt_id}/snapshot", response_model=SnapshotUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_snapshot(
    attempt_id: UUID,
    file: UploadFile = File(...),
    student: Student = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    attempt = _get_attempt_or_404(attempt_id, student, db)

    if file.content_type not in ALLOWED_SNAPSHOT_TYPES:
        raise HTTPException(status_code=400, detail="Only JPEG or PNG snapshots are accepted.")

    contents = await file.read()
    if len(contents) > MAX_SNAPSHOT_SIZE:
        raise HTTPException(status_code=400, detail="Snapshot exceeds the 5MB size limit.")

    ext = ".png" if file.content_type == "image/png" else ".jpg"
    attempt_dir = SNAPSHOT_ROOT / str(attempt.id)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    stored_path = attempt_dir / f"{uuid.uuid4().hex}{ext}"

    with open(stored_path, "wb") as f:
        f.write(contents)

    from app.models.models import TestAttemptSnapshot
    snapshot = TestAttemptSnapshot(attempt_id=attempt.id, file_path=str(stored_path))
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)

    return SnapshotUploadResponse(
        id=snapshot.id, captured_at=snapshot.captured_at, message="Snapshot stored."
    )


# ============================================================
# Profile summary: best score per language/domain
# ============================================================

@router.get("/profile/language-scores", response_model=AssessmentLanguageSummaryResponse)
def get_profile_language_scores(
    student: Student = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    scores = compute_language_scores(db, student.id)
    top_scores = top_language_scores(scores)
    return AssessmentLanguageSummaryResponse(scores=scores, top_scores=top_scores)


# ============================================================
# Improvement recommendations — rule-based, derived from real
# scores. See app/services/recommendations.py for the logic.
# ============================================================

@router.get("/profile/recommendations", response_model=RecommendationsResponse)
def get_profile_recommendations(
    student: Student = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    scores = compute_language_scores(db, student.id)
    recommendations = generate_recommendations(scores)
    return RecommendationsResponse(recommendations=recommendations)

