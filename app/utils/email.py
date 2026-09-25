import httpx

from app.core.config import settings


def send_email(to: str, subject: str, html: str) -> None:
    try:
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={
                "from": settings.email_from,
                "to": [to],
                "subject": subject,
                "html": html,
            },
            timeout=10,
        )
        response.raise_for_status()
    except Exception as e:
        # Never let an email failure crash the request that triggered it —
        # same resilience pattern used elsewhere in this backend.
        print(f"[EMAIL] Failed to send to {to}: {e}")


def verification_email_html(link: str, name: str) -> str:
    return f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: auto;">
        <h2>Confirm your email</h2>
        <p>Hi {name}, welcome to the Placement Portal. Click below to verify your account:</p>
        <p><a href="{link}" style="background:#2563eb;color:#fff;padding:10px 18px;
            border-radius:6px;text-decoration:none;display:inline-block;">Verify Email</a></p>
        <p>This link expires in 24 hours. If you didn't create this account, ignore this email.</p>
    </div>"""


def reset_password_email_html(link: str, name: str) -> str:
    return f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: auto;">
        <h2>Reset your password</h2>
        <p>Hi {name}, click below to set a new password:</p>
        <p><a href="{link}" style="background:#2563eb;color:#fff;padding:10px 18px;
            border-radius:6px;text-decoration:none;display:inline-block;">Reset Password</a></p>
        <p>This link expires in 1 hour. If you didn't request this, ignore this email.</p>
    </div>"""
