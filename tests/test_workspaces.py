from core.clock import utc_now
from datetime import datetime, timedelta
import hashlib
import pytest
from database import db
from database.models import (
    CompanyInvitation,
    Membership,
    CourseEditor,
    Enrollment,
    Company,
    User,
)


def invitation(data, **changes):
    values = dict(
        company_id=data["alpha"].id,
        email=data["learner"].email,
        token_hash=hashlib.sha256(b"test-invitation").hexdigest(),
        status="pending",
        expires_at=utc_now() + timedelta(days=7),
    )
    values.update(changes)
    invite = CompanyInvitation(**values)
    db.session.add(invite)
    db.session.commit()
    return invite


def test_one_login_multiple_companies_and_learning(data, login):
    client = login(data["editor"])
    for company in (data["alpha"], data["beta"]):
        assert (
            client.post(
                "/workspaces/select", data={"company_id": company.id}
            ).status_code
            == 302
        )
        assert company.name.encode() in client.get("/account").data
    assert client.post(f"/course/{data['published'].id}/start").status_code == 302
    assert Enrollment.query.filter_by(user_id=data["editor"].id).count() == 1
    assert client.post("/workspaces/select", data={"company_id": ""}).status_code == 302
    assert client.get("/account").status_code == 200


def test_unknown_workspace_cannot_be_selected(data, login):
    assert (
        login(data["learner"])
        .post("/workspaces/select", data={"company_id": 10})
        .status_code
        == 403
    )


@pytest.mark.parametrize(
    "action,status", [("accept", "accepted"), ("decline", "declined")]
)
def test_invitation_requires_explicit_response(data, login, action, status):
    invite = invitation(data)
    assert Membership.query.filter_by(user_id=data["learner"].id).count() == 0
    client = login(data["learner"])
    assert client.get("/invitations/test-invitation").status_code == 302
    assert Membership.query.filter_by(user_id=data["learner"].id).count() == 0
    assert (
        client.post(
            f"/invitations/{invite.id}/respond", data={"action": action}
        ).status_code
        == 302
    )
    db.session.refresh(invite)
    assert invite.status == status
    assert Membership.query.filter_by(user_id=data["learner"].id).count() == (
        1 if action == "accept" else 0
    )
    if action == "accept":
        assert (
            CourseEditor.query.join(Membership)
            .filter(Membership.user_id == data["learner"].id)
            .count()
            == 0
        )
    assert (
        client.post(
            f"/invitations/{invite.id}/respond", data={"action": action}
        ).status_code
        == 409
    )


def test_invitation_wrong_email(data, login):
    invite = invitation(data)
    client = login(data["other"])
    assert client.get("/invitations/test-invitation").status_code == 403
    assert (
        client.post(
            f"/invitations/{invite.id}/respond", data={"action": "accept"}
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "changes",
    [dict(expires_at=utc_now() - timedelta(seconds=1)), dict(status="cancelled")],
)
def test_expired_or_cancelled_invitation(data, login, changes):
    invite = invitation(data, **changes)
    client = login(data["learner"])
    assert client.get("/invitations/test-invitation").status_code == 404
    assert (
        client.post(
            f"/invitations/{invite.id}/respond", data={"action": "accept"}
        ).status_code
        == 409
    )


def test_cancel_invitation(data, login):
    invite = invitation(data)
    assert (
        login(data["other"])
        .post(f"/companies/10/invitations/{invite.id}/cancel")
        .status_code
        == 403
    )
    assert (
        login(data["owner"])
        .post(f"/companies/10/invitations/{invite.id}/cancel")
        .status_code
        == 302
    )
    db.session.refresh(invite)
    assert invite.status == "cancelled"


def test_invite_email_and_no_automatic_membership(data, login, monkeypatch):
    emails = []
    monkeypatch.setattr(
        "backend.routes.workspaces.send_email",
        lambda *args: emails.append(args) or True,
    )
    client = login(data["owner"])
    assert (
        client.post(
            "/companies/10/invitations", data={"email": " LEARNER@EXAMPLE.INVALID "}
        ).status_code
        == 302
    )
    assert CompanyInvitation.query.filter_by(email=data["learner"].email).count() == 1
    assert emails and "/invitations/" in emails[0][2]
    assert Membership.query.filter_by(user_id=data["learner"].id).count() == 0


def test_revocation_works_on_open_editor_session(data, login):
    editor = login(data["editor"])
    owner = login(data["owner"])
    course = data["assigned"]
    member = data["editor_membership"]
    assert editor.get(f"/course/{course.id}/builder").status_code == 200
    assert (
        owner.post(
            f"/companies/10/courses/{course.id}/editors/{member.id}",
            data={"action": "remove"},
        ).status_code
        == 302
    )
    assert editor.get(f"/course/{course.id}/builder").status_code == 403
    assert (
        editor.post(
            f"/course/{course.id}/lessons", data={"title": "Forbidden"}
        ).status_code
        == 403
    )


def test_remove_member_clears_assignments(data, login):
    member = data["editor_membership"]
    editor = login(data["editor"])
    assert (
        login(data["owner"])
        .post(f"/companies/10/members/{member.id}/remove")
        .status_code
        == 302
    )
    assert CourseEditor.query.filter_by(membership_id=member.id).count() == 0
    assert editor.get(f"/course/{data['assigned'].id}/builder").status_code == 403
    assert editor.post("/workspaces/select", data={"company_id": 10}).status_code == 403


def test_cross_company_assignment_rejected(data, login):
    foreign = Membership.query.filter_by(
        company_id=20, user_id=data["editor"].id
    ).first()
    assert (
        login(data["owner"])
        .post(
            f"/companies/10/courses/{data['assigned'].id}/editors/{foreign.id}",
            data={"action": "add"},
        )
        .status_code
        == 404
    )


def test_company_creation_and_owner_cannot_be_removed(data, login):
    client = login(data["learner"])
    assert (
        client.post("/companies/new", data={"name": "New company"}).status_code == 302
    )
    company = Company.query.filter_by(name="New company").one()
    member = Membership.query.filter_by(
        company_id=company.id, user_id=data["learner"].id
    ).one()
    assert member.role == "owner"
    assert (
        client.post(f"/companies/{company.id}/members/{member.id}/remove").status_code
        == 400
    )


def test_account_deletion_requires_explicit_company_deletion(data, login):
    client = login(data["owner"])
    client.post("/account/delete", data={"confirm_email": data["owner"].email})
    assert db.session.get(User, data["owner"].id)
    assert (
        client.post("/companies/10/delete", data={"confirm_name": "Alpha"}).status_code
        == 302
    )
    assert db.session.get(Company, 10) is None
    assert (
        client.post(
            "/account/delete", data={"confirm_email": data["owner"].email}
        ).status_code
        == 302
    )
    assert db.session.get(User, data["owner"].id) is None
    assert db.session.get(User, data["learner"].id)
