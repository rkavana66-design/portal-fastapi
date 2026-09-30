from typing import Optional, Literal, Any
from uuid import UUID
from pydantic import BaseModel


class DocumentVerificationResponse(BaseModel):
    id: UUID
    type: str
    file_path: str
    verification_status: str
    verification_details: Optional[dict[str, Any]] = None
    message: str


class ManualVerifyRequest(BaseModel):
    status: Literal["verified", "suspicious", "rejected"]
    notes: Optional[str] = None


class AdminDocumentListItem(BaseModel):
    id: UUID
    student_id: UUID
    student_name: Optional[str] = None
    type: str
    file_path: str
    verification_status: str
    verification_details: Optional[dict[str, Any]] = None
    created_at: str

    class Config:
        from_attributes = True


class GithubVerification(BaseModel):
    github_verified: bool
    github_username: Optional[str] = None
    github_public_repos: Optional[int] = None


class LeetcodeVerification(BaseModel):
    leetcode_verified: bool
    leetcode_username: Optional[str] = None
    leetcode_rating: Optional[int] = None


class LinkedinVerification(BaseModel):
    linkedin_verified: Literal["self_reported"]
    linkedin_url: Optional[str] = None


class PortfolioVerification(BaseModel):
    portfolio_verified: Literal["self_reported"]
    portfolio_url: Optional[str] = None


class ExternalProfileVerificationResponse(BaseModel):
    github: GithubVerification
    leetcode: LeetcodeVerification
    linkedin: LinkedinVerification
    portfolio: PortfolioVerification
