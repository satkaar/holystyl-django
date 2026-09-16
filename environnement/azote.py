"""Bilan azoté : ce qui a été apporté, et ce que la directive Nitrates plafonne.

Le plafond de 170 kg ne porte ni sur l'azote total, ni sur un cumul de
plusieurs campagnes : il vise le seul azote **organique issu des effluents
d'élevage**, rapporté à la surface épandable et à une campagne. C'est
pourquoi le calcul vit ici plutôt que dans un gabarit : une jauge qui compare
la mauvaise somme au mauvais dénominateur rassure à tort.

Les fertigations déjà saisies dans Agronomie comptent comme des apports
minéraux : les ressaisir ici serait les compter deux fois, les ignorer
fausserait le bilan. Leur champ `azote_n` est une dose à l'hectare — c'est ce
que demande le formulaire de fertigation.
"""

from django.utils.translation import gettext_lazy as _

#: Directive Nitrates 91/676/CEE, annexe III : kg d'azote organique issu des
#: effluents d'élevage, par hectare épandable et par an, en zone vulnérable.
PLAFOND_EFFLUENTS = 170


def _campagne(date):
    from parcelles.models import ParcelleCampagne

    return ParcelleCampagne.libelle_courant(date)


def campagnes(exploitation):
    """Les campagnes qui portent au moins un apport, la plus récente d'abord."""
    from agronomie.models import Fertigation

    from .models import ApportAzote

    if exploitation is None:
        return []
    vues = set(
        ApportAzote.objects.filter(exploitation=exploitation)
        .exclude(campagne="").values_list("campagne", flat=True)
    )
    vues |= {_campagne(f.date.date()) for f in
             Fertigation.objects.filter(exploitation=exploitation).only("date")}
    vues.add(_campagne(None))
    return sorted(vues, reverse=True)


def lignes(exploitation, campagne):
    """Les apports d'une campagne, saisis ici ou repris de la fertigation.

    Une ligne reprise n'est pas modifiable ici : elle appartient à l'écran qui
    l'a créée, et la renvoyer vers lui vaut mieux que d'en tenir deux copies.
    """
    from agronomie.models import Fertigation

    from .models import ApportAzote

    if exploitation is None:
        return []

    releve = []
    for a in (ApportAzote.objects.filter(exploitation=exploitation, campagne=campagne)
              .select_related("parcelle")):
        releve.append({
            "id": a.pk, "date": a.date, "parcelle": a.parcelle,
            "nature": a.nature, "nature_libelle": a.get_nature_display(),
            "produit": a.produit, "dose_kg_ha": a.dose_kg_ha, "teneur_n_pct": a.teneur_n_pct,
            "n_kg_ha": a.n_kg_ha, "surface_ha": a.surface_ha, "n_total": a.n_total,
            "effluent": a.est_effluent, "notes": a.notes, "origine": "saisi",
        })

    for f in (Fertigation.objects.filter(exploitation=exploitation)
              .select_related("parcelle")):
        date = f.date.date()
        if _campagne(date) != campagne or not f.azote_n:
            continue
        surface = f.parcelle.area
        releve.append({
            "id": f.pk, "date": date, "parcelle": f.parcelle,
            "nature": "mineral", "nature_libelle": _("Fertigation"),
            "produit": f.produit, "dose_kg_ha": None, "teneur_n_pct": None,
            "n_kg_ha": round(f.azote_n, 2), "surface_ha": surface,
            "n_total": round(f.azote_n * (surface or 0), 1),
            "effluent": False, "notes": f.notes, "origine": "fertigation",
        })

    releve.sort(key=lambda l: (l["date"], l["parcelle"].name), reverse=True)
    return releve


def bilan(exploitation, campagne):
    """Totaux de la campagne : par nature, par parcelle, et face au plafond."""
    from parcelles.models import Parcelle

    releve = lignes(exploitation, campagne)
    parcelles = (Parcelle.objects.filter(exploitation=exploitation)
                 if exploitation else Parcelle.objects.none())
    # Surface épandable : à défaut d'un zonage parcellaire, la SAU déclarée.
    sau = round(sum(p.area or 0 for p in parcelles if p.surface_utile), 2)

    n_total = round(sum(l["n_total"] for l in releve), 1)
    n_effluents = round(sum(l["n_total"] for l in releve if l["effluent"]), 1)
    n_organique_autre = round(sum(l["n_total"] for l in releve
                                  if l["nature"] == "organique_autre"), 1)
    n_mineral = round(n_total - n_effluents - n_organique_autre, 1)
    effluents_ha = round(n_effluents / sau, 1) if sau else None

    par_parcelle = {}
    for l in releve:
        p = par_parcelle.setdefault(l["parcelle"], {
            "parcelle": l["parcelle"], "n_total": 0.0, "n_effluents": 0.0, "apports": 0})
        p["n_total"] += l["n_total"]
        p["n_effluents"] += l["n_total"] if l["effluent"] else 0
        p["apports"] += 1
    for p in par_parcelle.values():
        surface = p["parcelle"].area
        p["n_total"] = round(p["n_total"], 1)
        p["n_effluents"] = round(p["n_effluents"], 1)
        p["n_ha"] = round(p["n_total"] / surface, 1) if surface else None
        p["effluents_ha"] = round(p["n_effluents"] / surface, 1) if surface else None
        # Le plafond s'apprécie sur l'exploitation ; à la parcelle, il alerte.
        p["depasse"] = p["effluents_ha"] is not None and p["effluents_ha"] > PLAFOND_EFFLUENTS

    return {
        "lignes": releve,
        "sau": sau,
        "n_total": n_total,
        "n_mineral": n_mineral,
        "n_effluents": n_effluents,
        "n_organique_autre": n_organique_autre,
        "n_total_ha": round(n_total / sau, 1) if sau else None,
        "effluents_ha": effluents_ha,
        "plafond": PLAFOND_EFFLUENTS,
        "pct_plafond": min(100, round(effluents_ha / PLAFOND_EFFLUENTS * 100)) if effluents_ha else 0,
        "depasse": effluents_ha is not None and effluents_ha > PLAFOND_EFFLUENTS,
        "par_parcelle": sorted(par_parcelle.values(), key=lambda p: p["parcelle"].name),
    }
