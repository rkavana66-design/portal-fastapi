

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
