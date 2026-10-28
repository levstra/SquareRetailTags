"""Cognito custom message trigger: Levstra-styled emails.

Deployed as its own Lambda (`infra/prod/email.tf` zips this one file), so it
imports only the standard library. Cognito substitutes `{####}` (the code or
temporary password) and `{username}` after we return; the copy here never
contains a user's address or code, and the log line never does either.

Preview every email in a browser:
    python -m esl.accounts.cognito_emails preview <dir>
"""

import html
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger()
log.setLevel(logging.INFO)

APP_URL = os.environ.get("APP_URL", "https://app.levstra.com")
WORDMARK_URL = "https://levstra.com/email/wordmark.png"
# Half the PNG's pixel width: infra/prod/branding/email-wordmark.png is 2x.
WORDMARK_WIDTH = 104

ACCENT = "#6d3fd4"
TEXT = "#1c1726"
MUTED = "#574f68"
FOOTER = "#9a93ad"
RULE = "#e7e1f0"
FONT = "'Plus Jakarta Sans', Arial, Helvetica, sans-serif"
MONO = "'IBM Plex Mono', Consolas, 'Courier New', monospace"

INVITE = "CustomMessage_AdminCreateUser"

# trigger -> (subject, heading, paragraphs before the code, note after the code)
EMAILS = {
    INVITE: (
        "You're invited to Levstra",
        "You're invited to Levstra",
        [
            "Sign in with the email address and temporary password below; "
            "you'll choose your own password.",
            "The temporary password expires in 7 days.",
        ],
        None,
    ),
    "CustomMessage_ResendCode": (
        "Your Levstra code",
        "Here's your new code",
        ["Use it to confirm your email address."],
        None,
    ),
    "CustomMessage_ForgotPassword": (
        "Reset your Levstra password",
        "Reset your password",
        ["Use this code to choose a new password. It expires in 1 hour."],
        "Didn't ask for this? You can ignore this email; your password won't change.",
    ),
    "CustomMessage_UpdateUserAttribute": (
        "Confirm your email for Levstra",
        "Confirm your email address",
        ["Enter this code in Levstra to confirm this address."],
        None,
    ),
    "CustomMessage_VerifyUserAttribute": (
        "Confirm your email for Levstra",
        "Confirm your email address",
        ["Enter this code in Levstra to confirm this address."],
        None,
    ),
    "CustomMessage_Authentication": (
        "Your Levstra sign-in code",
        "Your sign-in code",
        ["Enter this code to finish signing in."],
        "Didn't try to sign in? Change your password.",
    ),
}


def _p(text: str, color: str = MUTED, size: int = 15) -> str:
    return (
        f'<p style="margin:0 0 14px;font-family:{FONT};font-size:{size}px;'
        f'line-height:1.6;color:{color};">{html.escape(text)}</p>'
    )


def _code(code: str) -> str:
    return (
        f'<p style="margin:6px 0 20px;font-family:{MONO};font-size:28px;font-weight:500;'
        f'letter-spacing:0.25em;color:{ACCENT};">{code}</p>'
    )


def _invite_block(code: str, username: str, app_url: str) -> str:
    label = f"font-family:{FONT};font-size:13px;color:{FOOTER};padding:0 16px 4px 0;"
    value = f"font-family:{MONO};font-size:17px;font-weight:500;color:{ACCENT};padding:0 0 4px;"
    url = html.escape(app_url, quote=True)
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:6px 0 22px;">'
        f'<tr><td style="{label}">Email</td><td style="{value}">{username}</td></tr>'
        f'<tr><td style="{label}">Temporary password</td><td style="{value}">{code}</td></tr>'
        "</table>"
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:0 0 22px;"><tr>'
        f'<td style="border-radius:8px;background:{ACCENT};">'
        f'<a href="{url}" style="display:inline-block;padding:12px 22px;font-family:{FONT};'
        'font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;'
        f'border-radius:8px;">Sign in</a></td></tr></table>'
    )


def _layout(title: str, heading: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>{html.escape(title)}</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700&amp;family=IBM+Plex+Mono:wght@500&amp;display=swap" rel="stylesheet">
</head>
<body style="margin:0;padding:0;background:#ffffff;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#ffffff;">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:520px;">
<tr><td style="padding:0 0 28px;">
<img src="{WORDMARK_URL}" width="{WORDMARK_WIDTH}" height="28" alt="Levstra" style="display:block;border:0;outline:none;text-decoration:none;font-family:{FONT};font-size:22px;font-weight:700;color:{ACCENT};">
</td></tr>
<tr><td>
<h1 style="margin:0 0 12px;font-family:{FONT};font-size:22px;font-weight:700;line-height:1.3;color:{TEXT};">{html.escape(heading)}</h1>
{body}
</td></tr>
<tr><td style="padding:20px 0 0;border-top:1px solid {RULE};">
<p style="margin:0 0 6px;font-family:{FONT};font-size:12px;line-height:1.5;color:{FOOTER};">Levstra &middot; Electronic shelf labels</p>
<p style="margin:0;font-family:{FONT};font-size:12px;line-height:1.5;color:{FOOTER};">You're receiving this because an account for this address exists at levstra.com.</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""


def render(trigger: str, code: str, username: str | None, app_url: str) -> tuple[str, str] | None:
    """(subject, html) for a Cognito trigger, or None to leave Cognito's default."""
    entry = EMAILS.get(trigger)
    if entry is None:
        return None
    subject, heading, paragraphs, note = entry
    body = "".join(_p(text) for text in paragraphs)
    if trigger == INVITE:
        body += _invite_block(code, username or "", app_url)
    else:
        body += _code(code)
    if note:
        body += _p(note, color=FOOTER, size=13)
    return subject, _layout(subject, heading, body)


def handler(event: dict, context: object) -> dict:
    trigger = event.get("triggerSource", "")
    log.info("trigger_source=%s", trigger)
    request = event.get("request") or {}
    rendered = render(
        trigger,
        request.get("codeParameter", "{####}"),
        request.get("usernameParameter"),
        APP_URL,
    )
    if rendered is not None:
        subject, message = rendered
        event.setdefault("response", {})
        event["response"]["emailSubject"] = subject
        event["response"]["emailMessage"] = message
    return event


def main(argv: list[str]) -> None:
    if len(argv) != 2 or argv[0] != "preview":
        sys.exit("usage: python -m esl.accounts.cognito_emails preview <dir>")
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for trigger in EMAILS:
        _, message = render(trigger, "482913", "you@example.com", APP_URL)
        (out / f"{trigger}.html").write_text(message, encoding="utf-8")
    print(f"wrote {len(EMAILS)} emails to {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
