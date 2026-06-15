import os
from flask import Flask, render_template
from extensions import db
from sqlalchemy import text, inspect as sa_inspect

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _migrate_db():
    with db.engine.connect() as conn:
        inspector = sa_inspect(db.engine)
        tables = inspector.get_table_names()

        def add_cols(table, col_map):
            if table not in tables:
                return
            existing = [c["name"] for c in inspector.get_columns(table)]
            for col, typ in col_map.items():
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))

        add_cols("app_settings", {
            "comparison_prompt_extraction":  "TEXT",
            "comparison_prompt_comparison":  "TEXT",
            "comparison_prompt_summary":     "TEXT",
            "google_oauth_client_id":        "TEXT",
            "google_oauth_client_secret":    "TEXT",
            "google_access_token":           "TEXT",
            "google_refresh_token":          "TEXT",
            "google_token_expiry":           "DATETIME",
            "google_user_email":             "TEXT",
        })

        add_cols("editions", {
            "gdrive_folder_url":  "TEXT",
            "gdrive_folder_id":   "TEXT",
            "gdrive_synced_at":   "DATETIME",
        })

        add_cols("documents", {
            "gdrive_file_id": "TEXT",
        })

        add_cols("comparison_jobs", {
            "status_detail":          "TEXT",
            "started_at":             "DATETIME",
            "finished_at":            "DATETIME",
            "tokens_input":           "INTEGER",
            "tokens_output":          "INTEGER",
            "estimated_cost_usd":     "REAL",
            "edition_old_id":         "INTEGER",
            "edition_new_id":         "INTEGER",
            "file_mappings_json":     "TEXT",
            "per_file_results_json":  "TEXT",
            "edition_summary":        "TEXT",
        })

        conn.commit()


def _seed_comparison_prompts():
    from models import AppSettings
    from services.comparator import DEFAULT_PROMPT_EXTRACTION, DEFAULT_PROMPT_COMPARISON, DEFAULT_PROMPT_SUMMARY
    settings = db.session.get(AppSettings, 1)
    if settings and not settings.comparison_prompt_extraction:
        settings.comparison_prompt_extraction = DEFAULT_PROMPT_EXTRACTION
        settings.comparison_prompt_comparison = DEFAULT_PROMPT_COMPARISON
        settings.comparison_prompt_summary = DEFAULT_PROMPT_SUMMARY
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
    from routes.auth import bp as auth_bp

    app.register_blueprint(comp_bp)
    app.register_blueprint(ed_bp)
    app.register_blueprint(dt_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(comparison_bp)
    app.register_blueprint(auth_bp)

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

    @app.template_filter("file_icon")
    def file_icon(filename):
        if not filename:
            return "📎"
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        return {"pdf": "📄", "doc": "📝", "docx": "📝", "xls": "📊", "xlsx": "📊",
                "png": "🖼️", "jpg": "🖼️", "jpeg": "🖼️", "txt": "📃"}.get(ext, "📎")

    @app.template_filter("filesize_fmt")
    def filesize_fmt(size):
        if not size:
            return ""
        if size > 1_048_576:
            return f"{size / 1_048_576:.1f} MB"
        if size > 1024:
            return f"{size / 1024:.0f} KB"
        return f"{size} B"

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
