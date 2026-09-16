"""Bilan économique / ROI et préparation des dossiers de subvention.

Reproduit `bilan.roi` (gains nets = revenus − charges, marges) et l'assemblage
des données de subvention (parité `reports.exportSubvention`).
"""

from dataclasses import asdict, dataclass

from django.db.models import Sum
from django.utils import timezone

from irrigation.models import DtiScore, EnergyLog, WaterMeter

from .models import Charge, Facture, Recolte, Revenu


def prochain_numero(exploitation, modele=Facture, lettre="F") -> str:
    """Numéro suivant, séquentiel par année et par série (F-2026-004, D-2026-002).

    Le numéro ne doit contenir ni espace ni caractère exotique : la règle
    française BR-FR-01 rejette la facture sinon. Ce calcul vit ici et non dans
    une vue : la vente directe émet aussi des factures, et deux implantations
    finiraient par diverger — donc par produire deux fois le même numéro.
    """
    annee = timezone.localdate().year
    prefixe = f"{lettre}-{annee}-"
    if not exploitation:
        return f"{prefixe}001"
    existants = (
        modele.objects.filter(exploitation=exploitation, numero__startswith=prefixe)
        .values_list("numero", flat=True)
    )
    rangs = [int(n.rsplit("-", 1)[-1]) for n in existants if n.rsplit("-", 1)[-1].isdigit()]
    return f"{prefixe}{(max(rangs) + 1) if rangs else 1:03d}"


@dataclass
class Bilan:
    total_revenus: float = 0.0
    total_charges: float = 0.0
    resultat_net: float = 0.0
    surface_ha: float = 0.0
    marge_par_ha: float | None = None
    eau_m3: float = 0.0
    energie_kwh: float = 0.0


def compute_bilan(exploitation, year: int | None = None) -> Bilan:
    if exploitation is None:
        return Bilan()
    charges = Charge.objects.filter(exploitation=exploitation)
    revenus = Revenu.objects.filter(exploitation=exploitation)
    if year:
        charges = charges.filter(date__year=year)
        revenus = revenus.filter(date__year=year)

    total_rev = revenus.aggregate(s=Sum("montant"))["s"] or 0.0
    total_chg = charges.aggregate(s=Sum("montant"))["s"] or 0.0
    surface = exploitation.parcelles.aggregate(s=Sum("area"))["s"] or exploitation.total_area or 0.0
    eau = WaterMeter.objects.filter(exploitation=exploitation).aggregate(s=Sum("volume_m3"))["s"] or 0.0
    energie = EnergyLog.objects.filter(exploitation=exploitation).aggregate(s=Sum("energy_kwh"))["s"] or 0.0

    resultat = total_rev - total_chg
    return Bilan(
        total_revenus=round(total_rev, 2),
        total_charges=round(total_chg, 2),
        resultat_net=round(resultat, 2),
        surface_ha=round(surface, 2),
        marge_par_ha=round(resultat / surface, 2) if surface else None,
        eau_m3=round(eau, 2),
        energie_kwh=round(energie, 2),
    )


def subvention_context(exploitation, export_type: str, year: int | None = None) -> dict:
    """Assemble les données d'un dossier de subvention (PDF certifié)."""
    bilan = compute_bilan(exploitation, year)
    latest_dti = DtiScore.objects.filter(exploitation=exploitation).first()
    return {
        "exploitation": exploitation,
        "export_type": export_type,
        "year": year,
        "bilan": asdict(bilan),
        "dti": latest_dti,
        "parcelles": exploitation.parcelles.all(),
    }


# --- Rendements d'une parcelle ----------------------------------------------------------------
# Les récoltes vivent ici (`Recolte`) : c'est donc ici qu'on les agrège, pour que la fiche
# parcelle, le stock et le bilan lisent tous le même calcul.

def _libelle_campagne(date):
    """Campagne agricole d'une date : septembre → septembre (« 2025/2026 »)."""
    from parcelles.models import ParcelleCampagne

    return ParcelleCampagne.libelle_courant(timezone.localtime(date).date())


def rendements_par_parcelle(parcelle, campagnes_max=5):
    """Ce que la parcelle a produit, campagne par campagne.

    Rendement = kilos récoltés ÷ surface. Sans surface connue, il reste vide plutôt que faux.
    Renvoie la campagne la plus récente en premier.
    """
    recoltes = list(parcelle.recoltes.all())  # ordonnées de la plus récente à la plus ancienne
    surface = parcelle.area or 0
    qualites = dict(Recolte.Qualite.choices)

    par_campagne = {}
    for recolte in recoltes:
        campagne = par_campagne.setdefault(
            _libelle_campagne(recolte.date),
            {"libelle": "", "kg": 0.0, "valorisation": 0.0, "nombre": 0, "qualites": {}},
        )
        campagne["libelle"] = _libelle_campagne(recolte.date)
        campagne["kg"] += recolte.quantite_kg or 0
        campagne["valorisation"] += (recolte.quantite_kg or 0) * (recolte.prix_unitaire or 0)
        campagne["nombre"] += 1
        libelle_qualite = qualites.get(recolte.qualite, recolte.qualite)
        campagne["qualites"][libelle_qualite] = campagne["qualites"].get(libelle_qualite, 0) + (recolte.quantite_kg or 0)

    lignes = sorted(par_campagne.values(), key=lambda c: c["libelle"], reverse=True)[:campagnes_max]
    for ligne in lignes:
        ligne["kg"] = round(ligne["kg"], 1)
        ligne["valorisation"] = round(ligne["valorisation"], 2)
        ligne["rendement"] = round(ligne["kg"] / surface, 1) if surface else None
        ligne["prix_moyen"] = round(ligne["valorisation"] / ligne["kg"], 2) if ligne["kg"] else None
        ligne["qualites"] = sorted(
            ({"libelle": q, "kg": round(kg, 1), "part": round(kg / ligne["kg"] * 100)} for q, kg in ligne["qualites"].items()),
            key=lambda q: q["kg"], reverse=True,
        )

    courante, precedente = (lignes + [None, None])[0], (lignes + [None, None])[1]
    variation = None
    if courante and precedente and precedente["kg"]:
        variation = round((courante["kg"] - precedente["kg"]) / precedente["kg"] * 100)
    return {
        "campagnes": lignes,
        "courante": courante,
        "variation": variation,
        "total_kg": round(sum(r.quantite_kg or 0 for r in recoltes), 1),
        "total_valorisation": round(sum((r.quantite_kg or 0) * (r.prix_unitaire or 0) for r in recoltes), 2),
        "dernieres": recoltes[:5],
        "surface": surface,
    }
