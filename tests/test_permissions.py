import pytest
from database import db
from database.models import Course, Enrollment, CourseEditor
from backend.access import publication_errors


@pytest.mark.parametrize(
    "operation", ["builder", "edit", "lessons", "publish", "delete", "ai-suggestions"]
)
def test_unassigned_editor_denied(data, login, operation):
    client = login(data["editor"], company_id=10)
    cid = data["unassigned"].id
    response = (
        client.get(f"/course/{cid}/{operation}")
        if operation in ("builder", "edit")
        else client.post(f"/course/{cid}/{operation}", json={})
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "operation", ["builder", "edit", "lessons", "publish", "delete", "ai-suggestions"]
)
def test_published_course_not_editable_by_editor(data, login, operation):
    course = data["published"]
    db.session.add(
        CourseEditor(course_id=course.id, membership_id=data["editor_membership"].id)
    )
    db.session.commit()
    client = login(data["editor"], company_id=10)
    response = (
        client.get(f"/course/{course.id}/{operation}")
        if operation in ("builder", "edit")
        else client.post(f"/course/{course.id}/{operation}", json={})
    )
    assert response.status_code == 403


def test_assigned_editor_edits_but_cannot_publish_or_create(data, login):
    client = login(data["editor"], company_id=10)
    course = data["assigned"]
    assert client.get(f"/course/{course.id}/builder").status_code == 200
    assert (
        client.post(
            f"/course/{course.id}/lessons",
            data={"title": "New lesson", "text": "Content"},
        ).status_code
        == 302
    )
    assert client.post(f"/course/{course.id}/publish").status_code == 403
    client.post("/workspaces/select", data={"company_id": 10})
    assert client.post("/course/new", data={}).status_code == 403
    assert client.post("/pricing/choose", data={"plan": "monthly"}).status_code == 403
    assert client.get("/companies/10/team").status_code == 403
    assert (
        client.post(
            "/companies/10/invitations", data={"email": "x@example.invalid"}
        ).status_code
        == 403
    )


def test_owner_ids_are_not_company_ids(data, login):
    client = login(data["owner"], company_id=10)
    course = data["assigned"]
    assert data["owner"].id != course.company_id
    assert client.get(f"/course/{course.id}/builder").status_code == 200
    assert (
        login(data["other"], company_id=20)
        .get(f"/course/{course.id}/builder")
        .status_code
        == 403
    )


def test_editor_dashboard_does_not_leak_unassigned_courses(data, login):
    client = login(data["editor"], company_id=10)
    client.post("/workspaces/select", data={"company_id": 10})
    page = client.get("/account").data
    assert b"Assigned draft" in page
    assert b"Unassigned draft" not in page
    assert data["learner"].email.encode() not in page


def test_course_publication_validated(data, login):
    client = login(data["owner"], company_id=10)
    course = data["assigned"]
    assert publication_errors(course)
    assert client.post(f"/course/{course.id}/publish").status_code == 302
    db.session.refresh(course)
    assert course.status == "draft"
    valid = data["published"]
    assert publication_errors(valid) == []


def test_private_draft_invite_cannot_open_or_enroll(data, login):
    course = data["private"]
    course.status = "draft"
    db.session.commit()
    assert (
        login(data["learner"]).get("/course/private/" + course.invite_token).status_code
        == 404
    )
    assert login(data["learner"]).post(
        f"/course/{course.id}/start", data={"invite": course.invite_token}
    ).status_code in (404, 409)
    assert Enrollment.query.filter_by(course_id=course.id).count() == 0


def test_private_free_plan_preserves_visibility_and_form(data, login):
    from core.questions import LEVELS

    data["alpha"].plan = "free"
    db.session.commit()
    client = login(data["owner"], company_id=10)
    client.post("/workspaces/select", data={"company_id": 10})
    course = data["assigned"]
    response = client.post(
        f"/course/{course.id}/edit",
        data={
            "title": "Keep my title",
            "level": next(iter(LEVELS)),
            "visibility": "private",
        },
    )
    assert response.status_code == 400
    assert b"Keep my title" in response.data
    assert b'value="private" checked' in response.data
    db.session.refresh(course)
    assert course.title == "Assigned draft"
    assert course.access_mode == "public"


def test_private_course_visible_in_catalog_and_company_page(app, data, login):
    client = app.test_client()
    assert b"Private course" in client.get("/api/search").data
    assert b"Private course" in client.get("/company/10").data
    assert client.get(f"/course/{data['private'].id}").status_code == 200
    assert (
        client.get("/course/private/" + data["private"].invite_token).status_code == 200
    )
    learner = login(data["learner"])
    assert (
        learner.post(
            f"/course/{data['private'].id}/start",
            data={"invite": data["private"].invite_token},
        ).status_code
        == 302
    )
    assert Enrollment.query.filter_by(
        course_id=data["private"].id, user_id=data["learner"].id
    ).one()


def test_request_course_requires_owner_approval(data, login):
    course = data["private"]
    course.access_mode = "request"
    db.session.commit()
    client = login(data["learner"])
    assert client.post(f"/course/{course.id}/request").status_code == 302
    from database.models import AccessRequest

    req = AccessRequest.query.filter_by(course_id=course.id).one()
    assert Enrollment.query.filter_by(course_id=course.id).count() == 0
    assert (
        login(data["owner"], company_id=10)
        .post(f"/course/{course.id}/requests/{req.id}/accept")
        .status_code
        == 302
    )
    assert Enrollment.query.filter_by(course_id=course.id).count() == 1


def test_preview_creates_no_progress(data, login):
    course = data["published"]
    lesson = course.lessons[0]
    assert (
        login(data["owner"], company_id=10)
        .get(f"/course/{course.id}/preview/lesson/{lesson.id}")
        .status_code
        == 200
    )
    assert Enrollment.query.count() == 0


def test_legacy_public_video_blocks_restricted_publication(data):
    course = data["private"]
    lesson = course.lessons[0]
    lesson.video_filename = "legacy-id"
    lesson.video_delivery = "upload"
    assert any("re-upload" in e for e in publication_errors(course))


def test_csrf_required(app, data, login):
    client = login(data["owner"], company_id=10)
    app.config["WTF_CSRF_ENABLED"] = True
    assert (
        client.post(
            "/companies/10/invitations", data={"email": data["learner"].email}
        ).status_code
        == 403
    )


def test_published_structure_cannot_break_existing_progress(data, login):
    course = data["published"]
    db.session.add(Enrollment(user_id=data["learner"].id, course_id=course.id))
    db.session.commit()
    client = login(data["owner"], company_id=10)
    assert (
        client.post(
            f"/course/{course.id}/lessons", data={"title": "New", "text": "text"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/course/{course.id}/lessons/{course.lessons[0].id}/delete"
        ).status_code
        == 409
    )


@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_visible_private_course_does_not_open_lessons_without_access(
    data, login, token
):
    course = data["private"]
    client = login(data["learner"])
    assert client.get(f"/course/{course.id}").status_code == 200
    payload = {} if token is None else {"invite": token}
    assert client.post(f"/course/{course.id}/start", data=payload).status_code == 302
    assert client.get(f"/course/{course.id}/lesson/1").status_code == 403
    assert Enrollment.query.filter_by(course_id=course.id).count() == 0


def test_owner_sees_private_course_invitation_link(data, login):
    course = data["private"]
    client = login(data["owner"], company_id=10)
    link = ("/course/private/" + course.invite_token).encode()
    assert link in client.get(f"/course/{course.id}").data
    client.post("/workspaces/select", data={"company_id": 10})
    assert link in client.get("/account").data


def test_form_has_only_original_public_and_private_choices(data, login):
    client = login(data["owner"], company_id=10)
    page = client.get(f"/course/{data['private'].id}/edit").text
    assert 'value="None"' not in page and ">None</textarea>" not in page
    assert 'value="private" checked' in page
    assert 'name="visibility" value="public"' in page
    assert 'name="visibility" value="invite"' not in page
    assert 'name="visibility" value="request"' not in page


@pytest.mark.parametrize("visibility", ["private", "request", "invite"])
def test_previous_form_choices_normalize_to_visible_private_course(
    data, login, visibility
):
    from core.questions import LEVELS

    course = data["assigned"]
    response = login(data["owner"], company_id=10).post(
        f"/course/{course.id}/edit",
        data={
            "title": "Private draft",
            "level": next(iter(LEVELS)),
            "visibility": visibility,
        },
    )
    assert response.status_code == 302
    db.session.refresh(course)
    assert course.is_private and course.access_mode == "request"
