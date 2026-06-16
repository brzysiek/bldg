from flask import Blueprint, render_template

bp = Blueprint("placeholders", __name__)


@bp.route("/questions")
def questions():
    return render_template(
        "placeholder.html",
        title="Pytania do dokumentacji",
        icon="❓",
        description=(
            "Tutaj będzie można zadawać pytania dotyczące dokumentacji konkursowej "
            "i otrzymywać odpowiedzi generowane na podstawie analizy zgromadzonych dokumentów."
        ),
    )


@bp.route("/applications")
def applications():
    return render_template(
        "placeholder.html",
        title="Generowanie wniosków",
        icon="📝",
        description=(
            "Tutaj będzie można automatycznie generować wnioski o dofinansowanie "
            "na podstawie dokumentacji konkursowej zgromadzonej w systemie."
        ),
    )
