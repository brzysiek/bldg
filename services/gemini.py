from google import genai
from google.genai import types


def test_connection(api_key: str, model_name: str) -> dict:
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents="Odpowiedz krotko: czy dzialasz? Napisz tylko: TAK.",
        )
        return {"ok": True, "response": response.text.strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def summarize_document(text: str, settings) -> str:
    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = settings.gemini_summary_prompt.replace("{document_text}", text)
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
    )
    return response.text
