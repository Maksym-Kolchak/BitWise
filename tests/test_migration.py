from pathlib import Path
import sqlalchemy as sa
from flask_migrate import upgrade
from core import create_app
from database import db


def test_upgrade_legacy_schema_preserves_data(tmp_path):
    class Config:
        TESTING = False
        SECRET_KEY = "migration-test-key"
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + str(tmp_path / "legacy.db")
        UPLOAD_FOLDER = str(tmp_path / "uploads")

    application = create_app(Config)
    with application.app_context():
        upgrade(revision="0007")
        connection = db.engine
        with connection.begin() as c:
            c.execute(
                sa.text("""INSERT INTO "user" (id,type,email,login_nonce,avatar) VALUES
                (41,'company','owner@example.invalid','nonce1','old-logo'),
                (42,'person','learner@example.invalid','nonce2',NULL)""")
            )
            c.execute(
                sa.text(
                    "INSERT INTO company_profile (id,user_id,name,description,plan,course_credits) VALUES (1,41,'Legacy Co','Description','monthly',3)"
                )
            )
            c.execute(
                sa.text(
                    "INSERT INTO person_profile (id,user_id,first_name,last_name) VALUES (1,42,'Learner','Test')"
                )
            )
            c.execute(
                sa.text(
                    """INSERT INTO course (id,company_id,title,level,is_private,invite_token,status,yes_answers)
                VALUES (51,41,'Legacy Course','beginner',0,'invite1','published',''),
                       (52,41,'Legacy Private','beginner',1,'invite2','published','')"""
                )
            )
            c.execute(
                sa.text(
                    """INSERT INTO lesson (id,course_id,"order",title,text,video_filename) VALUES
                (61,51,1,'Lesson','Content',NULL),(62,52,1,'Private','Content','public-video')"""
                )
            )
            c.execute(
                sa.text("INSERT INTO quiz (id,lesson_id,pass_score) VALUES (71,61,70)")
            )
            c.execute(
                sa.text(
                    "INSERT INTO quiz_attempt (id,user_id,quiz_id,score,passed) VALUES (81,42,71,80,1)"
                )
            )
            c.execute(
                sa.text(
                    "INSERT INTO enrollment (id,user_id,course_id,status,current_lesson) VALUES (91,42,51,'completed',2)"
                )
            )
            c.execute(
                sa.text(
                    "INSERT INTO offer (id,company_id,user_id,course_id,text) VALUES (101,41,42,51,'Hello')"
                )
            )
            c.execute(
                sa.text(
                    "INSERT INTO access_request (id,user_id,course_id,status) VALUES (111,42,52,'pending')"
                )
            )
        upgrade(revision="head")
        from database.models import (
            Company,
            Membership,
            Course,
            User,
            Enrollment,
            QuizAttempt,
            Offer,
            AccessRequest,
            Lesson,
        )

        company = db.session.get(Company, 41)
        assert company.owner_id == 41 and company.name == "Legacy Co"
        assert company.avatar == "old-logo" and db.session.get(User, 41).avatar is None
        assert company.plan == "monthly" and company.course_credits == 3
        assert Membership.query.filter_by(company_id=41, user_id=41, role="owner").one()
        assert db.session.get(Course, 51).company_id == 41
        assert db.session.get(Course, 52).access_mode == "request"
        assert db.session.get(Course, 52).status == "draft"
        assert db.session.get(Lesson, 62).video_delivery == "upload"
        assert db.session.get(Enrollment, 91).status == "completed"
        assert db.session.get(QuizAttempt, 81).score == 80
        assert db.session.get(Offer, 101).company_id == 41
        assert db.session.get(AccessRequest, 111).status == "pending"
        assert (
            sa.inspect(db.engine).get_foreign_keys("course")[0]["referred_table"]
            == "company"
        )
        assert db.session.execute(sa.text("PRAGMA foreign_key_check")).all() == []
        # Re-running upgrade is a no-op, not duplicate data creation.
        upgrade(revision="head")
        assert Company.query.count() == 1
        # New company uses an unoccupied ID after explicit legacy IDs.
        created = Company(owner_id=41, name="Second company")
        db.session.add(created)
        db.session.commit()
        assert created.id > 41
        db.session.remove()


def test_upgrade_intermediate_hidden_courses_keeps_links_and_progress(tmp_path):
    class Config:
        TESTING = False
        SECRET_KEY = "migration-test-key"
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + str(tmp_path / "intermediate.db")
        UPLOAD_FOLDER = str(tmp_path / "uploads")

    application = create_app(Config)
    with application.app_context():
        upgrade(revision="0008")
        from database.models import User, Company, Course, Enrollment, AccessRequest

        owner = User(type="person", email="owner@example.invalid")
        learner = User(type="person", email="learner@example.invalid")
        db.session.add_all([owner, learner])
        db.session.flush()
        company = Company(owner_id=owner.id, name="Company")
        db.session.add(company)
        db.session.flush()
        course = Course(
            company_id=company.id,
            title="Previously hidden",
            level="beginner",
            status="published",
            is_private=True,
            access_mode="invite",
        )
        db.session.add(course)
        db.session.flush()
        course_id, token = course.id, course.invite_token
        db.session.add_all(
            [
                Enrollment(user_id=learner.id, course_id=course.id),
                AccessRequest(user_id=owner.id, course_id=course.id, status="pending"),
            ]
        )
        db.session.commit()
        upgrade(revision="head")
        db.session.expire_all()
        course = db.session.get(Course, course_id)
        assert course.access_mode == "request" and course.is_private
        assert course.status == "published" and course.invite_token == token
        assert Enrollment.query.filter_by(course_id=course_id).count() == 1
        assert (
            AccessRequest.query.filter_by(course_id=course_id, status="pending").count()
            == 1
        )
        assert (
            db.session.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == "0009"
        )
        upgrade(revision="head")
        assert Course.query.count() == 1
        db.session.remove()
