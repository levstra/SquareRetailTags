import ast
from pathlib import Path

import pytest

from esl.accounts import cognito_emails as ce

MAPPED = {
    "CustomMessage_AdminCreateUser": "You're invited to Levstra",
    "CustomMessage_ResendCode": "Your Levstra code",
    "CustomMessage_ForgotPassword": "Reset your Levstra password",
    "CustomMessage_UpdateUserAttribute": "Confirm your email for Levstra",
    "CustomMessage_VerifyUserAttribute": "Confirm your email for Levstra",
    "CustomMessage_Authentication": "Your Levstra sign-in code",
}


def event(trigger, username="{username}"):
    request = {"codeParameter": "{####}", "userAttributes": {"email": "a@b.co"}}
    if username is not None:
        request["usernameParameter"] = username
    return {"triggerSource": trigger, "request": request, "response": {}}


@pytest.mark.parametrize("trigger, subject", MAPPED.items())
def test_each_trigger_gets_subject_and_code(trigger, subject):
    out = ce.handler(event(trigger), None)["response"]
    assert out["emailSubject"] == subject
    assert "{####}" in out["emailMessage"]
    assert len(out["emailMessage"]) < 20_000


def test_invite_contains_username_and_sign_in_link():
    html = ce.handler(event("CustomMessage_AdminCreateUser"), None)["response"]["emailMessage"]
    assert "{username}" in html and "https://app.levstra.com" in html and "7 days" in html


def test_forgot_password_states_one_hour():
    html = ce.handler(event("CustomMessage_ForgotPassword"), None)["response"]["emailMessage"]
    assert "1 hour" in html


@pytest.mark.parametrize("trigger", [t for t in MAPPED if t != "CustomMessage_AdminCreateUser"])
def test_missing_username_parameter_is_fine(trigger):
    assert ce.handler(event(trigger, username=None), None)["response"]["emailSubject"]


def test_wordmark_image():
    html = ce.render("CustomMessage_ForgotPassword", "{####}", None, "https://app.levstra.com")[1]
    assert f'src="{ce.WORDMARK_URL}"' in html and 'alt="Levstra"' in html
    assert 'height="28"' in html and "width=" in html


def test_unknown_trigger_passes_through():
    e = event("CustomMessage_SignUp")
    assert ce.handler(e, None) == event("CustomMessage_SignUp")


def test_app_url_from_environment(monkeypatch):
    monkeypatch.setattr(ce, "APP_URL", "https://example.test")
    html = ce.handler(event("CustomMessage_AdminCreateUser"), None)["response"]["emailMessage"]
    assert "https://example.test" in html


def test_stdlib_only():
    tree = ast.parse(Path(ce.__file__).read_text(encoding="utf-8"))
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {
        n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    }
    assert "esl" not in names and names <= {"html", "json", "logging", "os", "sys", "pathlib"}


def test_preview_writes_one_file_per_trigger(tmp_path):
    ce.main(["preview", str(tmp_path)])
    assert {p.stem for p in tmp_path.glob("*.html")} == set(MAPPED)


def test_wordmark_png_matches_img_tag():
    import struct

    png = (Path(__file__).parents[1] / "infra/prod/branding/email-wordmark.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png[16:24])
    assert height == 56
    html = ce.render("CustomMessage_ForgotPassword", "{####}", None, "https://app.levstra.com")[1]
    assert f'width="{width // 2}"' in html
