"""Authorization shared by HTML, uploads, quizzes and AI endpoints."""

from flask import abort
from backend.i18n import _
from flask_login import current_user
from database import db
from database.models import Company, Course, CourseEditor, Membership


def membership_for(company_id, user=None):
    user = user or current_user
    if not user.is_authenticated:
        return None
    return Membership.query.filter_by(company_id=company_id, user_id=user.id).first()


def owns_company(company_id, user=None):
    membership = membership_for(company_id, user)
    return bool(
        membership
        and membership.role == "owner"
        and membership.company.owner_id == membership.user_id
    )


def require_company_owner(company_id=None):
    company = (
        db.session.get(Company, company_id) if company_id else current_user.company
    )
    if company is None or not owns_company(company.id):
        abort(403)
    return company


def can_edit_course(course, user=None):
    if owns_company(course.company_id, user):
        return True
    membership = membership_for(course.company_id, user)
    return bool(
        course.status == "draft"
        and membership
        and membership.role == "editor"
        and CourseEditor.query.filter_by(
            course_id=course.id, membership_id=membership.id
        ).first()
    )


def require_course(course_id, owner_only=False):
    course = db.session.get(Course, course_id)
    if course is None:
        abort(404)
    allowed = owns_company(course.company_id) if owner_only else can_edit_course(course)
    if not allowed:
        abort(403)
    return course


def publication_errors(course):
    errors = []
    if not course.lessons:
        errors.append(_("Add at least one lesson."))
    for lesson in course.lessons:
        if not (lesson.text or lesson.video_filename):
            errors.append(_("Lesson {number}: add text or video.", number=lesson.order))
        if not lesson.quiz or not lesson.quiz.questions:
            errors.append(_("Lesson {number}: add a quiz.", number=lesson.order))
        elif not 1 <= lesson.quiz.pass_score <= 100:
            errors.append(
                _("Lesson {number}: invalid pass score.", number=lesson.order)
            )
        else:
            for question in lesson.quiz.questions:
                if (
                    len(question.answers) < 2
                    or sum(bool(a.is_correct) for a in question.answers) != 1
                ):
                    errors.append(
                        _(
                            "Lesson {number}: each question needs two answers and one correct answer.",
                            number=lesson.order,
                        )
                    )
        if (
            course.is_private
            and lesson.video_filename
            and lesson.video_delivery != "authenticated"
        ):
            errors.append(
                _(
                    "Lesson {number}: re-upload the legacy public video before publishing a restricted course.",
                    number=lesson.order,
                )
            )
    return errors
