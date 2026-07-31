"""
services/email_service.py — SMTP email sending. Moved verbatim from app.py.
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Email Verification Token Store (in-memory + DB backed) — kept as-is from
# the original app.py. Not currently read anywhere else in the codebase
# (verification is actually tracked via the email_tokens DB table), but
# preserved rather than silently dropped during the move.
_verify_tokens = {}  # token -> user_id


def send_verification_email(to_email, username, token):
    """Send email verification link. Configure SMTP via env vars."""
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    from_email = os.environ.get("FROM_EMAIL", smtp_user)

    if not smtp_user or not smtp_pass:
        print(f"[Email] SMTP not configured — skip verification for {username}")
        return False

    verify_url = os.environ.get("APP_URL", "http://localhost:5000") + f"/verify_email/{token}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "DSA Tracker — Verify your email"
    msg["From"] = from_email
    msg["To"] = to_email

    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:2rem;background:#0f172a;color:#e2e8f0;border-radius:12px">
      <h2 style="color:#4d9de0">⚡ DSA Tracker</h2>
      <p>Hi <strong>{username}</strong>! Verify your email to activate your account.</p>
      <a href="{verify_url}"
         style="display:inline-block;margin:1.5rem 0;padding:.75rem 2rem;
                background:#4d9de0;color:#fff;text-decoration:none;
                border-radius:8px;font-weight:700">
        ✅ Verify Email
      </a>
      <p style="color:#64748b;font-size:.85rem">
        Link expires in 24 hours. If you did not sign up, ignore this email.
      </p>
    </div>"""

    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, to_email, msg.as_string())
        print(f"[Email] Verification sent to {to_email}")
        return True
    except Exception as e:
        print(f"[Email] Failed: {e}")
        return False
