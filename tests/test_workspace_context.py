"""Personal mode must behave like an unrelated learner, not an author."""

from database import db
from database.models import Enrollment, QuizAttempt


def test_owner_personal_card_has_start_not_management(data, login):
    client = login(data["owner"])
    course = data["published"]
    response = client.get(f"/course/{course.id}")
    assert response.status_code == 200
    assert f'action="/course/{course.id}/start"' in response.text
    assert f'href="/course/{course.id}/builder"' not in response.text
    assert f'action="/course/{course.id}/delete"' not in response.text


def test_personal_owner_cannot_manage_directly(data, login):
    client = login(data["owner"])
    assert client.get(f"/course/{data['assigned'].id}/builder").status_code == 403
    assert client.post(f"/course/{data['published'].id}/delete").status_code == 403


def test_owner_can_complete_own_course_as_an_ordinary_learner(data, login):
    client = login(data["owner"])
    course = data["published"]
    assert (
        client.post(f"/course/{course.id}/start", follow_redirects=True).status_code
        == 200
    )
    assert client.get(f"/course/{course.id}/lesson/1/quiz").status_code == 200
    quiz = course.lessons[0].quiz
    answers = {
        f"q_{q.id}": next(a.id for a in q.answers if a.is_correct)
        for q in quiz.questions
    }
    response = client.post(f"/course/{course.id}/lesson/1/quiz", data=answers)
    assert response.status_code == 200 and "Congratulations" in response.text
    enrollment = Enrollment.query.filter_by(
        user_id=data["owner"].id, course_id=course.id
    ).one()
    assert enrollment.status == "completed"
    assert (
        QuizAttempt.query.filter_by(
            user_id=data["owner"].id, quiz_id=quiz.id, passed=True
        ).count()
        == 1
    )
    client.post("/workspaces/select", data={"company_id": 10})
    assert (
        f'href="/course/{course.id}/builder"' in client.get(f"/course/{course.id}").text
    )
    client.post("/workspaces/select", data={"company_id": ""})
    page = client.get(f"/course/{course.id}").text
    assert (
        "Course completed" in page and f'href="/course/{course.id}/builder"' not in page
    )
    assert enrollment.status == "completed"


def test_personal_owner_and_unrelated_learner_have_same_actions(data, login):
    course = data["private"]
    for user in (data["owner"], data["learner"]):
        client = login(user)
        page = client.get(f"/course/{course.id}").text
        assert f'action="/course/{course.id}/request"' in page
        assert f'href="/course/{course.id}/builder"' not in page
        client.post(f"/course/{course.id}/start")
        assert (
            Enrollment.query.filter_by(course_id=course.id, user_id=user.id).count()
            == 0
        )
        assert (
            client.post(
                f"/course/{course.id}/start", data={"invite": course.invite_token}
            ).status_code
            == 302
        )
        assert client.get(f"/course/{course.id}/lesson/1").status_code == 200


def test_management_requires_the_matching_selected_company(data, login):
    from database.models import Membership, Company

    owner = data["owner"]
    second = Company(owner_id=owner.id, name="Another owned company")
    db.session.add(second)
    db.session.flush()
    db.session.add(Membership(company_id=second.id, user_id=owner.id, role="owner"))
    db.session.commit()
    client = login(owner, company_id=10)
    course = data["assigned"]
    assert client.get(f"/course/{course.id}/builder").status_code == 200
    client.post("/workspaces/select", data={"company_id": second.id})
    assert client.get(f"/course/{course.id}/builder").status_code == 403
    assert client.get("/companies/10/team").status_code == 403
    assert (
        client.post(
            f"/course/{course.id}/lessons", data={"title": "Stale form"}
        ).status_code
        == 403
    )
    assert client.get(f"/course/{course.id}").status_code == 404
    client.post("/workspaces/select", data={"company_id": ""})
    assert client.get("/companies/10/settings").status_code == 403


def test_editor_personal_mode_cannot_edit_assigned_draft(data, login):
    client = login(data["editor"])
    course = data["assigned"]
    assert client.get(f"/course/{course.id}").status_code == 404
    assert client.get(f"/course/{course.id}/builder").status_code == 403
    client.post("/workspaces/select", data={"company_id": 10})
    assert client.get(f"/course/{course.id}/builder").status_code == 200
    page = client.get(f"/course/{course.id}").text
    assert "Draft" in page and "Published" not in page
    client.post("/workspaces/select", data={"company_id": ""})
    assert (
        client.post(
            f"/course/{course.id}/lessons", data={"title": "Old editor form"}
        ).status_code
        == 403
    )


def test_take_as_learner_switches_without_losing_course_destination(data, login):
    client = login(data["owner"], company_id=10)
    course = data["published"]
    assert "Take as a learner" in client.get(f"/course/{course.id}").text
    response = client.post(
        "/workspaces/select", data={"company_id": "", "next": f"/course/{course.id}"}
    )
    assert response.location == f"/course/{course.id}"
    with client.session_transaction() as session:
        assert "active_company_id" not in session
    assert f'action="/course/{course.id}/start"' in client.get(response.location).text
    assert Enrollment.query.count() == 0
    assert (
        client.post(
            "/workspaces/select",
            data={"company_id": "", "next": "https://evil.invalid"},
        ).location
        == "/account"
    )


def test_author_preview_never_uses_enrollment_links(data, login):
    client = login(data["owner"], company_id=10)
    course = data["published"]
    lesson = course.lessons[0]
    url = f"/course/{course.id}/preview/lesson/{lesson.id}"
    assert f'href="{url}"' in client.get(f"/course/{course.id}").text
    assert client.get(url).status_code == 200
    assert Enrollment.query.count() == 0 and QuizAttempt.query.count() == 0


def test_workspace_selector_announces_immediate_navigation(data, login):
    page = login(data["owner"]).get("/").text
    assert "data-workspace-select" in page
    assert 'aria-describedby="workspace-help"' in page
    assert "Choosing a workspace opens its dashboard immediately." in page
    assert ">Switch</button>" not in page
    assert "<noscript>" in page


def test_system_pages_use_a_shared_aligned_container(app, data, login):
    client = login(data["owner"])
    for url, status in [
        ("/missing-page", 404),
        (f"/course/{data['assigned'].id}/builder", 403),
    ]:
        page = client.get(url)
        assert page.status_code == status
        assert 'class="container page status-page"' in page.text
        assert 'id="status-title"' in page.text
        assert 'class="hero"' not in page.text
    from flask import render_template

    with app.test_request_context():
        page = render_template(
            "errors/conflict.html", message="Please check your changes."
        )
        assert 'class="container page status-page"' in page
        assert "Please check your changes." in page


def test_legacy_company_account_can_learn_in_personal_mode(data, login):
    owner = data["owner"]
    owner.type = "company"  # retained historical value, not an active permission
    db.session.commit()
    client = login(owner)
    course = data["published"]
    page = client.get(f"/course/{course.id}")
    assert f'action="/course/{course.id}/start"' in page.text
    assert (
        client.post(f"/course/{course.id}/start", follow_redirects=True).status_code
        == 200
    )


def test_automatic_workspace_post_still_requires_csrf(app, data, login):
    client = login(data["owner"])
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/workspaces/select", data={"company_id": 10}).status_code == 403
    with client.session_transaction() as session:
        assert "active_company_id" not in session
