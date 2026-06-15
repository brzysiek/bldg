from extensions import db
from models import AppSettings

DEFAULT_PROMPT = """Jesteś ekspertem w analizie dokumentacji konkursów grantowych i programów dofinansowania w Polsce (FENG, PARP, NCBR, NCN i inne).

Przeanalizuj poniższy dokument i przygotuj szczegółowe podsumowanie strukturalne w języku polskim, zawierające:

1. **Typ i cel dokumentu** — co to jest i do czego służy
2. **Najważniejsze informacje** — kluczowe zasady, warunki, wymogi
3. **Terminy i daty** — wszystkie terminy (składania, oceny, realizacji)
4. **Kryteria i punktacja** — jeśli dokument zawiera kryteria oceny, wymień je z wagami
5. **Wymagania formalne** — kto może składać, co jest wymagane, co jest wykluczone
6. **Budżet i dofinansowanie** — kwoty, procenty, limity jeśli podane
7. **Kluczowe definicje i pojęcia** — ważne terminy używane w dokumencie
8. **Na co zwrócić szczególną uwagę** — ryzyka, pułapki, często popełniane błędy (jeśli wynika z treści)

Formatuj odpowiedź w Markdown. Bądź konkretny i precyzyjny — to narzędzie robocze dla osób piszących wnioski.

--- DOKUMENT ---
{document_text}
--- KONIEC DOKUMENTU ---"""


def run_seed():
    if AppSettings.query.count() == 0:
        db.session.add(AppSettings(
            id=1,
            gemini_api_key="",
            gemini_model="gemini-2.5-flash",
            gemini_summary_prompt=DEFAULT_PROMPT,
        ))
        db.session.commit()
