"""PDF materials of a course: upload, permissions, download, deletion."""
from io import BytesIO

import pytest

from database import db
from database.models import Course, CourseDocument, Enrollment


@pytest.fixture
def storage(monkeypatch):
    """Cloudinary is replaced by a list of "stored" and "deleted" public ids."""
    state = {"saved": [], "deleted": []}

    def fake_save(upload):
        public_id = f"hackathon-documents/fake{len(state['saved'])}.pdf"
        state["saved"].append(public_id)
        return public_id, 1234

    def fake_delete(public_id):
        state["deleted"].append(public_id)
        return True

    monkeypatch.setattr("backend.routes.builder.save_document", fake_save)
    monkeypatch.setattr("backend.routes.builder.delete_document", fake_delete)
    monkeypatch.setattr("backend.routes.courses.delete_document", fake_delete)
    monkeypatch.setattr("backend.uploads.delete_document", fake_delete)  # workspaces imports it inside the view
    monkeypatch.setattr(
        "backend.routes.courses.document_url",
        lambda public_id, download=False: f"https://signed.invalid/{public_id}?dl={int(download)}",
    )
    return state


def pdf(name="guide.pdf", content=b"%PDF-1.4 test"):
    return (BytesIO(content), name)


def add_document(client, course, title="Guide"):
    return client.post(
        f"/course/{course.id}/documents",
        data={"title": title, "document": pdf()},
        content_type="multipart/form-data",
    )


def make_document(course, public_id="hackathon-documents/x.pdf"):
    document = CourseDocument(
        course_id=course.id, title="Doc", public_id=public_id, filename="doc.pdf", size_bytes=10
    )
    db.session.add(document)
    db.session.commit()
    return document


def test_owner_adds_and_deletes_document(data, login, storage):
    client = login(data["owner"], company_id=10)
    course = data["published"]
    assert add_document(client, course).status_code == 302
    document = CourseDocument.query.filter_by(course_id=course.id).one()
    assert document.title == "Guide" and document.size_bytes == 1234
    assert b"Guide" in client.get(f"/course/{course.id}/builder").data
    response = client.post(f"/course/{course.id}/documents/{document.id}/delete")
    assert response.status_code == 302
    assert CourseDocument.query.count() == 0
    assert storage["deleted"] == [document.public_id]


def test_title_defaults_to_file_name(data, login, storage):
    client = login(data["owner"], company_id=10)
    client.post(
        f"/course/{data['published'].id}/documents",
        data={"title": "", "document": pdf("Safety rules.pdf")},
        content_type="multipart/form-data",
    )
    assert CourseDocument.query.one().title == "Safety rules"


def test_assigned_editor_can_add_but_unassigned_cannot(data, login, storage):
    client = login(data["editor"], company_id=10)
    assert add_document(client, data["assigned"]).status_code == 302
    assert CourseDocument.query.count() == 1
    assert add_document(client, data["unassigned"]).status_code == 403
    assert CourseDocument.query.count() == 1


def test_other_company_and_learner_cannot_manage(data, login, storage):
    course = data["published"]
    document = make_document(course)
    for user, company in ((data["other"], 20), (data["learner"], None)):
        client = login(user, company_id=company)
        assert add_document(client, course).status_code == 403
        assert client.post(f"/course/{course.id}/documents/{document.id}/delete").status_code == 403
    assert CourseDocument.query.count() == 1 and not storage["saved"]


def test_anonymous_cannot_add(data, app, storage):
    response = app.test_client().post(
        f"/course/{data['published'].id}/documents",
        data={"document": pdf()},
        content_type="multipart/form-data",
    )
    assert response.status_code in (302, 401)
    assert CourseDocument.query.count() == 0


def test_document_limit_per_course(data, login, storage):
    client = login(data["owner"], company_id=10)
    course = data["published"]
    for _ in range(10):
        add_document(client, course)
    assert add_document(client, course).status_code == 302
    assert CourseDocument.query.count() == 10


def test_document_from_another_course_is_not_deleted(data, login, storage):
    document = make_document(data["private"])
    client = login(data["owner"], company_id=10)
    response = client.post(f"/course/{data['published'].id}/documents/{document.id}/delete")
    assert response.status_code == 404
    assert CourseDocument.query.count() == 1


def test_download_permissions(data, login, storage):
    course = data["published"]
    document = make_document(course)
    url = f"/course/{course.id}/documents/{document.id}"
    # not enrolled
    assert login(data["learner"]).get(url).status_code == 403
    # anonymous
    assert login_required_redirect(url)
    # enrolled learner gets a short-lived signed link, with the download flag on request
    db.session.add(Enrollment(user_id=data["learner"].id, course_id=course.id))
    db.session.commit()
    client = login(data["learner"])
    response = client.get(url)
    assert response.status_code == 302
    assert response.location == f"https://signed.invalid/{document.public_id}?dl=0"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert client.get(url + "?download=1").location.endswith("dl=1")
    # owner in the company workspace
    assert login(data["owner"], company_id=10).get(url).status_code == 302
    # owner of another company
    assert login(data["other"], company_id=20).get(url).status_code == 403


def login_required_redirect(url):
    from flask import current_app

    response = current_app.test_client().get(url)
    return response.status_code in (302, 401)


def test_private_course_document_needs_accepted_access(data, login, storage):
    course = data["private"]
    document = make_document(course)
    url = f"/course/{course.id}/documents/{document.id}"
    client = login(data["learner"])
    assert client.get(url).status_code == 403
    page = client.get(f"/course/{course.id}").text
    assert "Doc" not in page.split("Lessons", 1)[-1] or "🔒" in page
    db.session.add(Enrollment(user_id=data["learner"].id, course_id=course.id))
    db.session.commit()
    assert client.get(url).status_code == 302


def test_draft_course_document_not_open_to_enrolled(data, login, storage):
    course = data["assigned"]
    document = make_document(course)
    db.session.add(Enrollment(user_id=data["learner"].id, course_id=course.id))
    db.session.commit()
    assert login(data["learner"]).get(f"/course/{course.id}/documents/{document.id}").status_code == 403


def test_course_page_shows_list_only_to_enrolled(data, login, storage):
    course = data["published"]
    make_document(course)
    locked = login(data["learner"]).get(f"/course/{course.id}").text
    assert "Materials" in locked and "/documents/" not in locked
    db.session.add(Enrollment(user_id=data["learner"].id, course_id=course.id))
    db.session.commit()
    opened = login(data["learner"]).get(f"/course/{course.id}").text
    assert "/documents/" in opened


def test_deleting_course_removes_files(data, login, storage):
    course = data["published"]
    document = make_document(course)
    response = login(data["owner"], company_id=10).post(f"/course/{course.id}/delete")
    assert response.status_code == 302
    assert db.session.get(Course, course.id) is None
    assert CourseDocument.query.count() == 0
    assert storage["deleted"] == [document.public_id]


def test_deleting_company_removes_files(data, login, storage):
    document = make_document(data["published"])
    client = login(data["owner"], company_id=10)
    response = client.post("/companies/10/delete", data={"confirm_name": "Alpha"})
    assert response.status_code == 302
    assert CourseDocument.query.count() == 0
    assert storage["deleted"] == [document.public_id]


# ---- the real validation in backend/uploads.py (Cloudinary is mocked) ----


@pytest.fixture
def uploads_module(monkeypatch):
    from backend import uploads

    options = {}
    monkeypatch.setattr(uploads, "_configure", lambda: None)
    monkeypatch.setattr(
        uploads.cloudinary.uploader,
        "upload",
        lambda source, **kw: options.update(kw) or {"public_id": kw["public_id"]},
    )
    return uploads, options


def storage_file(name, content):
    from werkzeug.datastructures import FileStorage

    return FileStorage(stream=BytesIO(content), filename=name)


def test_save_document_accepts_pdf_and_stores_it_private(app, uploads_module):
    uploads, options = uploads_module
    public_id, size = uploads.save_document(storage_file("a.PDF", b"%PDF-1.7 hello"))
    assert public_id.startswith("hackathon-documents/") and public_id.endswith(".pdf")
    assert size == len(b"%PDF-1.7 hello")
    assert options["resource_type"] == "raw" and options["type"] == "authenticated"


@pytest.mark.parametrize(
    "name,content",
    [
        ("a.exe", b"%PDF-1.4"),
        ("a.pdf", b"MZ not a pdf"),
        ("a.pdf", b""),
        ("a", b"%PDF-1.4"),
        ("a.pdf", b"%PDF-" + b"0" * (10 * 1024 * 1024)),
    ],
)
def test_save_document_rejects_bad_files(app, uploads_module, name, content):
    uploads, _ = uploads_module
    with pytest.raises(ValueError):
        uploads.save_document(storage_file(name, content))


def test_document_url_is_signed_and_expiring(app, monkeypatch):
    from backend import uploads

    seen = {}
    monkeypatch.setattr(uploads, "_configure", lambda: None)
    monkeypatch.setattr(
        uploads.cloudinary.utils,
        "private_download_url",
        lambda *a, **kw: seen.update(args=a, **kw) or "https://signed.invalid/doc",
    )
    assert uploads.document_url("hackathon-documents/x.pdf", download=True) == "https://signed.invalid/doc"
    assert seen["resource_type"] == "raw" and seen["type"] == "authenticated"
    assert seen["attachment"] is True and seen["expires_at"] > 0
