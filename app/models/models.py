import uuid
import enum
from datetime import datetime

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Enum, Integer, Float, JSON, func
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.session import Base


class UserRole(str, enum.Enum):
    student = "student"
    recruiter = "recruiter"
    admin = "admin"


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    role = Column(Enum(UserRole, name="user_role"), nullable=False, default=UserRole.student)
    is_verified = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    student = relationship("Student", back_populates="user", uselist=False, cascade="all, delete-orphan")
    recruiter = relationship("Recruiter", back_populates="user", uselist=False, cascade="all, delete-orphan")
    email_verifications = relationship("EmailVerification", back_populates="user", cascade="all, delete-orphan")


class Student(Base):
    __tablename__ = "students"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    name = Column(String, nullable=False)
    discipline = Column(String, nullable=True)  # e.g. IT, Medical, Healthcare-Tech
    college = Column(String, nullable=True)  # e.g. "Sapthagiri NPS University" — used to recognize this student's own institution on certificates
    github_url = Column(String, nullable=True)
    linkedin_url = Column(String, nullable=True)
    leetcode_url = Column(String, nullable=True)
    portfolio_url = Column(String, nullable=True)

    github_username = Column(String, nullable=True)
    leetcode_username = Column(String, nullable=True)
    leetcode_rating = Column(Integer, nullable=True)

    summary = Column(String, nullable=True)  # short bio/description, LinkedIn-style
    profile_photo_path = Column(String, nullable=True)  # local path, like Document.file_path

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="student")
    documents = relationship("Document", back_populates="student", cascade="all, delete-orphan")
    skills = relationship("Skill", back_populates="student", cascade="all, delete-orphan")
    projects = relationship("Project", back_populates="student", cascade="all, delete-orphan")
    test_attempts = relationship("TestAttempt", back_populates="student", cascade="all, delete-orphan")
    interviews = relationship("Interview", back_populates="student", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = Column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String, nullable=False)  # "certificate" | "internship" | "other"
    file_path = Column(String, nullable=False)  # local path (or URL later, for cloud storage)
    original_filename = Column(String, nullable=False)
    verification_status = Column(String, nullable=False, default="pending")  # pending | verified | suspicious | rejected
    verification_details = Column(JSON, nullable=True)  # e.g. {"qr_ok": true, "notes": "..."}
    qr_found = Column(Boolean, nullable=True)  # quick-queryable flags, mirrored inside verification_details too
    qr_domain = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    student = relationship("Student", back_populates="documents")


class Skill(Base):
    __tablename__ = "skills"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = Column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    proficiency = Column(Integer, nullable=False, default=50)  # 0-100
    source = Column(String, nullable=False, default="manual")  # project | certificate | internship | manual
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    student = relationship("Student", back_populates="skills")


class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = Column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    tech_stack = Column(String, nullable=True)  # comma-separated, e.g. "React,Node,PostgreSQL"
    github_url = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    student = relationship("Student", back_populates="projects")


class RecruiterOrg(Base):
    __tablename__ = "recruiter_orgs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    domain = Column(String, unique=True, nullable=False, index=True)  # e.g. dabur.com
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    recruiters = relationship("Recruiter", back_populates="org")


class Recruiter(Base):
    __tablename__ = "recruiters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    org_id = Column(UUID(as_uuid=True), ForeignKey("recruiter_orgs.id", ondelete="SET NULL"), nullable=True)
    name = Column(String, nullable=False)
    designation = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="recruiter")
    org = relationship("RecruiterOrg", back_populates="recruiters")
    interviews = relationship("Interview", back_populates="recruiter", cascade="all, delete-orphan")


class EmailVerification(Base):
    """Also reused for password-reset tokens via the `purpose` column."""
    __tablename__ = "email_verifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String, nullable=False, index=True)
    purpose = Column(String, nullable=False, default="verify_email")  # verify_email | reset_password
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="email_verifications")


# ---------------------------------------------------------------------------
# Assessment module: tests, questions, timed attempts, scoring, and basic
# proctoring (tab-switch events + camera snapshots). Kept intentionally
# simple — one JSON column for answers rather than a full answers table,
# multiple-choice only, and lightweight event logging rather than deep
# video analysis. This is enough for a hackathon-grade proctored test flow
# and for computing per-language "best score" summaries on the profile.
# ---------------------------------------------------------------------------

class Test(Base):
    __tablename__ = "tests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=False)
    discipline = Column(String, nullable=False, index=True)  # IT | Medical | Commerce | Arts | Science
    category = Column(String, nullable=True)  # e.g. "Programming", "Finance"
    subcategory = Column(String, nullable=True, index=True)  # e.g. "Python", "Accounting" — used as the skill key
    topic = Column(String, nullable=True, index=True)  # fallback skill key when subcategory is null
    duration_minutes = Column(Integer, nullable=False, default=30)
    passing_percent = Column(Integer, nullable=False, default=40)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    questions = relationship("Question", back_populates="test", cascade="all, delete-orphan", order_by="Question.order_index")
    attempts = relationship("TestAttempt", back_populates="test", cascade="all, delete-orphan")


class Question(Base):
    __tablename__ = "questions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    test_id = Column(UUID(as_uuid=True), ForeignKey("tests.id", ondelete="CASCADE"), nullable=False, index=True)
    question_type = Column(String, nullable=False, default="mcq")  # "mcq" | "coding"
    text = Column(String, nullable=False)

    # MCQ-only fields — nullable, since coding questions don't use them
    options = Column(JSON, nullable=True)  # list[str], e.g. ["2", "4", "6", "8"]
    correct_option = Column(Integer, nullable=True)  # index into options — never sent to students

    # Coding-only fields — nullable, since MCQ questions don't use them
    starter_code = Column(String, nullable=True)  # pre-filled code shown in the editor
    language = Column(String, nullable=True, default="python")  # Piston's language name, e.g. "python"
    expected_output = Column(String, nullable=True)  # exact stdout expected for a correct solution

    marks = Column(Integer, nullable=False, default=1)
    order_index = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    test = relationship("Test", back_populates="questions")


class TestAttempt(Base):
    __tablename__ = "test_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = Column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    test_id = Column(UUID(as_uuid=True), ForeignKey("tests.id", ondelete="CASCADE"), nullable=False, index=True)

    status = Column(String, nullable=False, default="in_progress")  # in_progress | completed | disqualified | abandoned
    answers = Column(JSON, nullable=True)  # {question_id: selected_option_index}
    question_order = Column(JSON, nullable=True)  # [question_id, ...] — the shuffled order shown to this student
    option_orders = Column(JSON, nullable=True)  # {question_id: [original_option_index, ...]} — shuffled option mapping per question
    score = Column(Float, nullable=True)  # marks obtained
    total_marks = Column(Float, nullable=True)
    percent = Column(Float, nullable=True)
    disqualification_reason = Column(String, nullable=True)

    started_at = Column(DateTime(timezone=True), server_default=func.now())
    submitted_at = Column(DateTime(timezone=True), nullable=True)

    student = relationship("Student", back_populates="test_attempts")
    test = relationship("Test", back_populates="attempts")
    events = relationship("TestAttemptEvent", back_populates="attempt", cascade="all, delete-orphan")
    snapshots = relationship("TestAttemptSnapshot", back_populates="attempt", cascade="all, delete-orphan")


class TestAttemptEvent(Base):
    """Proctoring events during an attempt — tab switches, fullscreen exits, etc."""
    __tablename__ = "test_attempt_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    attempt_id = Column(UUID(as_uuid=True), ForeignKey("test_attempts.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String, nullable=False)  # "tab_switch" | "fullscreen_exit" | "camera_lost" | ...
    event_data = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    attempt = relationship("TestAttempt", back_populates="events")


class TestAttemptSnapshot(Base):
    """Periodic webcam snapshots captured during an attempt, stored like Document.file_path."""
    __tablename__ = "test_attempt_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    attempt_id = Column(UUID(as_uuid=True), ForeignKey("test_attempts.id", ondelete="CASCADE"), nullable=False, index=True)
    file_path = Column(String, nullable=False)
    captured_at = Column(DateTime(timezone=True), server_default=func.now())

    attempt = relationship("TestAttempt", back_populates="snapshots")


class Interview(Base):
    """A recruiter-booked interview slot with a specific candidate."""
    __tablename__ = "interviews"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recruiter_id = Column(UUID(as_uuid=True), ForeignKey("recruiters.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = Column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)

    scheduled_at = Column(DateTime(timezone=True), nullable=False)
    duration_minutes = Column(Integer, nullable=False, default=30)
    mode = Column(String, nullable=False, default="online")  # "online" | "offline"
    location_or_link = Column(String, nullable=True)
    notes = Column(String, nullable=True)
    status = Column(String, nullable=False, default="scheduled")  # scheduled | completed | cancelled

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    recruiter = relationship("Recruiter", back_populates="interviews")
    student = relationship("Student", back_populates="interviews")


class SearchLog(Base):
    """
    Records every candidate search a recruiter runs. Feeds the skill-demand
    dashboard (Step 6): real recruiter search terms, aggregated over time,
    to show which skills companies are actually hiring for right now.
    Nothing here is invented — it's a direct log of real search queries.
    """
    __tablename__ = "search_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recruiter_id = Column(UUID(as_uuid=True), ForeignKey("recruiters.id", ondelete="SET NULL"), nullable=True, index=True)
    query_text = Column(String, nullable=True)
    discipline = Column(String, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
