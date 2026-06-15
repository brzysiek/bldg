from extensions import db
from models import AppSettings

DEFAULT_PROMPT = """Jesteś ekspertem w analizie dokumentacji konkursów grantowych i programów dofinansowania w Polsce (FENG, PARP, NCBR, NCN i inne).

Przeanalizuj poniższy dokument i odpowiedz WYŁĄCZNIE w formacie JSON (bez bloku kodu, bez dodatkowego tekstu):

{
  "opis": "1–4 słowa opisujące typ dokumentu (np. regulamin naboru, ogłoszenie o naborze, harmonogram, kryteria oceny, wzór umowy, lista sprawdzająca)",
  "podsumowanie": "Jeden akapit (5–8 zdań) w języku polskim zawierający: typ i cel dokumentu, najważniejsze warunki uczestnictwa, kluczowe wymogi formalne, terminy oraz kwoty dofinansowania jeśli podane. Pisz zwięźle i konkretnie — to narzędzie robocze dla osób piszących wnioski."
}

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
