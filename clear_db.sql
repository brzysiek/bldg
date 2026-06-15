-- Grant Docs – czyszczenie bazy (zachowuje app_settings)
-- Uruchom: mysql -u grant_docs -p grant_docs < clear_db.sql

USE grant_docs;

-- Dzieci przed rodzicami — bez wyłączania FK
DELETE FROM comparison_jobs;
DELETE FROM documents;
DROP TABLE IF EXISTS document_types;
DELETE FROM editions;
DELETE FROM competitions;
