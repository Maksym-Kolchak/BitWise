import pytest
from database import db
from database.models import Enrollment
from backend.translator import _serializer, _read_parts


def test_translation_authorization_rechecked(app, data, login):
    lesson = data["private"].lessons[0]
    with app.test_request_context():
        token = _serializer().dumps([["lesson", lesson.id, "text"]])
        with pytest.raises(ValueError):
            _read_parts(token)
    client = login(data["learner"])
    # Grant enrollment: text can now be translated, but not after access is removed.
    enrollment = Enrollment(user_id=data["learner"].id, course_id=lesson.course_id)
    db.session.add(enrollment)
    db.session.commit()
    with client:
        client.get("/account")
        assert _read_parts(token) == ["Private learning content"]
        db.session.delete(enrollment)
        db.session.commit()
        with pytest.raises(ValueError):
            _read_parts(token)


def test_translation_api_cannot_use_private_source_token(app, data):
    lesson = data["private"].lessons[0]
    with app.test_request_context():
        token = _serializer().dumps([["lesson", lesson.id, "text"]])
    assert (
        app.test_client().post("/api/translate", json={"sources": [token]}).status_code
        == 400
    )


def test_login_link_single_use_and_safe_return(app, data):
    from backend.routes.site_auth import _serializer
    from backend.i18n import safe_next_url

    user = data["learner"]
    with app.test_request_context():
        token = _serializer().dumps(
            {"id": user.id, "n": user.login_nonce, "next": "https://evil.invalid"}
        )
        assert safe_next_url("//evil.invalid", "/account") == "/account"
    client = app.test_client()
    response = client.get("/login/" + token)
    assert response.location == "/account"
    assert (
        "already used"
        in app.test_client().get("/login/" + token, follow_redirects=True).text
    )


def test_valid_login_returns_to_course(app, data):
    from backend.routes.site_auth import _serializer

    user = data["learner"]
    target = f"/course/{data['published'].id}"
    with app.test_request_context():
        token = _serializer().dumps(
            {"id": user.id, "n": user.login_nonce, "next": target}
        )
    assert app.test_client().get("/login/" + token).location == target


def test_private_headers(data, login):
    response = login(data["editor"]).get("/workspaces")
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Referrer-Policy"] == "same-origin"


def test_authenticated_upload_and_expiring_url(app, monkeypatch):
    from backend import uploads
    from werkzeug.datastructures import FileStorage
    from io import BytesIO

    options = {}
    monkeypatch.setattr(uploads, "_configure", lambda: None)
    monkeypatch.setattr(
        uploads.cloudinary.uploader,
        "upload_large",
        lambda source, **kw: options.update(kw) or {"public_id": "safe-video"},
    )
    assert (
        uploads.save_video(FileStorage(stream=BytesIO(b"test"), filename="test.mp4"))
        == "safe-video"
    )
    assert options["type"] == "authenticated"
    monkeypatch.setattr(
        uploads.cloudinary.utils,
        "private_download_url",
        lambda *a, **kw: options.update(kw) or "https://signed.invalid/video",
    )
    assert uploads.video_url("safe-video") == "https://signed.invalid/video"
    assert options["expires_at"] > 0 and options["type"] == "authenticated"


def test_production_requires_secret():
    from core import create_app

    class Bad:
        APP_ENV = "production"
        SECRET_KEY = "dev-change-me-in-production"

    with pytest.raises(RuntimeError):
        create_app(Bad)
