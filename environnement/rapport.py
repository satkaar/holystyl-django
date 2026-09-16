"""Rapport environnemental : une page pour tout ce que les autres savent déjà.

Ce module ne calcule presque rien de neuf. Il va chercher l'eau dans
l'irrigation, l'azote et le carbone dans leurs bilans, la biodiversité dans
ses fiches et l'alignement dans la Taxonomie, puis les met côte à côte pour
une campagne. C'est ce qui manque à l'exploitant quand une coopérative, une
banque ou un acheteur demande « où en êtes-vous ? » : les chiffres existent,
mais éparpillés sur six écrans.

Une rubrique sans donnée le dit (`renseigne: False`) plutôt que d'afficher un
zéro : un zéro se lit comme une performance, un blanc comme un travail à
faire.
"""

from django.utils.translation import gettext_lazy as _


def _campagne(date):
    from parcelles.models import ParcelleCampagne

    return ParcelleCampagne.libelle_courant(date)


def _annee_de(campagne):
    """L'année d'ouverture d'une campagne « 2025/2026 » : 2025."""
    try:
        return int(str(campagne).split("/")[0])
    except (ValueError, IndexError):
        return None


def campagnes(exploitation):
    from .carbone import campagnes as campagnes_carbone

    return campagnes_carbone(exploitation)


def _eau(exploitation, campagne):
    """Volumes irrigués et énergie de pompage sur la campagne."""
    from irrigation.models import IrrigationSession, WaterQuota

    from .carbone import energie_kwh

    sessions = [s for s in IrrigationSession.objects.filter(exploitation=exploitation)
                if _campagne(s.start_time.date()) == campagne]
    volume = round(sum(s.volume_delivered_m3 or 0 for s in sessions), 1)
    # Même source que le bilan carbone : compteur d'abord, sessions à défaut.
    energie = energie_kwh(exploitation, campagne)

    annee = _annee_de(campagne)
    quota = (WaterQuota.objects.filter(exploitation=exploitation, year=annee).first()
             if annee else None)
    plafond = quota.total_quota_m3 if quota else getattr(exploitation, "water_quota_m3", None)

    return {
        "renseigne": bool(sessions or energie),
        "volume_m3": volume,
        "energie_kwh": energie,
        "sessions": len(sessions),
        "quota_m3": plafond,
        "pct_quota": round(volume / plafond * 100) if plafond else None,
        "kwh_par_m3": round(energie / volume, 3) if volume else None,
    }


def _biodiversite(exploitation, campagne):
    from .models import Biodiversite

    fiches = [f for f in Biodiversite.objects.filter(exploitation=exploitation)
              .select_related("parcelle") if _campagne(f.date) == campagne]
    scores = [f.score for f in fiches if f.score is not None]
    return {
        "renseigne": bool(fiches),
        "fiches": len(fiches),
        "score_moyen": round(sum(scores) / len(scores)) if scores else None,
        "haies_ml": round(sum(f.haies_ml or 0 for f in fiches), 1),
        "jachere_ha": round(sum(f.jachere_ha or 0 for f in fiches), 2),
        "parcelles_suivies": len({f.parcelle_id for f in fiches}),
    }


def _taxonomie(exploitation, campagne):
    from .models import ActiviteTaxonomie

    annee = _annee_de(campagne)
    fiches = list(ActiviteTaxonomie.objects.filter(exploitation=exploitation, campagne=annee)) if annee else []
    total_ca = sum(f.chiffre_affaires or 0 for f in fiches)
    aligne_ca = sum(f.chiffre_affaires or 0 for f in fiches if f.statut == "aligne")
    return {
        "renseigne": bool(fiches),
        "annee": annee,
        "fiches": len(fiches),
        "alignees": sum(1 for f in fiches if f.statut == "aligne"),
        "pct_ca_aligne": round(aligne_ca / total_ca * 100) if total_ca else None,
    }


def synthese(exploitation, campagne):
    """Les rubriques du rapport, prêtes à l'affichage comme à l'impression."""
    from parcelles.models import Parcelle

    from exploitations import certifications as referentiel_certifs

    from .azote import bilan as bilan_azote
    from .carbone import bilan as bilan_carbone

    parcelles = (Parcelle.objects.filter(exploitation=exploitation)
                 if exploitation else Parcelle.objects.none())
    sau = round(sum(p.area or 0 for p in parcelles if p.surface_utile), 2)

    azote = bilan_azote(exploitation, campagne)
    carbone = bilan_carbone(exploitation, campagne)

    return {
        "campagne": campagne,
        "exploitation": exploitation,
        "sau": sau,
        "parcelles": parcelles.count(),
        "certifications": [
            referentiel_certifs.LIBELLES[c] for c in referentiel_certifs.nettoyer(
                getattr(exploitation, "certifications_detenues", []) or [])
        ],
        "eau": _eau(exploitation, campagne) if exploitation else {"renseigne": False},
        "azote": {
            "renseigne": bool(azote["lignes"]),
            **azote,
        },
        "carbone": {
            "renseigne": bool(carbone["lignes"]),
            **carbone,
        },
        "biodiversite": _biodiversite(exploitation, campagne) if exploitation else {"renseigne": False},
        "taxonomie": _taxonomie(exploitation, campagne) if exploitation else {"renseigne": False},
    }


def rubriques_manquantes(synth):
    """Ce qui n'est pas renseigné, pour le dire en tête plutôt qu'en creux."""
    libelles = {
        "eau": _("Eau"), "azote": _("Azote"), "carbone": _("Carbone"),
        "biodiversite": _("Biodiversité"), "taxonomie": _("Taxonomie EU"),
    }
    return [str(libelle) for cle, libelle in libelles.items()
            if not synth.get(cle, {}).get("renseigne")]
