"""Company workspaces, email-bound invitations and course assignments."""

import hashlib
import secrets
from datetime import timedelta
from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from backend.access import require_company_owner, owns_company
from backend.email import external_url, send_email
from backend.i18n import _, safe_next_url
from backend.routes.site_auth import EMAIL_RE
from core.clock import utc_now
from database import db
from database.models import (
    Company,
    CompanyInvitation,
    Course,
    CourseEditor,
    Membership,
    Offer,
)

bp = Blueprint("workspaces", __name__)


def pending_invitations():
    return CompanyInvitation.query.filter(
        CompanyInvitation.email == current_user.email,
        CompanyInvitation.status == "pending",
        CompanyInvitation.expires_at > utc_now(),
    ).all()


@bp.route("/workspaces", methods=["GET"])
@login_required
def index():
    return render_template("workspaces.html", invitations=pending_invitations())


@bp.route("/workspaces/select", methods=["POST"])
@login_required
def select():
    selected = request.form.get("company_id", "")
    if not selected:
        session.pop("active_company_id", None)
    else:
        company_id = request.form.get("company_id", type=int)
        if not Membership.query.filter_by(
            company_id=company_id, user_id=current_user.id
        ).first():
            abort(403)
        session["active_company_id"] = company_id
    destination = safe_next_url(request.form.get("next"), url_for("main.account"))
    return redirect(destination)


@bp.route("/companies/new", methods=["GET", "POST"])
@login_required
def create_company():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        if not name or len(name) > 120 or len(description) > 500:
            flash(_("Enter a company name (up to 120 characters)."), "error")
            return render_template("company_settings.html", company=None), 400
        company = Company(owner_id=current_user.id, name=name, description=description)
        db.session.add(company)
        db.session.flush()
        db.session.add(
            Membership(company_id=company.id, user_id=current_user.id, role="owner")
        )
        db.session.commit()
        session["active_company_id"] = company.id
        return redirect(url_for("main.account"))
    return render_template("company_settings.html", company=None)


@bp.route("/companies/<int:company_id>/settings", methods=["GET", "POST"])
@login_required
def settings(company_id):
    company = require_company_owner(company_id)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        if not name or len(name) > 120 or len(description) > 500:
            abort(400)
        company.name, company.description = name, description
        db.session.commit()
        flash(_("Company saved."), "success")
        return redirect(url_for("workspaces.settings", company_id=company.id))
    return render_template("company_settings.html", company=company)


@bp.route("/companies/<int:company_id>/logo", methods=["POST"])
@login_required
def logo(company_id):
    from backend.uploads import save_image, delete_image

    company = require_company_owner(company_id)
    old = company.avatar
    if request.form.get("remove"):
        company.avatar = None
    else:
        try:
            company.avatar = save_image(request.files.get("avatar"))
        except (ValueError, RuntimeError) as error:
            flash(str(error), "error")
            return redirect(url_for("workspaces.settings", company_id=company.id))
    db.session.commit()
    if old:
        delete_image(old)
    return redirect(url_for("workspaces.settings", company_id=company.id))


@bp.route("/companies/<int:company_id>/team")
@login_required
def team(company_id):
    company = require_company_owner(company_id)
    members = Membership.query.filter_by(company_id=company.id).all()
    courses = Course.query.filter_by(company_id=company.id).all()
    assignments = {
        (row.course_id, row.membership_id)
        for row in CourseEditor.query.join(
            Membership, CourseEditor.membership_id == Membership.id
        )
        .filter(Membership.company_id == company.id)
        .all()
    }
    invitations = (
        CompanyInvitation.query.filter_by(company_id=company.id)
        .order_by(CompanyInvitation.created_at.desc())
        .all()
    )
    return render_template(
        "company_team.html",
        company=company,
        members=members,
        courses=courses,
        assignments=assignments,
        invitations=invitations,
        now=utc_now(),
    )


@bp.route("/companies/<int:company_id>/invitations", methods=["POST"])
@login_required
def invite(company_id):
    company = require_company_owner(company_id)
    email = request.form.get("email", "").strip().lower()
    if len(email) > 255 or not EMAIL_RE.fullmatch(email):
        abort(400)
    from database.models import User

    user = User.query.filter_by(email=email).first()
    if (
        user
        and Membership.query.filter_by(company_id=company.id, user_id=user.id).first()
    ):
        flash(_("This person is already a company member."), "info")
        return redirect(url_for("workspaces.team", company_id=company.id))
    now = utc_now()
    recent = CompanyInvitation.query.filter(
        CompanyInvitation.company_id == company.id,
        CompanyInvitation.email == email,
        CompanyInvitation.created_at > now - timedelta(seconds=60),
    ).first()
    if recent:
        flash(_("Wait one minute before sending another invitation."), "info")
        return redirect(url_for("workspaces.team", company_id=company.id))
    CompanyInvitation.query.filter_by(
        company_id=company.id, email=email, status="pending"
    ).update({"status": "cancelled"})
    token = secrets.token_urlsafe(32)
    invitation = CompanyInvitation(
        company_id=company.id,
        email=email,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        status="pending",
        created_at=now,
        expires_at=now + timedelta(days=7),
    )
    db.session.add(invitation)
    db.session.commit()
    link = external_url("workspaces.invitation_link", token=token)
    sent = send_email(
        email,
        _("Company invitation"),
        _(
            "{company} invites you to edit assigned draft courses. Sign in with {email} and accept within 7 days.",
            company=company.name,
            email=email,
        )
        + "\n"
        + link,
    )
    flash(
        (
            _(
                "Invitation created. The recipient can also find it in their personal account."
            )
            if sent
            else _(
                "Email could not be sent. The invitation is available in the recipient account."
            )
        ),
        "success" if sent else "error",
    )
    return redirect(url_for("workspaces.team", company_id=company.id))


@bp.route("/invitations/<token>")
def invitation_link(token):
    invitation = CompanyInvitation.query.filter_by(
        token_hash=hashlib.sha256(token.encode()).hexdigest()
    ).first()
    if (
        not invitation
        or invitation.status != "pending"
        or invitation.expires_at <= utc_now()
    ):
        abort(404)
    if not current_user.is_authenticated:
        session["after_login"] = url_for("workspaces.invitation_link", token=token)
        flash(_("Sign in with the invited email to view this invitation."), "info")
        return redirect(url_for("main.home", auth="login"))
    if current_user.email != invitation.email:
        abort(403)
    return redirect(url_for("workspaces.index"))


@bp.route("/invitations/<int:invitation_id>/respond", methods=["POST"])
@login_required
def respond(invitation_id):
    action = request.form.get("action")
    if action not in ("accept", "decline"):
        abort(400)
    invitation = db.session.get(CompanyInvitation, invitation_id)
    if invitation is None:
        abort(404)
    if invitation.email != current_user.email:
        abort(403)
    claimed = CompanyInvitation.query.filter(
        CompanyInvitation.id == invitation_id,
        CompanyInvitation.status == "pending",
        CompanyInvitation.expires_at > utc_now(),
    ).update(
        {"status": "accepted" if action == "accept" else "declined"},
        synchronize_session=False,
    )
    if not claimed:
        db.session.rollback()
        abort(409)
    if action == "accept":
        if not Membership.query.filter_by(
            company_id=invitation.company_id, user_id=current_user.id
        ).first():
            db.session.add(
                Membership(
                    company_id=invitation.company_id,
                    user_id=current_user.id,
                    role="editor",
                )
            )
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        abort(409)
    flash(
        _("Invitation accepted.") if action == "accept" else _("Invitation declined."),
        "success",
    )
    return redirect(url_for("workspaces.index"))


@bp.route(
    "/companies/<int:company_id>/invitations/<int:invitation_id>/cancel",
    methods=["POST"],
)
@login_required
def cancel(company_id, invitation_id):
    require_company_owner(company_id)
    invitation = db.session.get(CompanyInvitation, invitation_id)
    if not invitation or invitation.company_id != company_id:
        abort(404)
    CompanyInvitation.query.filter_by(id=invitation_id, status="pending").update(
        {"status": "cancelled"}
    )
    db.session.commit()
    return redirect(url_for("workspaces.team", company_id=company_id))


@bp.route(
    "/companies/<int:company_id>/members/<int:membership_id>/remove", methods=["POST"]
)
@login_required
def remove_member(company_id, membership_id):
    require_company_owner(company_id)
    membership = db.session.get(Membership, membership_id)
    if not membership or membership.company_id != company_id:
        abort(404)
    if membership.role == "owner":
        abort(400)
    CourseEditor.query.filter_by(membership_id=membership.id).delete()
    db.session.delete(membership)
    db.session.commit()
    return redirect(url_for("workspaces.team", company_id=company_id))


@bp.route(
    "/companies/<int:company_id>/courses/<int:course_id>/editors/<int:membership_id>",
    methods=["POST"],
)
@login_required
def assign(company_id, course_id, membership_id):
    require_company_owner(company_id)
    course = db.session.get(Course, course_id)
    member = db.session.get(Membership, membership_id)
    if (
        not course
        or course.company_id != company_id
        or not member
        or member.company_id != company_id
        or member.role != "editor"
    ):
        abort(404)
    assignment = CourseEditor.query.filter_by(
        course_id=course_id, membership_id=membership_id
    ).first()
    action = request.form.get("action")
    if action == "remove":
        if assignment:
            db.session.delete(assignment)
    elif action == "add":
        if not assignment:
            db.session.add(
                CourseEditor(course_id=course_id, membership_id=membership_id)
            )
    else:
        abort(400)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        abort(409)
    return redirect(url_for("workspaces.team", company_id=company_id))


@bp.route("/companies/<int:company_id>/delete", methods=["POST"])
@login_required
def delete_company(company_id):
    from backend.uploads import delete_document, delete_image, delete_video

    company = require_company_owner(company_id)
    if request.form.get("confirm_name") != company.name:
        abort(400)
    videos = []
    documents = []
    for course in Course.query.filter_by(company_id=company.id).all():
        documents.extend(document.public_id for document in course.documents)
        videos.extend(
            (lesson.video_filename, lesson.video_delivery)
            for lesson in course.lessons
            if lesson.video_filename
        )
        CourseEditor.query.filter_by(course_id=course.id).delete()
        db.session.delete(course)
    db.session.flush()
    CompanyInvitation.query.filter_by(company_id=company.id).delete()
    membership_ids = [
        member.id for member in Membership.query.filter_by(company_id=company.id)
    ]
    if membership_ids:
        CourseEditor.query.filter(
            CourseEditor.membership_id.in_(membership_ids)
        ).delete(synchronize_session=False)
    Membership.query.filter_by(company_id=company.id).delete()
    Offer.query.filter_by(company_id=company.id).delete()
    avatar = company.avatar
    db.session.delete(company)
    db.session.commit()
    if session.get("active_company_id") == company_id:
        session.pop("active_company_id", None)
    for public_id, delivery in videos:
        delete_video(public_id, delivery=delivery)
    for public_id in documents:
        delete_document(public_id)
    if avatar:
        delete_image(avatar)
    return redirect(url_for("workspaces.index"))
