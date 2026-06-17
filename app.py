import os
import logging as _logging
import threading
import time as _time
from collections import deque
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request as _req
from extensions import db
from sqlalchemy import text, inspect as sa_inspect
from dotenv import load_dotenv


class _RingBufferHandler(_logging.Handler):
    def __init__(self, capacity=2000):
        super().__init__()
        self._buf = deque(maxlen=capacity)
        self.setFormatter(_logging.Formatter("%(asctime)s"))

    def emit(self, record):
        try:
            self._buf.append({
                "ts": record.created,
                "level": record.levelname,
                "name": record.name,
                "msg": record.getMessage(),
                "time_str": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
            })
        except Exception:
            pass  # never raise from a log handler

    def records(self, since: float = 0.0):
        return [r for r in self._buf if r["ts"] > since]


_log_buffer = _RingBufferHandler(2000)

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

_MYSQL_COL_TYPES = {
    "TEXT":     "TEXT",
    "DATETIME": "DATETIME",
    "INTEGER":  "INT",
    "REAL":     "FLOAT",
}

STATUS_LABELS_PL = {
    "queued":            "W kolejce",
    "pending":           "Oczekuje",
    "extracting":        "Ekstrakcja tekstu",
    "chunking":          "Analiza struktury",
    "comparing":         "Porównywanie",
    "awaiting_summary":  "Gotowe do podsumowania",
    "summarizing":       "Podsumowanie",
    "done":              "Zakończono",
    "error":             "Błąd analizy",
    "cancelled":         "Anulowano",
}


def _mysql_uri():
    host     = os.environ.get("MYSQL_HOST",     "localhost")
    port     = os.environ.get("MYSQL_PORT",     "3306")
    user     = os.environ.get("MYSQL_USER",     "root")
    password = os.environ.get("MYSQL_PASSWORD", "")
    database = os.environ.get("MYSQL_DATABASE", "grant_docs")
    # connect_timeout prevents infinite hangs when MySQL is slow/overloaded on shared hosting
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4&connect_timeout=10"


def _migrate_db():
    with db.engine.connect() as conn:
        inspector = sa_inspect(db.engine)
        tables = inspector.get_table_names()

        def add_cols(table, col_map):
            if table not in tables:
                return
            existing = {c["name"] for c in inspector.get_columns(table)}
            for col, typ in col_map.items():
                if col not in existing:
                    sql_type = _MYSQL_COL_TYPES.get(typ, typ)
                    try:
                        conn.execute(text(f"ALTER TABLE `{table}` ADD COLUMN `{col}` {sql_type}"))
                    except Exception as exc:
                        # MySQL error 1060 = "Duplicate column name" — another worker already
                        # ran this ALTER TABLE concurrently (race between min_instances workers
                        # both starting at the same time). Safe to ignore.
                        if "1060" in str(exc) or "Duplicate column" in str(exc):
                            _logging.getLogger(__name__).debug(
                                "_migrate_db: column %s.%s already added by another worker, skipping",
                                table, col,
                            )
                        else:
                            raise

        add_cols("app_settings", {
            "comparison_prompt_extraction":  "TEXT",
            "comparison_prompt_comparison":  "TEXT",
            "comparison_prompt_summary":     "TEXT",
            "google_drive_api_key":          "TEXT",
            # Per-stage AI config
            "doc_summary_model":             "TEXT",
            "doc_summary_temperature":       "REAL",
            "doc_summary_max_tokens":        "INTEGER",
            "doc_summary_system":            "TEXT",
            "comparison_prompt_edition":     "TEXT",
            "cmp_extraction_model":          "TEXT",
            "cmp_extraction_temperature":    "REAL",
            "cmp_extraction_max_tokens":     "INTEGER",
            "cmp_extraction_system":         "TEXT",
            "cmp_comparison_model":          "TEXT",
            "cmp_comparison_temperature":    "REAL",
            "cmp_comparison_max_tokens":     "INTEGER",
            "cmp_comparison_system":         "TEXT",
            "cmp_summary_model":             "TEXT",
            "cmp_summary_temperature":       "REAL",
            "cmp_summary_max_tokens":        "INTEGER",
            "cmp_summary_system":            "TEXT",
            "cmp_edition_model":             "TEXT",
            "cmp_edition_temperature":       "REAL",
            "cmp_edition_max_tokens":        "INTEGER",
            "cmp_edition_system":            "TEXT",
        })
        add_cols("editions", {
            "gdrive_folder_url":  "TEXT",
            "gdrive_folder_id":   "TEXT",
            "gdrive_synced_at":   "DATETIME",
        })
        add_cols("documents", {
            "gdrive_file_id":        "TEXT",
            "ai_description":        "TEXT",
            "extraction_cache_key":  "TEXT",
            "extraction_cache_json": "TEXT",
        })

        # Migracja: document_type_id → edition_id
        if "documents" in tables:
            doc_cols = {c["name"] for c in inspector.get_columns("documents")}
            if "document_type_id" in doc_cols and "edition_id" not in doc_cols:
                conn.execute(text("ALTER TABLE `documents` ADD COLUMN `edition_id` INT"))
                if "document_types" in tables:
                    conn.execute(text(
                        "UPDATE `documents` d "
                        "JOIN `document_types` dt ON d.document_type_id = dt.id "
                        "SET d.edition_id = dt.edition_id"
                    ))
                conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
                conn.execute(text("ALTER TABLE `documents` DROP COLUMN `document_type_id`"))
                conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
                conn.commit()
            if "document_types" in inspector.get_table_names():
                conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
                conn.execute(text("DROP TABLE IF EXISTS `document_types`"))
                conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
                conn.commit()

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
            "pair_lock_at":           "DATETIME",
            "skip_redactional":       "TINYINT(1)",
        })
        conn.commit()


def _seed_comparison_prompts():
    from models import AppSettings
    from services.comparator import DEFAULT_PROMPT_EXTRACTION, DEFAULT_PROMPT_COMPARISON, DEFAULT_PROMPT_SUMMARY
    from services.prompt_history import record_prompt_version
    settings = db.session.get(AppSettings, 1)
    if settings and not settings.comparison_prompt_extraction:
        settings.comparison_prompt_extraction = DEFAULT_PROMPT_EXTRACTION
        settings.comparison_prompt_comparison = DEFAULT_PROMPT_COMPARISON
        settings.comparison_prompt_summary    = DEFAULT_PROMPT_SUMMARY
        record_prompt_version("comparison_prompt_extraction", DEFAULT_PROMPT_EXTRACTION, source="seed")
        record_prompt_version("comparison_prompt_comparison", DEFAULT_PROMPT_COMPARISON, source="seed")
        record_prompt_version("comparison_prompt_summary", DEFAULT_PROMPT_SUMMARY, source="seed")
        db.session.commit()
    if settings and not settings.comparison_prompt_edition:
        from services.comparator import DEFAULT_PROMPT_EDITION_SUMMARY
        settings.comparison_prompt_edition = DEFAULT_PROMPT_EDITION_SUMMARY
        record_prompt_version("comparison_prompt_edition", DEFAULT_PROMPT_EDITION_SUMMARY, source="seed")
        db.session.commit()


def _cleanup_stale_jobs(timeout_minutes: float) -> int:
    """Marks in-progress jobs older than timeout_minutes as error. Requires app context."""
    from models import ComparisonJob
    cutoff = datetime.utcnow() - timedelta(minutes=timeout_minutes)
    in_progress = ["pending", "comparing", "extracting", "chunking", "awaiting_summary", "summarizing"]

    stale = ComparisonJob.query.filter(ComparisonJob.status.in_(in_progress)).all()
    cleaned = 0
    for job in stale:
        ref = job.started_at or job.created_at
        if ref and ref < cutoff:
            job.status = "error"
            job.error_message = (
                f"Przekroczono limit czasu ({timeout_minutes:.0f} min). "
                "Proces zostal przerwany automatycznie przez monitor zadan."
            )
            job.finished_at = datetime.utcnow()
            cleaned += 1

    if cleaned:
        db.session.commit()

    return cleaned


def _start_job_monitor(app):
    """Daemon thread that sweeps stale jobs every 5 minutes."""
    timeout_minutes = float(os.environ.get("COMPARISON_TIMEOUT_MINUTES", "60"))

    def _run():
        _time.sleep(60)  # give app time to fully start before first sweep
        while True:
            try:
                with app.app_context():
                    _cleanup_stale_jobs(timeout_minutes)
            except Exception:
                pass
            _time.sleep(300)

    threading.Thread(target=_run, daemon=True, name="job-monitor").start()


def create_app():
    from _build import BUILD
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "grant-docs-secret-key-change-in-prod")

    log_level = getattr(_logging, os.environ.get("LOG_LEVEL", "INFO").upper(), _logging.INFO)
    _logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    root_logger = _logging.getLogger()
    root_logger.setLevel(log_level)
    if _log_buffer not in root_logger.handlers:
        _log_buffer.setLevel(_logging.DEBUG)
        root_logger.addHandler(_log_buffer)

    os.makedirs(os.path.join(BASE_DIR, "storage"), exist_ok=True)

    app.config["SQLALCHEMY_DATABASE_URI"] = _mysql_uri()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db_uri = app.config["SQLALCHEMY_DATABASE_URI"]
    engine_opts: dict = {"pool_pre_ping": True}
    if "mysql" in db_uri:
        engine_opts.update({
            "pool_recycle": 55,    # recycle before MySQL wait_timeout (often 60s on shared hosting)
            "pool_timeout": 10,    # give up waiting for a free pool slot after 10s (prevents hang)
            "pool_size": 2,        # match Passenger min_instances — no point in a larger pool
            "max_overflow": 1,     # allow 1 extra connection burst
        })
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = engine_opts

    db.init_app(app)

    from routes.competitions import bp as comp_bp
    from routes.editions import bp as ed_bp
    from routes.files import bp as files_bp
    from routes.settings import bp as settings_bp
    from routes.comparison import bp as comparison_bp
    from routes.placeholders import bp as placeholders_bp

    app.register_blueprint(comp_bp)
    app.register_blueprint(ed_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(comparison_bp)
    app.register_blueprint(placeholders_bp)

    @app.route("/logs")
    def logs_page():
        return render_template("logs/index.html")

    @app.route("/api/logs")
    def api_logs():
        since = float(_req.args.get("since", 0))
        return jsonify(_log_buffer.records(since=since))

    @app.route("/api/health")
    def api_health():
        summarize_routes = [
            str(rule) for rule in app.url_map.iter_rules()
            if "summarize" in str(rule)
        ]
        import subprocess, shlex
        try:
            commit = subprocess.check_output(
                shlex.split("git rev-parse --short HEAD"), text=True
            ).strip()
        except Exception:
            commit = "unknown"
        return jsonify({
            "ok": True,
            "commit": commit,
            "summarize_routes": sorted(summarize_routes),
        })

    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def internal_error(e):
        _logging.getLogger(__name__).error(
            "Nieobsłużony błąd 500  path=%s  %s",
            _req.path, e, exc_info=True,
        )
        if (_req.is_json
                or _req.headers.get("X-Requested-With") == "XMLHttpRequest"
                or "application/json" in _req.headers.get("Accept", "")):
            from flask import jsonify as _jsonify
            return _jsonify({"ok": False, "error": str(e)}), 500
        # Fallback HTML — pokazuje rzeczywisty komunikat błędu
        msg = _logging.escape(str(e)) if hasattr(_logging, "escape") else str(e).replace("<", "&lt;")
        return (
            f'<h1>Błąd serwera (500)</h1>'
            f'<p style="font-family:monospace;color:red">{msg}</p>'
            f'<p><a href="/">Powrót</a></p>'
        ), 500

    app.jinja_env.globals["BUILD"] = BUILD

    @app.template_filter("status_pl")
    def status_pl_filter(status):
        return STATUS_LABELS_PL.get(status or "", status or "—")

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
                "xlsm": "📊", "ppt": "📋", "pptx": "📋",
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
        # Sweep any jobs left hanging from previous server run
        timeout_minutes = float(os.environ.get("COMPARISON_TIMEOUT_MINUTES", "60"))
        _cleanup_stale_jobs(timeout_minutes)

    _start_job_monitor(app)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, host="0.0.0.0", port=5002)
