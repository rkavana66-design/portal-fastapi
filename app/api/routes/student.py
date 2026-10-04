import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.deps import require_role
from app.models.models import User, UserRole, Student, Document, Skill, Project
from app.schemas.student import (
    DocumentUploadResponse, DocumentListItem,
    SkillCreate, SkillResponse,
    ProjectCreate, ProjectResponse,
    StudentProfileResponse, UpdateExternalProfilesRequest,
    PhotoUploadResponse,
)
from app.core.config import settings
from app.services.language_scores import compute_language_scores

router = APIRouter(prefix="/api/student", tags=["student"])

UPLOAD_ROOT = Path(settings.upload_dir)
UPLOAD_ROOT.mkdir(exist_ok=True)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_CONTENT_TYPES = {"application/pdf"}
ALLOWED_DOC_TYPES = {"certificate", "internship", "other"}

MAX_PHOTO_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_PHOTO_TYPES = {"image/jpeg", "image/png"}
# Hardcoded to match this project's fixed local dev setup (same base URL
# used everywhere else in this app, e.g. lib/api.ts's BASE_URL on the
# frontend). Move to settings if you ever deploy somewhere else.
BACKEND_BASE_URL = "http://127.0.0.1:8000"


def _photo_url(student: Student) -> str | None:
    if not student.profile_photo_path:
        return None
    # profile_photo_path is stored as an absolute local path (like
    # Document.file_path) rooted at UPLOAD_ROOT; convert it to the public
    # /uploads/... URL served by the StaticFiles mount in main.py.
    relative = Path(student.profile_photo_path).relative_to(UPLOAD_ROOT).as_posix()
    return f"{BACKEND_BASE_URL}/uploads/{relative}"


def get_current_student(
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
) -> Student:
    """
    require_role already guarantees current_user.role == student; this just
    fetches the linked Student row (or fails clearly if one is somehow missing).
    """
    student = db.query(Student).filter(Student.user_id == current_user.id).first()
    if student is None:
        raise HTTPException(status_code=404, detail="Student profile not found for this account")
    return student


# ---------- a) GET PROFILE ----------
@router.get("/profile", response_model=StudentProfileResponse)
def get_profile(
    student: Student = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    verified_count = sum(1 for d in student.documents if d.verification_status == "verified")
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


# ---------- a2) UPDATE EXTERNAL PROFILE LINKS (and college) ----------
@router.put("/profile/external", response_model=StudentProfileResponse)
def update_external_profiles(
    payload: UpdateExternalProfilesRequest,
    student: Student = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    if payload.college is not None:
        # Empty/whitespace-only input clears the field rather than saving a
        # blank string that would never match anything on a certificate.
        student.college = payload.college.strip() or None
    if payload.github_url is not None:
        student.github_url = payload.github_url
    if payload.linkedin_url is not None:
        student.linkedin_url = payload.linkedin_url
    if payload.leetcode_url is not None:
        student.leetcode_url = payload.leetcode_url
    if payload.portfolio_url is not None:
        student.portfolio_url = payload.portfolio_url
    if payload.summary is not None:
        student.summary = payload.summary

    db.commit()
    db.refresh(student)

    verified_count = sum(1 for d in student.documents if d.verification_status == "verified")
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


# ---------- a3) UPLOAD PROFILE PHOTO ----------
@router.post("/profile/photo", response_model=PhotoUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_profile_photo(
    file: UploadFile = File(...),
    student: Student = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    if file.content_type not in ALLOWED_PHOTO_TYPES:
        raise HTTPException(status_code=400, detail="Only JPEG or PNG images are accepted.")

    contents = await file.read()
    if len(contents) > MAX_PHOTO_SIZE:
        raise HTTPException(status_code=400, detail="Photo exceeds the 5MB size limit.")

    photo_dir = UPLOAD_ROOT / "profile_photos" / str(student.id)
    photo_dir.mkdir(parents=True, exist_ok=True)

    # Remove any previous photo for this student so old files don't pile up.
    if student.profile_photo_path:
        old_path = Path(student.profile_photo_path)
        if old_path.exists():
            old_path.unlink(missing_ok=True)

    ext = ".png" if file.content_type == "image/png" else ".jpg"
    stored_path = photo_dir / f"{uuid.uuid4().hex}{ext}"
    with open(stored_path, "wb") as f:
        f.write(contents)

    student.profile_photo_path = str(stored_path)
    db.commit()
    db.refresh(student)

    return PhotoUploadResponse(profile_photo_url=_photo_url(student), message="Profile photo updated.")


# ---------- b) UPLOAD DOCUMENT ----------
@router.post("/documents", response_model=DocumentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    type: str = Form(...),
    file: UploadFile = File(...),
    student: Student = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    if type not in ALLOWED_DOC_TYPES:
        raise HTTPException(status_code=400, detail=f"type must be one of {sorted(ALLOWED_DOC_TYPES)}")

    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File exceeds the 10MB size limit.")

    student_dir = UPLOAD_ROOT / "documents" / str(student.id)
    student_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{uuid.uuid4().hex}.pdf"
    file_path = student_dir / stored_filename

    with open(file_path, "wb") as f:
        f.write(contents)

    document = Document(
        student_id=student.id,
        type=type,
        file_path=str(file_path),
        original_filename=file.filename,
        verification_status="pending",
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    return DocumentUploadResponse(
        id=document.id,
        type=document.type,
        file_path=document.file_path,
        original_filename=document.original_filename,
        verification_status=document.verification_status,
        message="Document uploaded successfully. Verification is pending.",
    )


# ---------- e) LIST DOCUMENTS ----------
@router.get("/documents", response_model=list[DocumentListItem])
def list_documents(student: Student = Depends(get_current_student)):
    return student.documents


# ---------- c) ADD SKILL ----------
@router.post("/skills", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
def add_skill(
    payload: SkillCreate,
    student: Student = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    skill = Skill(
        student_id=student.id,
        name=payload.name,
        proficiency=payload.proficiency,
        source=payload.source,
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    return skill


# ---------- d) ADD PROJECT ----------
@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def add_project(
    payload: ProjectCreate,
    student: Student = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    project = Project(
        student_id=student.id,
        title=payload.title,
        description=payload.description,
        tech_stack=payload.tech_stack,
        github_url=payload.github_url,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project
