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


@app.get("/api/temp-fix-admin-email/{secret}")
def temp_fix_admin_email(secret: str):
    if secret != "fix-admin-55512":
        return {"error": "wrong secret"}
    from app.db.session import SessionLocal
    from app.models.models import User

    db = SessionLocal()
    try:
        admin_user = db.query(User).filter(User.email == "admin@setu.local").first()
        if admin_user is None:
            return {"error": "No admin account found with the old email."}
        admin_user.email = "admin@setuportal.org"
        db.commit()
        return {"status": "Admin email updated.", "new_email": "admin@setuportal.org"}
    finally:
        db.close()


@app.get("/api/temp-seed-tests/{secret}")
def temp_seed_tests(secret: str):
    if secret != "seed-tests-77410":
        return {"error": "wrong secret"}
    from scripts.seed_assessment_data import seed
    seed()
    return {"status": "Seeding attempted — check logs for details, or check /api/assessment/tests afterward."}
