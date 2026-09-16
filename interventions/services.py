"""Temps passé sur une parcelle — agrégats du journal des interventions.

Les heures de travail humain vivent dans `Intervention.duration_hours` : c'est donc ici
qu'on les additionne, pour que la fiche parcelle et les futurs écrans comptent pareil.
"""

from django.db.models import Count, Sum

from .models import Intervention


def heures_par_parcelle(parcelle):
    """Heures travaillées sur la parcelle : total, par type de travail et par personne.

    Les interventions annulées ne comptent pas ; celles sans durée saisie non plus, mais
    elles sont dénombrées à part pour que le total ne paraisse pas faux.
    """
    faites = Intervention.objects.filter(parcelle=parcelle).exclude(status=Intervention.Status.ANNULEE)
    chiffrees = faites.exclude(duration_hours__isnull=True)
    types = dict(Intervention.Type.choices)

    par_type = [
        {"libelle": types.get(ligne["intervention_type"], ligne["intervention_type"]),
         "heures": round(ligne["heures"] or 0, 1), "nombre": ligne["nombre"]}
        for ligne in chiffrees.values("intervention_type")
        .annotate(heures=Sum("duration_hours"), nombre=Count("id"))
        .order_by("-heures")
    ]
    par_personne = [
        {"libelle": ligne["assigned_to__name"] or "—", "heures": round(ligne["heures"] or 0, 1),
         "nombre": ligne["nombre"]}
        for ligne in chiffrees.values("assigned_to__name")
        .annotate(heures=Sum("duration_hours"), nombre=Count("id"))
        .order_by("-heures")
    ]

    total = round(chiffrees.aggregate(h=Sum("duration_hours"))["h"] or 0, 1)
    surface = parcelle.area or 0
    return {
        "total": total,
        "par_hectare": round(total / surface, 1) if surface else None,
        "par_type": par_type,
        "par_personne": par_personne,
        "cout": round(float(chiffrees.aggregate(c=Sum("cost"))["c"] or 0), 2),
        "nombre": faites.count(),
        "sans_duree": faites.filter(duration_hours__isnull=True).count(),
        "dernieres": list(faites.select_related("assigned_to").order_by("-start_time")[:5]),
    }
