from extensions import db
from models import PromptVersion


def record_prompt_version(prompt_key, content, source="manual"):
    if not content:
        return
    last = (
        PromptVersion.query
        .filter_by(prompt_key=prompt_key)
        .order_by(PromptVersion.id.desc())
        .first()
    )
    if last and last.content == content:
        return
    db.session.add(PromptVersion(prompt_key=prompt_key, content=content, source=source))


def get_prompt_history(prompt_key, limit=20):
    return (
        PromptVersion.query
        .filter_by(prompt_key=prompt_key)
        .order_by(PromptVersion.created_at.desc(), PromptVersion.id.desc())
        .limit(limit)
        .all()
    )
