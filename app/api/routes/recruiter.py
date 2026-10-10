from datetime import datetime, timezone
from uuid import UUID
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.deps import require_role
from app.models.models import User, UserRole, Student, Recruiter, Skill, Interview, SearchLog
from app.schemas.student import StudentProfileResponse
from app.schemas.interview import CandidateSummary, InterviewCreate, InterviewResponse
from app.services.language_scores import compute_language_scores, top_language_scores
from app.core.config import settings

router = APIRouter(prefix="/api/recruiter", tags=["recruiter"])

UPLOAD_ROOT = Path(settings.upload_dir)
# Public address of this backend, used to build photo links that users'
# browsers can actually open.
BACKEND_BASE_URL = "https://portal-fastapi.onrender.com"


def _photo_url(student: Student) -> str | None:
    if not student.profile_photo_path:
        return None
    relative = Path(student.profile_photo_path).relative_to(UPLOAD_ROOT).as_posix()
    return f"{BACKEND_BASE_URL}/uploads/{relative}"


def get_current_recruiter(
    current_user: User = Depends(require_role(UserRole.recruiter)),
    db: Session = Depends(get_db),
) -> Recruiter:
    recruiter = db.query(Recruiter).filter(Recruiter.user_id == current_user.id).first()
    if recruiter is None:
        raise HTTPException(status_code=404, detail="Recruiter profile not found for this account")
    return recruiter


# ---------- a) SEARCH CANDIDATES ----------
@router.get("/candidates", response_model=list[CandidateSummary])
def search_candidates(
    q: str | None = None,
    discipline: str | None = None,
    min_score: float | None = None,
    recruiter: Recruiter = Depends(get_current_recruiter),
    db: Session = Depends(get_db),
):
    # Log this search for the skill-demand dashboard (Step 6) — only when
    # there's an actual query, since a bare discipline-only browse isn't a
    # meaningful "skill" signal on its own.
    if q:
        db.add(SearchLog(recruiter_id=recruiter.id, query_text=q, discipline=discipline))
        db.commit()

    query = db.query(Student)

    if discipline:
        query = query.filter(Student.discipline == discipline)

    if q:
        pattern = f"%{q}%"
        matching_student_ids = (
            db.query(Skill.student_id).filter(Skill.name.ilike(pattern)).subquery()
        )
        query = query.filter(
            or_(Student.name.ilike(pattern), Student.id.in_(matching_student_ids))
        )

    students = query.order_by(Student.name).limit(50).all()

    results: list[CandidateSummary] = []
    for student in students:
        scores = compute_language_scores(db, student.id)
        top = top_language_scores(scores, limit=3)

        if min_score is not None:
            best = max((s.percent for s in scores), default=0)
            if best < min_score:
                continue

        verified_count = sum(1 for d in student.documents if d.verification_status == "verified")
        results.append(
            CandidateSummary(
                id=student.id,
                name=student.name,
                discipline=student.discipline,
                top_skills=top,
                verified_documents_count=verified_count,
            )
        )

    return results


# ---------- b) CANDIDATE RESUME ----------
@router.get("/candidate/{student_id}/resume", response_model=StudentProfileResponse)
def get_candidate_resume(
    student_id: UUID,
    recruiter: Recruiter = Depends(get_current_recruiter),
    db: Session = Depends(get_db),
):
    student = db.query(Student).filter(Student.id == student_id).first()
    if student is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    verified_count = sum(1 for d in student.documents if d.verification_status == "verified")

    # Reuses the exact same response shape students see on their own profile —
    # a recruiter gets the same trustworthy, AI-verified resume, not a
    # separate/duplicated view that could drift out of sync.
    return StudentProfileResponse(
        id=student.id,
        user_id=student.user_id,
        name=student.name,
        discipline=student.discipline,
        college=student.college,
        year_of_study=None,
        cgpa=None,
        github_url=student.github_url,
        github_username=student.github_username,
        linkedin_url=student.linkedin_url,
        leetcode_url=student.leetcode_url,
        leetcode_username=student.leetcode_username,
        leetcode_rating=student.leetcode_rating,
        portfolio_url=student.portfolio_url,
        summary=student.summary,
        profile_photo_url=_photo_url(student),
        documents=student.documents,
        skills=student.skills,
        projects=student.projects,
        verified_documents_count=verified_count,
        language_scores=compute_language_scores(db, student.id),
    )


# ---------- c) BOOK INTERVIEW ----------
@router.post("/interviews", response_model=InterviewResponse)
def schedule_interview(
    payload: InterviewCreate,
    recruiter: Recruiter = Depends(get_current_recruiter),
    db: Session = Depends(get_db),
):
    student = db.query(Student).filter(Student.id == payload.student_id).first()
    if student is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    if payload.mode not in ("online", "offline"):
        raise HTTPException(status_code=400, detail="mode must be 'online' or 'offline'")

    scheduled_at = payload.scheduled_at
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    if scheduled_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Interview time must be in the future.")

    interview = Interview(
        recruiter_id=recruiter.id,
        student_id=student.id,
        scheduled_at=payload.scheduled_at,
        duration_minutes=payload.duration_minutes,
        mode=payload.mode,
        location_or_link=payload.location_or_link,
        notes=payload.notes,
    )
    db.add(interview)
    db.commit()
    db.refresh(interview)

    return InterviewResponse(
        id=interview.id,
        student_id=interview.student_id,
        student_name=student.name,
        scheduled_at=interview.scheduled_at,
        duration_minutes=interview.duration_minutes,
        mode=interview.mode,
        location_or_link=interview.location_or_link,
        notes=interview.notes,
        status=interview.status,
    )


# ---------- d) UPCOMING INTERVIEWS ----------
@router.get("/interviews", response_model=list[InterviewResponse])
def list_upcoming_interviews(
    recruiter: Recruiter = Depends(get_current_recruiter),
    db: Session = Depends(get_db),
):
    interviews = (
        db.query(Interview)
        .filter(Interview.recruiter_id == recruiter.id)
        .filter(Interview.status == "scheduled")
        .order_by(Interview.scheduled_at)
        .all()
    )

    results = []
    for interview in interviews:
        student = db.query(Student).filter(Student.id == interview.student_id).first()
        results.append(
            InterviewResponse(
                id=interview.id,
                student_id=interview.student_id,
                student_name=student.name if student else "Unknown candidate",
                scheduled_at=interview.scheduled_at,
                duration_minutes=interview.duration_minutes,
                mode=interview.mode,
                location_or_link=interview.location_or_link,
                notes=interview.notes,
                status=interview.status,
            )
        )
    return results
