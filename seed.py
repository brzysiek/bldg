from datetime import date
from extensions import db
from models import Competition, Edition, DocumentType, AppSettings
from utils import slugify

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
    if Competition.query.count() > 0:
        return

    # Seed AppSettings
    if AppSettings.query.count() == 0:
        settings = AppSettings(
            id=1,
            gemini_api_key="",
            gemini_model="gemini-2.5-flash",
            gemini_summary_prompt=DEFAULT_PROMPT,
        )
        db.session.add(settings)

    # Competition 1
    c1 = Competition(
        name="FENG 2.1 – Badania przemysłowe i prace rozwojowe",
        slug=slugify("FENG 2.1 – Badania przemysłowe i prace rozwojowe"),
        program="FENG",
    )
    db.session.add(c1)
    db.session.flush()

    e1 = Edition(
        competition_id=c1.id,
        name="Nabór I/2025",
        slug=slugify("Nabor I 2025"),
        year=2025,
        status="aktywna",
        deadline=date(2025, 9, 30),
    )
    db.session.add(e1)
    db.session.flush()

    for i, name in enumerate(["Regulamin konkursu", "Kryteria oceny", "Wzory dokumentów", "FAQ"]):
        db.session.add(DocumentType(edition_id=e1.id, name=name, slug=slugify(name), order_index=i))

    e2 = Edition(
        competition_id=c1.id,
        name="Nabór II/2024",
        slug=slugify("Nabor II 2024"),
        year=2024,
        status="archiwalna",
    )
    db.session.add(e2)
    db.session.flush()

    for i, name in enumerate(["Regulamin konkursu", "Kryteria oceny"]):
        db.session.add(DocumentType(edition_id=e2.id, name=name, slug=slugify(name), order_index=i))

    # Competition 2
    c2 = Competition(
        name="PARP – Ścieżka SMART",
        slug=slugify("PARP – Ścieżka SMART"),
        program="PARP",
    )
    db.session.add(c2)
    db.session.flush()

    e3 = Edition(
        competition_id=c2.id,
        name="Runda 4/2025",
        slug=slugify("Runda 4 2025"),
        year=2025,
        status="planowana",
        deadline=date(2025, 12, 1),
    )
    db.session.add(e3)
    db.session.flush()

    for i, name in enumerate(["Regulamin", "Instrukcja wypełniania wniosku", "Załączniki"]):
        db.session.add(DocumentType(edition_id=e3.id, name=name, slug=slugify(name), order_index=i))

    db.session.commit()
