"""Ce que la parcelle a produit et ce qu'elle a coûté en temps.

Rassemble deux lectures qui vivent dans les apps propriétaires des données — les récoltes
(`finances`) et les heures (`interventions`, `operations`) — et en tire le rapport entre les
deux : ce que rapporte une heure passée sur la parcelle.
"""

from finances.services import rendements_par_parcelle
from interventions.services import heures_par_parcelle
from operations.services import heures_engins_par_parcelle


def _couleur(token, defaut):
    return {"color_token": token, "color": defaut}


def synthese(parcelle):
    """Rendements, heures et fruit du travail, prêts pour le gabarit."""
    rendements = rendements_par_parcelle(parcelle)
    heures = heures_par_parcelle(parcelle)
    engins = heures_engins_par_parcelle(parcelle)

    total_kg = rendements["total_kg"]
    total_heures = heures["total"]
    fruit = {
        "kg_par_heure": round(total_kg / total_heures, 1) if total_heures else None,
        "euros_par_heure": round(rendements["total_valorisation"] / total_heures, 2) if total_heures else None,
        # Ce que coûte une heure de travail rapporté aux kilos sortis : utile pour situer un prix de revient.
        "cout_par_kg": round(heures["cout"] / total_kg, 2) if total_kg and heures["cout"] else None,
    }

    # Graphiques : rendement par campagne (de la plus ancienne à la plus récente) et
    # répartition des heures par type de travail.
    campagnes = list(reversed(rendements["campagnes"]))
    graphique_rendement = {
        "labels": [c["libelle"] for c in campagnes],
        "data": [c["rendement"] if c["rendement"] is not None else c["kg"] for c in campagnes],
        "label": "kg/ha" if rendements["surface"] else "kg",
        "type": "bar",
        **_couleur("--success", "#3f8f7a"),
    } if campagnes else None
    graphique_heures = {
        "labels": [t["libelle"] for t in heures["par_type"]],
        "data": [t["heures"] for t in heures["par_type"]],
        "label": "heures",
        "type": "bar",
        **_couleur("--action", "#0891b2"),
    } if heures["par_type"] else None

    return {
        "rendements": rendements,
        "heures": heures,
        "engins": engins,
        "fruit": fruit,
        "graphique_rendement": graphique_rendement,
        "graphique_heures": graphique_heures,
    }
