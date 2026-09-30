from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.db.session import Base, engine
from app.models import models  # noqa: F401 — ensures models are registered before create_all
from app.api.routes import auth, examples, student, verification, assessment, recruiter, insights
from app.core.config import settings

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

# Serves uploaded files (currently: profile photos) at http://127.0.0.1:8000/uploads/...
app.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")

app.include_router(auth.router)
app.include_router(examples.router)
app.include_router(student.router)
app.include_router(verification.router)
app.include_router(assessment.router)
app.include_router(recruiter.router)
app.include_router(insights.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/temp-create-admin/{secret}")
def temp_create_admin(secret: str):
    if secret != "make-admin-98765":
        return {"error": "wrong secret"}
    from app.db.session import SessionLocal
    from app.models.models import User, UserRole
    from app.core.security import hash_password

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == "admin@setu.local").first()
        if existing:
            return {"status": "Admin already exists.", "email": "admin@setu.local"}

        admin_user = User(
            email="admin@setu.local",
            password_hash=hash_password("ChangeThisPassword123"),
            role=UserRole.admin,
            is_verified=True,
        )
        db.add(admin_user)
        db.commit()
        return {
            "status": "Admin account created.",
            "email": "admin@setu.local",
            "password": "ChangeThisPassword123",
            "note": "Log in with these, then we'll delete this endpoint.",
        }
    finally:
        db.close()
