from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ---------- Admin: creating tests ----------
class QuestionCreate(BaseModel):
    question_type: str = "mcq"  # "mcq" | "coding"
    text: str
    # MCQ fields
    options: Optional[list[str]] = None
    correct_option: Optional[int] = None
    # Coding fields
    starter_code: Optional[str] = None
    language: Optional[str] = "python"
    expected_output: Optional[str] = None

    marks: int = 1
    order_index: int = 0


class TestCreate(BaseModel):
    title: str
    discipline: str  # IT | Medical | Commerce | Arts | Science
    category: Optional[str] = None
    subcategory: Optional[str] = None  # skill key, e.g. "Python"
    topic: Optional[str] = None  # fallback skill key if subcategory is null
    duration_minutes: int = 30
    passing_percent: int = 40
    questions: list[QuestionCreate] = Field(min_length=1)


class TestResponse(BaseModel):
    id: UUID
    title: str
    discipline: str
    category: Optional[str]
    subcategory: Optional[str]
    topic: Optional[str]
    duration_minutes: int
    passing_percent: int
    is_active: bool
    question_count: int

    class Config:
        from_attributes = True


# ---------- Student: listing & taking tests ----------
class TestListItem(BaseModel):
    id: UUID
    title: str
    discipline: str
    category: Optional[str]
    subcategory: Optional[str]
    topic: Optional[str]
    duration_minutes: int
    passing_percent: int

    class Config:
        from_attributes = True


class QuestionForStudent(BaseModel):
    """Never includes correct_option or expected_output — this is what the student sees while taking the test."""
    id: UUID
    question_type: str = "mcq"
    text: str
    options: Optional[list[str]] = None  # only present for mcq questions
    starter_code: Optional[str] = None  # only present for coding questions
    language: Optional[str] = None  # only present for coding questions

    class Config:
        from_attributes = True


class StartAttemptResponse(BaseModel):
    attempt_id: UUID
    test_id: UUID
    title: str
    duration_minutes: int
    started_at: datetime
    questions: list[QuestionForStudent]


class SubmitAttemptRequest(BaseModel):
    answers: dict[str, int] = {}  # {question_id: selected_option_index} — mcq questions only
    code_answers: dict[str, str] = {}  # {question_id: submitted_code} — coding questions only


class SubmitAttemptResponse(BaseModel):
    attempt_id: UUID
    status: str
    score: float
    total_marks: float
    percent: float
    passed: bool


class RunCodeRequest(BaseModel):
    """For the 'Run' button while taking a test — executes code without grading or saving it."""
    language: str
    code: str
    stdin: Optional[str] = None


class RunCodeResponse(BaseModel):
    stdout: str
    stderr: str
    success: bool  # true if the code ran without a runtime error (NOT whether the output was "correct")


class ProctoringEventRequest(BaseModel):
    event_type: str  # "tab_switch" | "fullscreen_exit" | "camera_lost" | ...
    event_data: Optional[dict] = None


class ProctoringEventResponse(BaseModel):
    logged: bool
    disqualified: bool
    tab_switch_count: int


class SnapshotUploadResponse(BaseModel):
    id: UUID
    captured_at: datetime
    message: str


# ---------- Language / domain score summary ----------
class LanguageScoreSchema(BaseModel):
    language: str        # e.g., "Python", "Java", "Accounting", "Anatomy"
    percent: float        # composite percent — see `sources` for what it's built from
    tests_taken: int      # number of completed assessment attempts for this language

    # Transparency fields: exactly which real signals fed into `percent`,
    # and their individual raw values, so nothing is a black-box number.
    test_percent: Optional[float] = None       # best verified test score, if any test was taken
    skill_percent: Optional[float] = None      # self-reported skill proficiency, if listed
    project_count: int = 0                     # number of real projects using this language
    sources: list[str] = []                    # e.g. ["test", "skill", "projects"]


class AssessmentLanguageSummaryResponse(BaseModel):
    scores: list[LanguageScoreSchema]
    top_scores: list[LanguageScoreSchema]  # top 5 by percent, for resume preview
