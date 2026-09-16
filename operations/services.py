"""Heures d'engins passées sur une parcelle (affectations du parc matériel)."""

from django.db.models import Count, Sum

from .models import AffectationEngin


def heures_engins_par_parcelle(parcelle):
    """Heures de machine sur la parcelle, au total et par engin."""
    affectations = AffectationEngin.objects.filter(parcelle=parcelle).exclude(heures_utilisees__isnull=True)
    par_engin = [
        {"libelle": ligne["machine__name"] or "—", "heures": round(ligne["heures"] or 0, 1), "nombre": ligne["nombre"]}
        for ligne in affectations.values("machine__name")
        .annotate(heures=Sum("heures_utilisees"), nombre=Count("id"))
        .order_by("-heures")
    ]
    return {"total": round(affectations.aggregate(h=Sum("heures_utilisees"))["h"] or 0, 1), "par_engin": par_engin}
