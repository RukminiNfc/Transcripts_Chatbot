"""
Test the SMTP configuration and report the REAL error.

Why this exists: notification_service records only "SMTP send failed — check SMTP settings and
the server log" for every failure, so three very different problems look identical in the UI:

    535 Authentication Failed            wrong credentials, or wrong ZeptoMail region
    553 Sender is not allowed to relay   sender domain not verified with the provider
    Connection refused / timeout         wrong host or port

This checks each stage separately so the failing one is obvious.

Usage (from the Backend/ directory):
    python test_smtp.py                          # connect + authenticate only, sends nothing
    python test_smtp.py --send you@example.com   # also sends one real email
"""
from __future__ import annotations

import argparse
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.config import settings  # noqa: E402


def _masked(value: str) -> str:
    if not value:
        return "(EMPTY)"
    return f"{value[:6]}…{value[-4:]}" if len(value) > 12 else "(set)"


def _describe(exc: Exception) -> str:
    """Pull the provider's actual response out of smtplib's exception types."""
    code = getattr(exc, "smtp_code", None)
    err = getattr(exc, "smtp_error", None)
    if isinstance(err, bytes):
        err = err.decode(errors="replace")
    if code:
        return f"{code} {err or ''}".strip()
    return f"{type(exc).__name__}: {exc}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", metavar="EMAIL",
                        help="also send one real test email to this address")
    args = parser.parse_args()

    print("=" * 62)
    print("SMTP CHECK")
    print("=" * 62)
    print(f"  SMTP_HOST       {settings.SMTP_HOST or '(EMPTY)'}")
    print(f"  SMTP_PORT       {settings.SMTP_PORT}")
    print(f"  SMTP_USER       {settings.SMTP_USER or '(EMPTY)'}")
    print(f"  SMTP_PASSWORD   {_masked(settings.SMTP_PASSWORD)}")
    print(f"  SMTP_FROM_EMAIL {settings.SMTP_FROM_EMAIL or '(EMPTY)'}")

    # The app refuses to send at all unless these three are present, so check before dialling.
    missing = [n for n, v in (("SMTP_HOST", settings.SMTP_HOST),
                              ("SMTP_USER", settings.SMTP_USER),
                              ("SMTP_FROM_EMAIL", settings.SMTP_FROM_EMAIL)) if not v]
    if missing:
        print(f"\n  MISSING: {', '.join(missing)} — sending is disabled until these are set.")
        raise SystemExit(1)

    print("\n1. Connect")
    try:
        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=25)
        print("   OK")
    except Exception as exc:
        print(f"   FAILED — {_describe(exc)}")
        print("   => wrong SMTP_HOST or SMTP_PORT, or the host is unreachable.")
        raise SystemExit(1)

    print("2. STARTTLS")
    try:
        server.starttls()
        print("   OK")
    except Exception as exc:
        print(f"   FAILED — {_describe(exc)}")
        raise SystemExit(1)

    print("3. Authenticate")
    try:
        server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        print("   OK — credentials accepted")
    except Exception as exc:
        print(f"   FAILED — {_describe(exc)}")
        print("   => 535 means the username/password were rejected. For ZeptoMail check that")
        print("      SMTP_USER is literally 'emailapikey' AND that the host matches your")
        print("      account's region (smtp.zeptomail.in / .com / .eu differ).")
        raise SystemExit(1)

    if not args.send:
        print("\n" + "=" * 62)
        print("RESULT: connection and credentials are good. Nothing was sent.")
        print("Re-run with --send you@example.com to test an actual delivery,")
        print("which is what catches an unverified sender domain.")
        server.quit()
        return

    print(f"4. Send to {args.send}")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "SMTP configuration test"
    msg["From"] = settings.SMTP_FROM_EMAIL
    msg["To"] = args.send
    msg.attach(MIMEText(
        "<p>If you are reading this, the SMTP configuration works and minutes can be sent.</p>",
        "html",
    ))
    try:
        server.sendmail(settings.SMTP_FROM_EMAIL, [args.send], msg.as_string())
        print("   OK — accepted for delivery")
    except Exception as exc:
        print(f"   FAILED — {_describe(exc)}")
        print("   => 553 'not allowed to relay' means the provider will not send FROM")
        print(f"      {settings.SMTP_FROM_EMAIL}. The sending domain has to be verified in the")
        print("      provider's console (SPF/DKIM records); logging in is not the same thing.")
        server.quit()
        raise SystemExit(1)
    server.quit()

    print("\n" + "=" * 62)
    print(f"RESULT: PASSED — check {args.send}")
    print("Restart the API so it picks up these settings, then Send from the Minutes page.")


if __name__ == "__main__":
    main()
