-- Grant Docs – inicjalizacja bazy MySQL
-- Uruchom: mysql -u root -p < init_db.sql

CREATE DATABASE IF NOT EXISTS grant_docs
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'grant_docs'@'localhost' IDENTIFIED BY 'twoje_haslo';
GRANT ALL PRIVILEGES ON grant_docs.* TO 'grant_docs'@'localhost';
FLUSH PRIVILEGES;

USE grant_docs;

CREATE TABLE IF NOT EXISTS competitions (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT,
    program     TEXT,
    description TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS editions (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    competition_id    INT NOT NULL,
    name              TEXT NOT NULL,
    slug              TEXT,
    year              INT,
    status            TEXT DEFAULT 'aktywna',
    deadline          DATE,
    description       TEXT,
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    gdrive_folder_url TEXT,
    gdrive_folder_id  TEXT,
    gdrive_synced_at  DATETIME,
    CONSTRAINT fk_editions_competition
        FOREIGN KEY (competition_id) REFERENCES competitions(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS documents (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    edition_id        INT NOT NULL,
    original_name     TEXT NOT NULL,
    stored_path       TEXT NOT NULL,
    file_size         INT,
    mime_type         TEXT,
    version_label     TEXT,
    uploaded_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    notes             TEXT,
    gdrive_file_id    TEXT,
    ai_summary        LONGTEXT,
    ai_summary_model  TEXT,
    ai_summarized_at  DATETIME,
    ai_summary_status TEXT,
    ai_summary_error  TEXT,
    CONSTRAINT fk_documents_edition
        FOREIGN KEY (edition_id) REFERENCES editions(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS app_settings (
    id                           INT AUTO_INCREMENT PRIMARY KEY,
    gemini_api_key               TEXT,
    gemini_model                 TEXT DEFAULT 'gemini-2.5-flash',
    gemini_summary_prompt        LONGTEXT,
    updated_at                   DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    comparison_prompt_extraction LONGTEXT,
    comparison_prompt_comparison LONGTEXT,
    comparison_prompt_summary    LONGTEXT,
    google_drive_api_key         TEXT,
    google_oauth_client_id       TEXT,
    google_oauth_client_secret   TEXT,
    google_access_token          LONGTEXT,
    google_refresh_token         TEXT,
    google_token_expiry          DATETIME,
    google_user_email            TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS comparison_jobs (
    id                     INT AUTO_INCREMENT PRIMARY KEY,
    created_at             DATETIME DEFAULT CURRENT_TIMESTAMP,
    edition_old_id         INT,
    edition_new_id         INT,
    file_mappings_json     LONGTEXT,
    per_file_results_json  LONGTEXT,
    edition_summary        LONGTEXT,
    competition_name       TEXT,
    doc_old_name           TEXT,
    doc_new_name           TEXT,
    label_old              TEXT,
    label_new              TEXT,
    status                 TEXT DEFAULT 'pending',
    status_detail          TEXT,
    progress_current       INT DEFAULT 0,
    progress_total         INT DEFAULT 0,
    error_message          TEXT,
    changes_json           LONGTEXT,
    executive_summary      LONGTEXT,
    gemini_model_used      TEXT,
    prompt_extraction_used LONGTEXT,
    prompt_comparison_used LONGTEXT,
    prompt_summary_used    LONGTEXT,
    started_at             DATETIME,
    finished_at            DATETIME,
    tokens_input           INT DEFAULT 0,
    tokens_output          INT DEFAULT 0,
    estimated_cost_usd     FLOAT DEFAULT 0.0,
    CONSTRAINT fk_jobs_edition_old
        FOREIGN KEY (edition_old_id) REFERENCES editions(id) ON DELETE SET NULL,
    CONSTRAINT fk_jobs_edition_new
        FOREIGN KEY (edition_new_id) REFERENCES editions(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Wymagany wiersz konfiguracyjny (id=1 jest hardcoded w aplikacji)
INSERT IGNORE INTO app_settings (id, gemini_model) VALUES (1, 'gemini-2.5-flash');
