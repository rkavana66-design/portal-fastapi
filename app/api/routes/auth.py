from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.models import User, Student, Recruiter, RecruiterOrg, EmailVerification, UserRole
from app.schemas.auth import (
    SignupRequest, LoginRequest, TokenResponse, MessageResponse,
    ForgotPasswordRequest, ResetPasswordRequest, ResendVerificationRequest,
)
from app.core.security import (
    hash_password, verify_password, create_access_token,
    generate_raw_token, hash_token,
)
from app.utils.email import send_email, verification_email_html, reset_password_email_html
from app.core.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

FREE_EMAIL_DOMAINS = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com"}

VERIFY_TOKEN_HOURS = 24
RESET_TOKEN_HOURS = 1


def _create_verification_token(db: Session, user: User, purpose: str, hours: int) -> str:
    raw_token = generate_raw_token()
    record = EmailVerification(
        user_id=user.id,
        token_hash=hash_token(raw_token),
        purpose=purpose,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=hours),
    )
    db.add(record)
    db.commit()
    return raw_token


# ---------- SIGNUP ----------
@router.post("/signup", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, db: Session = Depends(get_db)):
    domain = payload.email.split("@")[1].lower()

    if payload.role == UserRole.recruiter and domain in FREE_EMAIL_DOMAINS:
        raise HTTPException(
            status_code=400,
            detail="Recruiters must sign up with a company email address, not a personal one.",
        )

    if db.query(User).filter(User.email == payload.email.lower()).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    user = User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_verified=False,
    )
    db.add(user)
    db.flush()  # get user.id before commit

    if payload.role == UserRole.student:
        db.add(Student(user_id=user.id, name=payload.name, discipline=payload.discipline))
    elif payload.role == UserRole.recruiter:
        org = db.query(RecruiterOrg).filter(RecruiterOrg.domain == domain).first()
        if org is None:
            # Auto-create an org record from the domain for demo purposes.
            # In production you'd likely require pre-registered/approved orgs instead.
            org = RecruiterOrg(name=domain.split(".")[0].title(), domain=domain)
            db.add(org)
            db.flush()
        db.add(Recruiter(user_id=user.id, org_id=org.id, name=payload.name, designation=payload.designation))

    db.commit()
    db.refresh(user)

    raw_token = _create_verification_token(db, user, "verify_email", VERIFY_TOKEN_HOURS)
    link = f"{settings.client_url}/verify-email/{raw_token}"
    send_email(user.email, "Verify your email — Placement Portal", verification_email_html(link, payload.name))

    return MessageResponse(message="Account created. Check your email to verify your account before logging in.")


# ---------- VERIFY EMAIL ----------
@router.get("/verify-email/{token}", response_model=MessageResponse)
def verify_email(token: str, db: Session = Depends(get_db)):
    hashed = hash_token(token)
    record = (
        db.query(EmailVerification)
        .filter(
            EmailVerification.token_hash == hashed,
            EmailVerification.purpose == "verify_email",
            EmailVerification.used_at.is_(None),
            EmailVerification.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )
    if record is None:
        raise HTTPException(status_code=400, detail="Verification link is invalid or has expired.")

    user = db.query(User).filter(User.id == record.user_id).first()
    user.is_verified = True
    record.used_at = datetime.now(timezone.utc)
    db.commit()

    return MessageResponse(message="Email verified. You can now log in.")


# ---------- RESEND VERIFICATION ----------
@router.post("/resend-verification", response_model=MessageResponse)
def resend_verification(payload: ResendVerificationRequest, db: Session = Depends(get_db)):
    generic = MessageResponse(message="If that account exists and is unverified, a new link was sent.")
    user = db.query(User).filter(User.email == payload.email.lower()).first()
    if user is None or user.is_verified:
        return generic

    raw_token = _create_verification_token(db, user, "verify_email", VERIFY_TOKEN_HOURS)
    link = f"{settings.client_url}/verify-email/{raw_token}"
    name = user.student.name if user.student else (user.recruiter.name if user.recruiter else "there")
    send_email(user.email, "Verify your email — Placement Portal", verification_email_html(link, name))
    return generic


# ---------- LOGIN ----------
@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email.lower()).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_verified:
        raise HTTPException(status_code=403, detail="Please verify your email before logging in.")

    name = user.student.name if user.student else (user.recruiter.name if user.recruiter else "")
    token = create_access_token({"sub": str(user.id), "role": user.role.value, "email": user.email})

    return TokenResponse(access_token=token, role=user.role, name=name, email=user.email)


# ---------- FORGOT PASSWORD ----------
@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    generic = MessageResponse(message="If that email is registered, a reset link has been sent.")
    user = db.query(User).filter(User.email == payload.email.lower()).first()
    if user is None:
        return generic

    raw_token = _create_verification_token(db, user, "reset_password", RESET_TOKEN_HOURS)
    link = f"{settings.client_url}/reset-password/{raw_token}"
    name = user.student.name if user.student else (user.recruiter.name if user.recruiter else "there")
    send_email(user.email, "Reset your password — Placement Portal", reset_password_email_html(link, name))
    return generic


# ---------- RESET PASSWORD ----------
@router.post("/reset-password/{token}", response_model=MessageResponse)
def reset_password(token: str, payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    hashed = hash_token(token)
    record = (
        db.query(EmailVerification)
        .filter(
            EmailVerification.token_hash == hashed,
            EmailVerification.purpose == "reset_password",
            EmailVerification.used_at.is_(None),
            EmailVerification.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )
    if record is None:
        raise HTTPException(status_code=400, detail="Reset link is invalid or has expired.")

    user = db.query(User).filter(User.id == record.user_id).first()
    user.password_hash = hash_password(payload.password)
    record.used_at = datetime.now(timezone.utc)
    db.commit()

    return MessageResponse(message="Password updated. You can now log in with your new password.")
