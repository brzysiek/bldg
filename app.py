import os
from flask import Flask, render_template
from extensions import db
from sqlalchemy import text, inspect as sa_inspect

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _migrate_db():
    """Dodaje brakujace kolumny do istniejacych tabel (safe migration)."""
    with db.engine.connect() as conn:
        inspector = sa_inspect(db.engine)

        existing_settings = [c["name"] for c in inspector.get_columns("app_settings")]
        for col_name, col_type in {
            "comparison_prompt_extraction": "TEXT",
            "comparison_prompt_comparison": "TEXT",
            "comparison_prompt_summary":    "TEXT",
        }.items():
            if col_name not in existing_settings:
                conn.execute(text(f"ALTER TABLE app_settings ADD COLUMN {col_name} {col_type}"))

        if "comparison_jobs" in inspector.get_table_names():
            existing_jobs = [c["name"] for c in inspector.get_columns("comparison_jobs")]
            for col, typ in {
                "status_detail":       "TEXT",
                "started_at":          "DATETIME",
                "finished_at":         "DATETIME",
                "tokens_input":        "INTEGER",
                "tokens_output":       "INTEGER",
                "estimated_cost_usd":  "REAL",
            }.items():
                if col not in existing_jobs:
                    conn.execute(text(f"ALTER TABLE comparison_jobs ADD COLUMN {col} {typ}"))

        conn.commit()


def _seed_comparison_prompts():
    """Uzupelnia prompty porownan w AppSettings jesli sa NULL."""
    from models import AppSettings
    from services.comparator import DEFAULT_PROMPT_EXTRACTION, DEFAULT_PROMPT_COMPARISON, DEFAULT_PROMPT_SUMMARY
    settings = db.session.get(AppSettings, 1)
    if settings and not settings.comparison_prompt_extraction:
        settings.comparison_prompt_extraction = DEFAULT_PROMPT_EXTRACTION
        settings.comparison_prompt_comparison = DEFAULT_PROMPT_COMPARISON
        settings.comparison_prompt_summary    = DEFAULT_PROMPT_SUMMARY
        db.session.commit()


def create_app():
    app = Flask(__name__)
    app.secret_key = "grant-docs-secret-key-change-in-prod"

    os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "storage"), exist_ok=True)

    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(BASE_DIR, 'data', 'grants.db')}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    from routes.competitions import bp as comp_bp
    from routes.editions import bp as ed_bp
    from routes.document_types import bp as dt_bp
    from routes.files import bp as files_bp
    from routes.settings import bp as settings_bp
    from routes.comparison import bp as comparison_bp

    app.register_blueprint(comp_bp)
    app.register_blueprint(ed_bp)
    app.register_blueprint(dt_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(comparison_bp)

    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html"), 404

    @app.template_filter("duration_fmt")
    def duration_fmt(seconds):
        if seconds is None:
            return "—"
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s"

    @app.template_filter("from_json_len")
    def from_json_len(value):
        import json as _json
        try:
            data = _json.loads(value or "[]")
            return len(data) if isinstance(data, list) else 0
        except Exception:
            return 0

    with app.app_context():
        db.create_all()
        _migrate_db()
        from seed import run_seed
        run_seed()
        _seed_comparison_prompts()

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, host="0.0.0.0", port=5002)
