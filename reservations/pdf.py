"""Export en mémoire des billets déjà autorisés par le parcours de consultation."""

from io import BytesIO
from pathlib import Path
from threading import Lock
from xml.sax.saxutils import escape

import reportlab
from django.utils import timezone
from django.utils.formats import date_format
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A5
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepInFrame, PageBreak, Paragraph, SimpleDocTemplate, Spacer


# ReportLab partage des ressources internes : une génération à la fois par processus.
_VERROU_PDF = Lock()
_POLICES = Path(reportlab.__file__).parent / "fonts"
pdfmetrics.registerFont(TTFont("LGC", str(_POLICES / "Vera.ttf")))
pdfmetrics.registerFont(TTFont("LGC-Gras", str(_POLICES / "VeraBd.ttf")))


def _decorer_page(canvas, document):
    largeur, hauteur = A5
    canvas.saveState()
    canvas.setFillColor(HexColor("#18181b"))
    canvas.rect(0, hauteur - 32 * mm, largeur, 32 * mm, fill=1, stroke=0)
    canvas.setFillColor(HexColor("#f87171"))
    canvas.setFont("LGC-Gras", 17)
    canvas.drawString(18 * mm, hauteur - 16 * mm, "Le Grand Cinéma")
    canvas.setFillColor(HexColor("#ffffff"))
    canvas.setFont("LGC", 9)
    canvas.drawString(18 * mm, hauteur - 24 * mm, "Votre billet dématérialisé")
    canvas.setFillColor(HexColor("#71717a"))
    canvas.setFont("LGC", 8)
    canvas.drawString(18 * mm, 12 * mm, "Le cinéma se partage.")
    canvas.restoreState()


def generer_pdf_billets(reservation, billets):
    """Retourne un PDF contenant une page par billet, sans écriture en base."""
    with _VERROU_PDF:
        buffer = BytesIO()
        document = SimpleDocTemplate(
            buffer, pagesize=A5, leftMargin=18 * mm, rightMargin=18 * mm,
            topMargin=42 * mm, bottomMargin=22 * mm,
            title="Vos billets — Le Grand Cinéma", author="Le Grand Cinéma",
        )
        normal = ParagraphStyle(
            "normal", fontName="LGC", fontSize=11, leading=16,
            textColor=HexColor("#18181b"), spaceAfter=14,
        )
        titre = ParagraphStyle(
            "titre", parent=normal, fontName="LGC-Gras", fontSize=18,
            leading=23, spaceAfter=20,
        )
        label = ParagraphStyle(
            "label", parent=normal, fontSize=9, leading=13,
            textColor=HexColor("#71717a"), spaceAfter=4,
        )
        place_style = ParagraphStyle(
            "place", parent=normal, fontName="LGC-Gras", fontSize=20,
            leading=26, backColor=HexColor("#facc15"), borderPadding=10,
            spaceBefore=12, spaceAfter=24,
        )
        reference = ParagraphStyle(
            "reference", parent=normal, fontSize=8, leading=12, spaceAfter=20,
        )
        seance = reservation.seance
        debut = timezone.localtime(seance.debut)
        date_seance = escape(date_format(debut, "l d F Y"))
        heure_seance = escape(date_format(debut, "H:i"))
        pages = []
        for numero, billet in enumerate(billets, start=1):
            place = billet.reservation_place.place
            contenu = [
                Paragraph(f"BILLET {numero} SUR {len(billets)}", label),
                Paragraph(escape(f"Place {place.rangee}{place.numero}"), place_style),
                Paragraph("VOTRE SÉANCE", label),
                Paragraph(escape(seance.film.titre), titre),
                Paragraph("Date et heure", label),
                Paragraph(f"{date_seance}<br/>à {heure_seance}", normal),
                Paragraph("Salle", label),
                Paragraph(escape(seance.salle.nom), normal),
                Spacer(1, 8),
                Paragraph("Référence du billet", label),
                Paragraph(escape(str(billet.reference)), reference),
                Paragraph("Présentez ce billet à l’entrée de la salle.", label),
            ]
            if numero > 1:
                pages.append(PageBreak())
            # Les titres longs restent sur la page du billet correspondant.
            pages.append(KeepInFrame(
                document.width, document.height, contenu, mode="shrink", vAlign="TOP",
            ))
        document.build(pages, onFirstPage=_decorer_page, onLaterPages=_decorer_page)
        return buffer.getvalue()
