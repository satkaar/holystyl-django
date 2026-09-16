"""Santé végétale : le registre des traitements et ce qu'on a observé.

Le registre phytosanitaire est le document que réclament le contrôle et la
certification : parcelle, culture, date, produit, AMM, cible, dose, surface.
C'est pourquoi il est tenu ici comme un registre, pas comme un tableau de
bord — et qu'il s'imprime.

Les interventions de type « traitement » déjà saisies dans le journal des
interventions y figurent, signalées comme telles : elles sont trop proches
pour être ignorées, et les ressaisir les compterait deux fois. Elles sont
souvent incomplètes au regard du registre (ni AMM ni cible) : la page le dit
plutôt que de laisser croire à un registre en règle.
"""

from django.utils.translation import gettext_lazy as _


def _campagne(date):
    from parcelles.models import ParcelleCampagne

    return ParcelleCampagne.libelle_courant(date)


def campagnes(exploitation):
    """Les campagnes qui portent un traitement ou une observation."""
    from interventions.models import Intervention

    from .models import ObservationSanitaire, Traitement

    if exploitation is None:
        return []
    vues = set(Traitement.objects.filter(exploitation=exploitation)
               .exclude(campagne="").values_list("campagne", flat=True))
    vues |= set(ObservationSanitaire.objects.filter(exploitation=exploitation)
                .exclude(campagne="").values_list("campagne", flat=True))
    vues |= {_campagne(i.start_time.date()) for i in
             Intervention.objects.filter(exploitation=exploitation,
                                         intervention_type=Intervention.Type.TRAITEMENT)
             .only("start_time")}
    vues.add(_campagne(None))
    return sorted(v for v in vues if v)[::-1]


def traitements(exploitation, campagne):
    """Le registre de la campagne : saisies d'ici, plus les interventions."""
    from interventions.models import Intervention

    from .models import Traitement

    if exploitation is None:
        return []

    registre = []
    for t in (Traitement.objects.filter(exploitation=exploitation, campagne=campagne)
              .select_related("parcelle")):
        registre.append({
            "id": t.pk, "date": t.date, "parcelle": t.parcelle, "culture": t.culture,
            "produit": t.produit, "numero_amm": t.numero_amm,
            "type_cible": t.get_type_cible_display(), "cible": t.cible,
            "dose": t.dose, "unite_dose": t.unite_dose, "surface_ha": t.surface_ha,
            "delai_avant_recolte": t.delai_avant_recolte,
            "recolte_possible_le": t.recolte_possible_le,
            "operateur": t.operateur, "conditions": t.conditions, "notes": t.notes,
            "origine": "saisi", "complet": bool(t.numero_amm and t.cible and t.dose),
        })

    for i in (Intervention.objects.filter(exploitation=exploitation,
                                          intervention_type=Intervention.Type.TRAITEMENT)
              .select_related("parcelle")):
        date = i.start_time.date()
        if _campagne(date) != campagne:
            continue
        registre.append({
            "id": i.pk, "date": date, "parcelle": i.parcelle, "culture": "",
            "produit": i.product or str(_("Produit non précisé")), "numero_amm": "",
            "type_cible": str(_("Non précisé")), "cible": "",
            "dose": None, "unite_dose": i.dose or "", "surface_ha": i.surface,
            "delai_avant_recolte": None, "recolte_possible_le": None,
            "operateur": i.assigned_to.name if i.assigned_to else "",
            "conditions": "", "notes": i.notes,
            "origine": "intervention", "complet": False,
        })

    registre.sort(key=lambda t: t["date"], reverse=True)
    return registre


def observations(exploitation, campagne):
    from .models import ObservationSanitaire

    if exploitation is None:
        return []
    return list(ObservationSanitaire.objects.filter(exploitation=exploitation, campagne=campagne)
                .select_related("parcelle"))


def bilan(exploitation, campagne):
    """Le registre, les observations, et ce qui manque au registre."""
    registre = traitements(exploitation, campagne)
    vues = observations(exploitation, campagne)
    incomplets = [t for t in registre if not t["complet"]]
    return {
        "traitements": registre,
        "observations": vues,
        "incomplets": incomplets,
        "nb_traitements": len(registre),
        "nb_observations": len(vues),
        "parcelles_traitees": len({t["parcelle"].pk for t in registre if t["parcelle"]}),
        "surface_traitee": round(sum(t["surface_ha"] or 0 for t in registre), 2),
        "alertes": [o for o in vues if o.intensite >= 2],
    }
