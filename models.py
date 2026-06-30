from datetime import datetime
from sqlalchemy.orm import deferred as _deferred
from extensions import db


class Competition(db.Model):
    __tablename__ = "competitions"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Text, nullable=False)
    slug = db.Column(db.Text, unique=True)
    program = db.Column(db.Text)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    editions = db.relationship("Edition", backref="competition", cascade="all, delete-orphan", lazy=True)


class Edition(db.Model):
    __tablename__ = "editions"

    id = db.Column(db.Integer, primary_key=True)
    competition_id = db.Column(db.Integer, db.ForeignKey("competitions.id", ondelete="CASCADE"), nullable=False)
    name = db.Column(db.Text, nullable=False)
    slug = db.Column(db.Text)
    year = db.Column(db.Integer)
    status = db.Column(db.Text, default="aktywna")
    deadline = db.Column(db.Date)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    gdrive_folder_url = db.Column(db.Text)
    gdrive_folder_id = db.Column(db.Text)
    gdrive_synced_at = db.Column(db.DateTime)

    documents = db.relationship("Document", backref="edition", cascade="all, delete-orphan", lazy=True)


class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    edition_id = db.Column(db.Integer, db.ForeignKey("editions.id", ondelete="CASCADE"), nullable=False)
    original_name = db.Column(db.Text, nullable=False)
    stored_path = db.Column(db.Text, nullable=False)
    file_size = db.Column(db.Integer)
    mime_type = db.Column(db.Text)
    version_label = db.Column(db.Text)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text)

    gdrive_file_id = db.Column(db.Text)

    # deferred: large text, loaded on-demand — never needed in list views
    ai_summary = _deferred(db.Column(db.Text))
    ai_description = db.Column(db.Text)
    ai_summary_model = db.Column(db.Text)
    ai_summarized_at = db.Column(db.DateTime)
    ai_summary_status = db.Column(db.Text)
    ai_summary_error = db.Column(db.Text)

    extraction_cache_key  = db.Column(db.Text)
    # deferred: never loaded in list queries — can be several MB per document
    extraction_cache_json = _deferred(db.Column(db.Text(16_777_215)))  # MEDIUMTEXT — large docs exceed TEXT 64 KB limit

    extraction_status      = db.Column(db.Text)   # null | 'pending' | 'done' | 'error'
    extraction_error       = db.Column(db.Text)
    extraction_prompt_hash = db.Column(db.Text)   # md5 of extraction prompt only
    extraction_started_at  = db.Column(db.DateTime)

    ai_summary_started_at  = db.Column(db.DateTime)


class AppSettings(db.Model):
    __tablename__ = "app_settings"

    id = db.Column(db.Integer, primary_key=True)
    gemini_api_key = db.Column(db.Text)
    gemini_model = db.Column(db.Text, default="gemini-2.5-flash")
    gemini_summary_prompt = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    comparison_prompt_extraction = db.Column(db.Text)
    comparison_prompt_comparison = db.Column(db.Text)
    comparison_prompt_summary = db.Column(db.Text)

    google_drive_api_key = db.Column(db.Text)  # deprecated — zastąpione OAuth

    drive_access_token  = db.Column(db.Text)
    drive_refresh_token = db.Column(db.Text)
    drive_token_expiry  = db.Column(db.DateTime)

    # Per-stage config for AI document summary (services/gemini.py)
    doc_summary_model = db.Column(db.Text)
    doc_summary_temperature = db.Column(db.Float)
    doc_summary_max_tokens = db.Column(db.Integer)
    doc_summary_system = db.Column(db.Text)

    # Per-stage config for comparison pipeline (services/comparator.py)
    comparison_prompt_edition = db.Column(db.Text)

    cmp_extraction_model = db.Column(db.Text)
    cmp_extraction_temperature = db.Column(db.Float)
    cmp_extraction_max_tokens = db.Column(db.Integer)
    cmp_extraction_system = db.Column(db.Text)

    cmp_comparison_model = db.Column(db.Text)
    cmp_comparison_temperature = db.Column(db.Float)
    cmp_comparison_max_tokens = db.Column(db.Integer)
    cmp_comparison_system = db.Column(db.Text)

    cmp_summary_model = db.Column(db.Text)
    cmp_summary_temperature = db.Column(db.Float)
    cmp_summary_max_tokens = db.Column(db.Integer)
    cmp_summary_system = db.Column(db.Text)

    cmp_edition_model = db.Column(db.Text)
    cmp_edition_temperature = db.Column(db.Float)
    cmp_edition_max_tokens = db.Column(db.Integer)
    cmp_edition_system = db.Column(db.Text)


class PromptVersion(db.Model):
    __tablename__ = "prompt_versions"

    id = db.Column(db.Integer, primary_key=True)
    prompt_key = db.Column(db.Text, nullable=False)
    content = db.Column(db.Text, nullable=False)
    source = db.Column(db.Text, default="manual")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ComparisonJob(db.Model):
    __tablename__ = "comparison_jobs"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    edition_old_id = db.Column(db.Integer, db.ForeignKey("editions.id", ondelete="SET NULL"), nullable=True)
    edition_new_id = db.Column(db.Integer, db.ForeignKey("editions.id", ondelete="SET NULL"), nullable=True)
    file_mappings_json = db.Column(db.Text)
    # deferred: can be several MB per job — never needed in the jobs list view
    per_file_results_json = _deferred(db.Column(db.Text))
    edition_summary       = _deferred(db.Column(db.Text))
    changes_json          = _deferred(db.Column(db.Text))

    competition_name = db.Column(db.Text)
    doc_old_name = db.Column(db.Text)
    doc_new_name = db.Column(db.Text)
    label_old = db.Column(db.Text)
    label_new = db.Column(db.Text)

    status = db.Column(db.Text, default="pending")
    status_detail = db.Column(db.Text)
    progress_current = db.Column(db.Integer, default=0)
    progress_total = db.Column(db.Integer, default=0)
    error_message = db.Column(db.Text)

    executive_summary = db.Column(db.Text)

    gemini_model_used = db.Column(db.Text)
    prompt_extraction_used = db.Column(db.Text)
    prompt_comparison_used = db.Column(db.Text)
    prompt_summary_used = db.Column(db.Text)

    started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)
    tokens_input = db.Column(db.Integer, default=0)
    tokens_output = db.Column(db.Integer, default=0)
    estimated_cost_usd = db.Column(db.Float, default=0.0)
    pair_lock_at = db.Column(db.DateTime)  # heartbeat: renewed each section during active processing
    skip_redactional = db.Column(db.Boolean, default=False)  # omit ZMIANA_REDAKCYJNA from results
    job_label    = db.Column(db.Text)  # user-editable display name (overrides competition_name in UI)
    requested_by = db.Column(db.Text)
