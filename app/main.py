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


@app.get("/api/temp-add-college-column/{secret}")
def temp_add_college_column(secret: str):
    if secret != "add-college-12345":
        return {"error": "wrong secret"}
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE students ADD COLUMN IF NOT EXISTS college VARCHAR"))
        conn.commit()
    return {"status": "college column added (or already existed) — safe, no data was touched"}
