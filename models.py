from datetime import datetime
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

    # Google Drive
    gdrive_folder_url = db.Column(db.Text)
    gdrive_folder_id = db.Column(db.Text)
    gdrive_synced_at = db.Column(db.DateTime)

    document_types = db.relationship("DocumentType", backref="edition", cascade="all, delete-orphan", lazy=True)


class DocumentType(db.Model):
    __tablename__ = "document_types"

    id = db.Column(db.Integer, primary_key=True)
    edition_id = db.Column(db.Integer, db.ForeignKey("editions.id", ondelete="CASCADE"), nullable=False)
    name = db.Column(db.Text, nullable=False)
    slug = db.Column(db.Text)
    order_index = db.Column(db.Integer, default=0)
    description = db.Column(db.Text)

    documents = db.relationship("Document", backref="document_type", cascade="all, delete-orphan", lazy=True)


class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    document_type_id = db.Column(db.Integer, db.ForeignKey("document_types.id", ondelete="CASCADE"), nullable=False)
    original_name = db.Column(db.Text, nullable=False)
    stored_path = db.Column(db.Text, nullable=False)
    file_size = db.Column(db.Integer)
    mime_type = db.Column(db.Text)
    version_label = db.Column(db.Text)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text)

    # Google Drive
    gdrive_file_id = db.Column(db.Text)

    ai_summary = db.Column(db.Text)
    ai_summary_model = db.Column(db.Text)
    ai_summarized_at = db.Column(db.DateTime)
    ai_summary_status = db.Column(db.Text)
    ai_summary_error = db.Column(db.Text)


class AppSettings(db.Model):
    __tablename__ = "app_settings"

    id = db.Column(db.Integer, primary_key=True)
    gemini_api_key = db.Column(db.Text)
    gemini_model = db.Column(db.Text, default="gemini-2.5-flash")
    gemini_summary_prompt = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Comparison prompts
    comparison_prompt_extraction = db.Column(db.Text)
    comparison_prompt_comparison = db.Column(db.Text)
    comparison_prompt_summary = db.Column(db.Text)

    # Google Drive OAuth
    google_oauth_client_id = db.Column(db.Text)
    google_oauth_client_secret = db.Column(db.Text)
    google_access_token = db.Column(db.Text)
    google_refresh_token = db.Column(db.Text)
    google_token_expiry = db.Column(db.DateTime)
    google_user_email = db.Column(db.Text)


class ComparisonJob(db.Model):
    __tablename__ = "comparison_jobs"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Edition-based (new flow)
    edition_old_id = db.Column(db.Integer, db.ForeignKey("editions.id", ondelete="SET NULL"), nullable=True)
    edition_new_id = db.Column(db.Integer, db.ForeignKey("editions.id", ondelete="SET NULL"), nullable=True)
    file_mappings_json = db.Column(db.Text)    # [{old_doc_id, new_doc_id, old_name, new_name}, ...]
    per_file_results_json = db.Column(db.Text) # [{idx, old_name, new_name, changes, summary}, ...]
    edition_summary = db.Column(db.Text)

    # Legacy (single file pair)
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

    changes_json = db.Column(db.Text)
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
