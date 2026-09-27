from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from app.models.models import UserRole


class SignupRequest(BaseModel):
    name: str
    email: EmailStr
    password: str = Field(min_length=8)
    role: UserRole = UserRole.student
    discipline: Optional[str] = None       # for students
    college: Optional[str] = None          # for students
    designation: Optional[str] = None      # for recruiters


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: UserRole
    name: str
    email: EmailStr


class MessageResponse(BaseModel):
    message: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    password: str = Field(min_length=8)


class ResendVerificationRequest(BaseModel):
    email: EmailStr
