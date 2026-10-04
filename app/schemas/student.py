from datetime import datetime
from typing import Optional, Literal
from uuid import UUID
from pydantic import BaseModel, Field

from app.schemas.assessment import LanguageScoreSchema


# ---------- Documents ----------
class DocumentUploadResponse(BaseModel):
    id: UUID
    type: str
    file_path: str
    original_filename: str
    verification_status: str
    message: str


class DocumentListItem(BaseModel):
    id: UUID
    type: str
    file_path: str
    verification_status: str
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Skills ----------
class SkillCreate(BaseModel):
    name: str
    proficiency: int = Field(default=50, ge=0, le=100)
    source: Literal["project", "certificate", "internship", "manual"] = "manual"


class SkillResponse(BaseModel):
    id: UUID
    name: str
    proficiency: int
    source: str

    class Config:
        from_attributes = True


# ---------- Projects ----------
class ProjectCreate(BaseModel):
    title: str
    description: Optional[str] = None
    tech_stack: Optional[str] = None
    github_url: Optional[str] = None


class ProjectResponse(BaseModel):
    id: UUID
    title: str
    description: Optional[str]
    tech_stack: Optional[str]
    github_url: Optional[str]

    class Config:
        from_attributes = True


# ---------- Profile ----------
class DocumentBrief(BaseModel):
    id: UUID
    type: str
    file_path: str
    verification_status: str
    verification_details: Optional[dict] = None

    class Config:
        from_attributes = True


class SkillBrief(BaseModel):
    name: str
    proficiency: int
    source: str

    class Config:
        from_attributes = True


class ProjectBrief(BaseModel):
    title: str
    description: Optional[str]
    tech_stack: Optional[str]
    github_url: Optional[str]

    class Config:
        from_attributes = True


class StudentProfileResponse(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    discipline: Optional[str]
    college: Optional[str] = None
    # Not yet in the database — kept here as nullable placeholders so the frontend
    # can already build against this shape; wire these up when the columns are added.
    year_of_study: Optional[int] = None
    cgpa: Optional[float] = None
    github_url: Optional[str] = None
    github_username: Optional[str] = None
    linkedin_url: Optional[str] = None
    leetcode_url: Optional[str] = None
    leetcode_username: Optional[str] = None
    leetcode_rating: Optional[int] = None
    portfolio_url: Optional[str] = None
    summary: Optional[str] = None
    profile_photo_url: Optional[str] = None
    documents: list[DocumentBrief]
    skills: list[SkillBrief]
    projects: list[ProjectBrief]
    verified_documents_count: int = 0
    language_scores: list[LanguageScoreSchema] = []


class UpdateExternalProfilesRequest(BaseModel):
    college: Optional[str] = None
    github_url: Optional[str] = None
    linkedin_url: Optional[str] = None
    leetcode_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    summary: Optional[str] = None


class PhotoUploadResponse(BaseModel):
    profile_photo_url: str
    message: str
