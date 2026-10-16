import os

from flask import Flask, render_template
from flask_login import LoginManager
from flask_wtf.csrf import CSRFError, CSRFProtect

from core.config import BASE_DIR, Config
from backend.i18n import _ as translate
from database import db, migrate

csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = "main.home"
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "info"
# the "Please log in" message is translated at the moment it is shown
login_manager.localize_callback = translate


@login_manager.user_loader
def load_user(user_id):
    from database.models import User

    return db.session.get(User, int(user_id))


def create_app(config_class=Config):
    # Pages and styles live in the frontend/ folder
    app = Flask(
        __name__,
        template_folder=os.path.join(BASE_DIR, "frontend", "templates"),
        static_folder=os.path.join(BASE_DIR, "frontend", "static"),
    )
    app.config.from_object(config_class)
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError(
            'Set SECRET_KEY in .env. Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
        )
    if app.config.get("APP_ENV") == "production" and (
        not app.config.get("SECRET_KEY")
        or app.config["SECRET_KEY"] == "dev-change-me-in-production"
    ):
        raise RuntimeError("Set a unique SECRET_KEY before production startup.")

    if app.config.get("APP_ENV") == "production":
        from urllib.parse import urlsplit

        public_url = urlsplit(app.config.get("PUBLIC_URL", ""))
        if (
            len(app.config["SECRET_KEY"]) < 32
            or public_url.scheme != "https"
            or not public_url.netloc
        ):
            raise RuntimeError(
                "Production needs a SECRET_KEY of at least 32 characters and an HTTPS PUBLIC_URL."
            )

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    # absolute path, so migrations are found from any folder the server is started in
    migrate.init_app(app, db, directory=os.path.join(BASE_DIR, "migrations"))
    csrf.init_app(app)
    login_manager.init_app(app)

    # EN / RU: _() and the other helpers in every template (backend/i18n.py)
    from backend import i18n

    i18n.init_app(app)

    # "Translate" button next to texts written by users (backend/translator.py)
    from backend import translator

    translator.init_app(app)

    from backend.routes.site_auth import bp as auth_bp
    from backend.routes.main import bp as main_bp
    from backend.routes.courses import bp as courses_bp
    from backend.routes.builder import bp as builder_bp
    from backend.routes.quiz import bp as quiz_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(courses_bp)
    app.register_blueprint(builder_bp)
    app.register_blueprint(quiz_bp)
    from backend.routes.workspaces import bp as workspaces_bp

    app.register_blueprint(workspaces_bp)

    @login_manager.unauthorized_handler
    def require_login():
        from flask import flash, redirect, request, session, url_for
        from backend.i18n import safe_next_url

        # Never replay a POST automatically after authentication.
        target = request.full_path if request.method == "GET" else request.referrer
        session["after_login"] = safe_next_url(target, url_for("main.account"))
        flash(translate("Please log in to continue."), "info")
        return redirect(url_for("main.home", auth="login"))

    @app.context_processor
    def inject_globals():
        from datetime import datetime

        from flask_login import current_user
        from backend.access import owns_company, can_edit_course
        from database.models import Membership

        memberships = (
            Membership.query.filter_by(user_id=current_user.id).all()
            if current_user.is_authenticated
            else []
        )
        return {
            "now_year": datetime.now().year,
            "workspace_memberships": memberships,
            "active_company": (
                current_user.company if current_user.is_authenticated else None
            ),
            "owns_company": owns_company,
            "can_edit_course": can_edit_course,
        }

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(CSRFError)
    def csrf_error(e):
        return render_template("errors/403.html"), 403

    with app.app_context():
        import database.models  # noqa: F401  (registers the tables)

        if app.config.get("TESTING"):
            # Tests use a throw-away in-memory database: no migrations needed.
            db.create_all()
        # Non-test databases are upgraded explicitly with: flask --app run db upgrade

    @app.errorhandler(409)
    def conflict(error):
        return render_template("errors/conflict.html", message=error.description), 409

    @app.after_request
    def private_response_headers(response):
        from flask import request
        from flask_login import current_user

        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.endpoint == "static":
            return response
        if current_user.is_authenticated or request.path.startswith(
            ("/login/", "/signup/confirm/", "/invitations/", "/course/private/")
        ):
            response.headers["Cache-Control"] = "private, no-store"
        return response

    return app
