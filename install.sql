-- ============================================================
-- Grant Docs — instalacja bazy danych od zera (MySQL / utf8mb4)
-- Użycie: mysql -u USER -p DATABASE_NAME < install.sql
-- ============================================================

SET NAMES utf8mb4;
SET foreign_key_checks = 0;

-- ------------------------------------------------------------
-- competitions
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS competitions (
    id         INT           NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name       TEXT          NOT NULL,
    slug       TEXT,
    program    TEXT,
    description TEXT,
    created_at DATETIME      DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_competitions_slug (slug(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- editions
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS editions (
    id               INT      NOT NULL AUTO_INCREMENT PRIMARY KEY,
    competition_id   INT      NOT NULL,
    name             TEXT     NOT NULL,
    slug             TEXT,
    year             INT,
    status           VARCHAR(50) DEFAULT 'aktywna',
    deadline         DATE,
    description      TEXT,
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    gdrive_folder_url TEXT,
    gdrive_folder_id  TEXT,
    gdrive_synced_at  DATETIME,
    CONSTRAINT fk_editions_competition
        FOREIGN KEY (competition_id) REFERENCES competitions (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- documents
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    id                     INT           NOT NULL AUTO_INCREMENT PRIMARY KEY,
    edition_id             INT           NOT NULL,
    original_name          TEXT          NOT NULL,
    stored_path            TEXT          NOT NULL,
    file_size              INT,
    mime_type              TEXT,
    version_label          TEXT,
    uploaded_at            DATETIME      DEFAULT CURRENT_TIMESTAMP,
    notes                  TEXT,
    gdrive_file_id         TEXT,

    -- AI summary
    ai_summary             TEXT,
    ai_description         TEXT,
    ai_summary_model       TEXT,
    ai_summarized_at       DATETIME,
    ai_summary_status      TEXT,
    ai_summary_error       TEXT,

    -- AI extraction / segmentation
    extraction_cache_key   TEXT,
    extraction_cache_json  MEDIUMTEXT,
    extraction_status      TEXT,
    extraction_error       TEXT,
    extraction_prompt_hash TEXT,

    CONSTRAINT fk_documents_edition
        FOREIGN KEY (edition_id) REFERENCES editions (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- app_settings  (zawsze jeden wiersz: id=1)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_settings (
    id                           INT    NOT NULL AUTO_INCREMENT PRIMARY KEY,
    gemini_api_key               TEXT,
    gemini_model                 VARCHAR(100) DEFAULT 'gemini-2.5-flash',
    gemini_summary_prompt        TEXT,
    updated_at                   DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    comparison_prompt_extraction TEXT,
    comparison_prompt_comparison TEXT,
    comparison_prompt_summary    TEXT,
    google_drive_api_key         TEXT,

    -- Per-stage: podsumowanie dokumentu
    doc_summary_model            TEXT,
    doc_summary_temperature      DOUBLE,
    doc_summary_max_tokens       INT,
    doc_summary_system           TEXT,

    -- Per-stage: porównanie edycji
    comparison_prompt_edition    TEXT,

    cmp_extraction_model         TEXT,
    cmp_extraction_temperature   DOUBLE,
    cmp_extraction_max_tokens    INT,
    cmp_extraction_system        TEXT,

    cmp_comparison_model         TEXT,
    cmp_comparison_temperature   DOUBLE,
    cmp_comparison_max_tokens    INT,
    cmp_comparison_system        TEXT,

    cmp_summary_model            TEXT,
    cmp_summary_temperature      DOUBLE,
    cmp_summary_max_tokens       INT,
    cmp_summary_system           TEXT,

    cmp_edition_model            TEXT,
    cmp_edition_temperature      DOUBLE,
    cmp_edition_max_tokens       INT,
    cmp_edition_system           TEXT,

    drive_access_token           TEXT,
    drive_refresh_token          TEXT,
    drive_token_expiry           DATETIME
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Seed: pusty rekord — aplikacja uzupełni prompt przy pierwszym uruchomieniu
INSERT INTO app_settings (id, gemini_api_key, gemini_model)
VALUES (1, '', 'gemini-2.5-flash')
ON DUPLICATE KEY UPDATE id = id;

-- ------------------------------------------------------------
-- prompt_versions
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prompt_versions (
    id         INT      NOT NULL AUTO_INCREMENT PRIMARY KEY,
    prompt_key TEXT     NOT NULL,
    content    TEXT     NOT NULL,
    source     VARCHAR(50) DEFAULT 'manual',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- comparison_jobs
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS comparison_jobs (
    id                    INT           NOT NULL AUTO_INCREMENT PRIMARY KEY,
    created_at            DATETIME      DEFAULT CURRENT_TIMESTAMP,

    edition_old_id        INT,
    edition_new_id        INT,
    file_mappings_json    TEXT,
    per_file_results_json MEDIUMTEXT,
    edition_summary       TEXT,
    changes_json          MEDIUMTEXT,

    competition_name      TEXT,
    doc_old_name          TEXT,
    doc_new_name          TEXT,
    label_old             TEXT,
    label_new             TEXT,

    status                VARCHAR(50)   DEFAULT 'pending',
    status_detail         TEXT,
    progress_current      INT           DEFAULT 0,
    progress_total        INT           DEFAULT 0,
    error_message         TEXT,

    executive_summary     TEXT,

    gemini_model_used     TEXT,
    prompt_extraction_used TEXT,
    prompt_comparison_used TEXT,
    prompt_summary_used   TEXT,

    started_at            DATETIME,
    finished_at           DATETIME,
    tokens_input          INT           DEFAULT 0,
    tokens_output         INT           DEFAULT 0,
    estimated_cost_usd    DOUBLE        DEFAULT 0.0,
    pair_lock_at          DATETIME,
    skip_redactional      TINYINT(1)    DEFAULT 0,
    job_label             TEXT,

    CONSTRAINT fk_jobs_edition_old
        FOREIGN KEY (edition_old_id) REFERENCES editions (id) ON DELETE SET NULL,
    CONSTRAINT fk_jobs_edition_new
        FOREIGN KEY (edition_new_id) REFERENCES editions (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET foreign_key_checks = 1;
