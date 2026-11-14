"""QR codes de contrôle des billets, partagés par la page et le PDF."""

from django.urls import reverse
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing, Rect
from reportlab.lib.colors import white

from .services import creer_url_absolue


def creer_url_controle_billet(billet):
    chemin = reverse("reservations:controler_billet", kwargs={"reference": billet.reference})
    return creer_url_absolue(chemin)


def creer_qr_code_billet(billet, taille=192):
    """Produit un QR vectoriel avec une bordure blanche pour faciliter le scan."""
    dessin = Drawing(taille, taille)
    dessin.add(Rect(0, 0, taille, taille, fillColor=white, strokeColor=None))
    dessin.add(QrCodeWidget(
        creer_url_controle_billet(billet), barWidth=taille, barHeight=taille,
        barBorder=4, barLevel="M",
    ))
    dessin.hAlign = "CENTER"
    return dessin
