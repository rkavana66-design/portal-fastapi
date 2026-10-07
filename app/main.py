import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.db.session import Base, engine, SessionLocal
from app.models import models  # noqa: F401 — ensures models are registered before create_all
from app.models.models import User, UserRole
from app.api.routes import auth, examples, student, verification, assessment, recruiter, insights
from app.core.config import settings
from app.core.security import hash_password

ADMIN_EMAIL = "admin@setuportal.org"

app = FastAPI(title="Placement Portal — Auth Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.client_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# For a hackathon, create_all is fine to get moving fast.
# Switch to Alembic migrations (`alembic upgrade head`) once the schema stabilizes.
Base.metadata.create_all(bind=engine)

# Serves uploaded files (profile photos, certificates) at /uploads/...
app.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")

app.include_router(auth.router)
app.include_router(examples.router)
app.include_router(student.router)
app.include_router(verification.router)
app.include_router(assessment.router)
app.include_router(recruiter.router)
app.include_router(insights.router)


@app.on_event("startup")
def one_time_setup_from_env():
    """
    One-time setup driven by environment variables, so secrets never appear in
    code or chat. Set the variables on Render, let the service restart, check
    the logs, then DELETE the variables.

    ADMIN_NEW_PASSWORD (min 12 characters): creates the admin account if it
        doesn't exist, or changes its password if it does.
    SEED_ASSESSMENT_DATA=1: loads the sample assessment tests. Safe to repeat;
        tests that already exist are skipped.
    """
    new_password = os.environ.get("ADMIN_NEW_PASSWORD")
    if new_password:
        if len(new_password) < 12:
            print("ADMIN_NEW_PASSWORD ignored: it must be at least 12 characters.", flush=True)
        else:
            db = SessionLocal()
            try:
                admin_user = db.query(User).filter(User.email == ADMIN_EMAIL).first()
                if admin_user is None:
                    db.add(User(
                        email=ADMIN_EMAIL,
                        password_hash=hash_password(new_password),
                        role=UserRole.admin,
                        is_verified=True,
                    ))
                    db.commit()
                    print("Admin account created. Now delete ADMIN_NEW_PASSWORD on Render.", flush=True)
                elif admin_user.role == UserRole.admin:
                    admin_user.password_hash = hash_password(new_password)
                    db.commit()
                    print("Admin password updated. Now delete ADMIN_NEW_PASSWORD on Render.", flush=True)
                else:
                    print("ADMIN_NEW_PASSWORD ignored: that email belongs to a non-admin account.", flush=True)
            finally:
                db.close()

    if os.environ.get("SEED_ASSESSMENT_DATA") == "1":
        try:
            from scripts.seed_assessment_data import seed
            seed()
            print("Assessment tests loaded. Now delete SEED_ASSESSMENT_DATA on Render.", flush=True)
        except Exception as e:
            print(f"Seeding failed: {e}", flush=True)


@app.get("/api/health")
def health():
    return {"status": "ok"}
