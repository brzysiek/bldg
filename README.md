# Grant Docs Manager

Webowa aplikacja do zarządzania dokumentacją konkursów grantowych i wniosków o dofinansowanie.

## Uruchomienie

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Otwórz http://localhost:5000

Przejdź do ⚙️ Ustawienia i wpisz klucz Gemini API, aby włączyć funkcję podsumowań AI.

## Funkcje

- Hierarchia: **Konkurs → Edycja → Typ dokumentu → Pliki**
- Upload plików (PDF, DOCX, XLSX, TXT, CSV, obrazy)
- Pobieranie plików
- Podsumowania AI dokumentów (Google Gemini)
- Panel konfiguracji Gemini API

## Stack

- **Backend:** Python + Flask + SQLAlchemy
- **Baza danych:** SQLite (`data/grants.db`)
- **Frontend:** Jinja2 + Tailwind CSS (CDN)
- **AI:** Google Gemini API
