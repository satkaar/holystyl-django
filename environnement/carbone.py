"""Empreinte carbone : ce que l'exploitation émet, et ce qu'elle stocke.

Deux mises en garde, qui tiennent le module :

1. **Ce n'est pas un diagnostic certifié.** Un bilan opposable — Label bas
   carbone, CAP'2ER, GES'TIM+ — suppose une méthode validée, un périmètre
   discuté et souvent un audit. Ici, on additionne des quantités par des
   facteurs d'émission : c'est un ordre de grandeur, utile pour voir d'où
   vient le gros du poids, pas pour vendre un crédit carbone.

2. **Les facteurs appartiennent à la ferme.** Ceux proposés ci-dessous sont
   des valeurs courantes, à confirmer avant usage ; chacun porte sa source et
   reste modifiable. Là où la valeur dépend trop du système — cheptel, sols,
   stockage — aucune valeur n'est proposée : mieux vaut un blanc qu'un chiffre
   inventé, et c'est le diagnostic de la ferme qui le comblera.

Trois postes se calculent seuls, à partir de ce que l'application enregistre
déjà : l'électricité des relevés d'irrigation, le carburant sorti du stock et
l'azote minéral du bilan azoté.
"""

from django.utils.translation import gettext_lazy as _

#: Facteurs proposés à la saisie : (clé, libellé, unité, valeur, source).
#: `valeur` à None : la grandeur dépend trop du système pour qu'une valeur par
#: défaut ait un sens — elle vient du diagnostic de la ferme.
FACTEURS = (
    ("gnr", _("Gazole non routier (GNR)"), _("L"), 3.17,
     _("Ordre de grandeur courant (combustion et amont) — à confirmer sur la Base Empreinte de l'ADEME.")),
    ("gazole", _("Gazole routier"), _("L"), 3.17,
     _("Ordre de grandeur courant (combustion et amont) — à confirmer sur la Base Empreinte de l'ADEME.")),
    ("fioul", _("Fioul de chauffage"), _("L"), 3.25,
     _("Ordre de grandeur courant (combustion et amont) — à confirmer sur la Base Empreinte de l'ADEME.")),
    ("electricite", _("Électricité (réseau France)"), _("kWh"), 0.06,
     _("Ordre de grandeur pour le mix français — à confirmer, la valeur bouge d'une année à l'autre.")),
    ("gaz", _("Gaz naturel"), _("kWh"), 0.24,
     _("Ordre de grandeur courant — à confirmer sur la Base Empreinte de l'ADEME.")),
    ("azote_mineral", _("Engrais azoté minéral"), _("kg N"), 8.0,
     _("Fabrication et protoxyde d'azote au champ réunis. Très variable selon le procédé et le sol : "
       "à remplacer par la valeur de votre diagnostic.")),
    ("phyto", _("Produit phytosanitaire"), _("kg de matière active"), None,
     _("Dépend de la molécule : à prendre dans votre diagnostic.")),
    ("cheptel", _("Cheptel"), _("tête et par an"), None,
     _("Dépend de l'espèce, de la conduite et de l'alimentation : valeur à prendre dans un diagnostic "
       "d'élevage (CAP'2ER ou équivalent).")),
    ("aliments", _("Aliments achetés"), _("t"), None,
     _("Dépend de la composition et de l'origine : valeur à prendre dans votre diagnostic.")),
    ("stockage_haies", _("Stockage par les haies"), _("ml et par an"), None,
     _("Dépend de l'essence, de l'âge et de la conduite : valeur à prendre dans un diagnostic "
       "(Label bas carbone ou équivalent).")),
    ("stockage_sols", _("Stockage dans les sols"), _("ha et par an"), None,
     _("Dépend du sol, du climat et des pratiques : valeur à prendre dans votre diagnostic.")),
)


def catalogue():
    """Les facteurs proposés, prêts pour le formulaire."""
    return [{"cle": cle, "libelle": str(libelle), "unite": str(unite),
             "valeur": valeur, "source": str(source)}
            for cle, libelle, unite, valeur, source in FACTEURS]


def _facteur(cle):
    for entree in catalogue():
        if entree["cle"] == cle:
            return entree
    return None


def _campagne(date):
    from parcelles.models import ParcelleCampagne

    return ParcelleCampagne.libelle_courant(date)


def campagnes(exploitation):
    """Les campagnes qui portent quelque chose, la plus récente d'abord."""
    from .azote import campagnes as campagnes_azote
    from .models import PosteCarbone

    if exploitation is None:
        return []
    vues = set(campagnes_azote(exploitation))
    vues |= set(PosteCarbone.objects.filter(exploitation=exploitation)
                .values_list("campagne", flat=True))
    vues.add(_campagne(None))
    return sorted(v for v in vues if v)[::-1]


def energie_kwh(exploitation, campagne):
    """L'électricité de la campagne, sans la compter deux fois.

    Elle est enregistrée à deux endroits : les relevés de compteur
    (`EnergyLog`) et l'énergie portée par chaque session d'irrigation. Quand
    les deux existent, le compteur fait foi — il mesure tout, la session ne
    connaît qu'elle-même. Les additionner gonflerait la facture carbone.
    """
    from irrigation.models import EnergyLog, IrrigationSession

    if exploitation is None:
        return 0.0

    releves = round(sum(log.energy_kwh or 0 for log in
                        EnergyLog.objects.filter(exploitation=exploitation)
                        .only("energy_kwh", "log_date")
                        if _campagne(log.log_date.date()) == campagne), 1)
    if releves:
        return releves
    return round(sum(s.energy_kwh or 0 for s in
                     IrrigationSession.objects.filter(exploitation=exploitation)
                     .only("energy_kwh", "start_time")
                     if _campagne(s.start_time.date()) == campagne), 1)


def _ligne(poste, libelle, quantite, unite, facteur, source, origine, url=None):
    return {
        "poste": poste, "libelle": libelle, "quantite": round(quantite, 1),
        "unite": unite, "facteur": facteur, "source": source,
        "emissions_kg": round(quantite * facteur, 1),
        "stockage": False, "origine": origine, "url": url, "id": None, "notes": "",
    }


def lignes_automatiques(exploitation, campagne):
    """Ce que l'application sait déjà, converti en émissions.

    Rien n'est inventé : l'électricité vient des relevés d'irrigation, le
    carburant des sorties de stock, l'azote minéral du bilan azoté. Ces trois
    postes ne se saisissent donc pas ici — les ressaisir les compterait deux
    fois.
    """
    from stock.models import Article, Mouvement

    from .azote import bilan as bilan_azote

    if exploitation is None:
        return []

    releve = []

    kwh = energie_kwh(exploitation, campagne)
    if kwh:
        f = _facteur("electricite")
        releve.append(_ligne("electricite", str(_("Électricité — irrigation")),
                             kwh, f["unite"], f["valeur"], f["source"], "irrigation",
                             "irrigation:dti"))

    litres = sum(m.quantite or 0 for m in
                 Mouvement.objects.filter(exploitation=exploitation,
                                          type_mouvement=Mouvement.Type.SORTIE,
                                          article__categorie=Article.Categorie.CARBURANT,
                                          article__unite="l").select_related("article")
                 if _campagne(m.date.date()) == campagne)
    if litres:
        f = _facteur("gnr")
        releve.append(_ligne("carburant", str(_("Carburant — sorties de stock")),
                             litres, f["unite"], f["valeur"], f["source"], "stock",
                             "stock:articles"))

    n_mineral = bilan_azote(exploitation, campagne)["n_mineral"]
    if n_mineral:
        f = _facteur("azote_mineral")
        releve.append(_ligne("engrais", str(_("Azote minéral — bilan azoté")),
                             n_mineral, f["unite"], f["valeur"], f["source"], "azote",
                             "environnement:bilan_azote"))

    return releve


def bilan(exploitation, campagne):
    """Émissions et stockage de la campagne, par poste et à l'hectare."""
    from parcelles.models import Parcelle

    from .models import PosteCarbone

    releve = lignes_automatiques(exploitation, campagne)
    saisis = (PosteCarbone.objects.filter(exploitation=exploitation, campagne=campagne)
              if exploitation else PosteCarbone.objects.none())
    for p in saisis:
        releve.append({
            "id": p.pk, "poste": p.poste, "libelle": p.libelle, "quantite": p.quantite,
            "unite": p.unite, "facteur": p.facteur, "source": p.source_facteur,
            "emissions_kg": p.emissions_kg, "stockage": p.est_stockage,
            "origine": "saisi", "url": None, "notes": p.notes,
        })

    emissions = round(sum(l["emissions_kg"] for l in releve if not l["stockage"]), 1)
    stockage = round(sum(l["emissions_kg"] for l in releve if l["stockage"]), 1)
    parcelles = (Parcelle.objects.filter(exploitation=exploitation)
                 if exploitation else Parcelle.objects.none())
    sau = round(sum(p.area or 0 for p in parcelles if p.surface_utile), 2)

    par_poste = {}
    for l in releve:
        if l["stockage"]:
            continue
        entree = par_poste.setdefault(l["poste"], {"poste": l["poste"], "emissions_kg": 0.0})
        entree["emissions_kg"] += l["emissions_kg"]
    libelles = dict(PosteCarbone.Poste.choices)
    for entree in par_poste.values():
        entree["emissions_kg"] = round(entree["emissions_kg"], 1)
        entree["libelle"] = str(libelles.get(entree["poste"], entree["poste"]))
        entree["pct"] = round(entree["emissions_kg"] / emissions * 100) if emissions else 0

    for l in releve:
        l["poste_libelle"] = str(libelles.get(l["poste"], l["poste"]))
    releve.sort(key=lambda l: l["emissions_kg"], reverse=True)
    return {
        "lignes": releve,
        "emissions": emissions,
        "stockage": stockage,
        "net": round(emissions - stockage, 1),
        "sau": sau,
        "emissions_ha": round(emissions / sau, 1) if sau else None,
        "par_poste": sorted(par_poste.values(), key=lambda p: p["emissions_kg"], reverse=True),
        # Un facteur laissé à zéro ne pèse rien : autant le dire, sinon le
        # total paraît complet alors qu'il lui manque une ligne.
        "sans_facteur": [l for l in releve if not l["facteur"]],
    }
