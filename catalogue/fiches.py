"""Lecture des fiches techniques du Drive par l'IA.

Le classeur porte les prix et les photos ; il ne dit rien des débits, des
pressions ni des portées — sa colonne « Description » est vide sur 32 394 des
32 410 lignes. Cette moitié manquante n'existe que dans les PDF rangés par
fournisseur sur le Drive. Ce module les lit et la rapatrie.

Trois choses distinguent cette lecture des autres du projet.

**Une fiche parle de plusieurs produits.** Une notice Aqua4D couvre les
modèles 30, 40, 50 et 60M ; un catalogue Nelson déroule des dizaines de buses.
L'extraction rend donc une liste, là où lire une pièce d'identité rend un
objet.

**Le rattachement est l'étape risquée, pas l'extraction.** Le modèle lit
correctement « TU H-A 60 » ; le problème est de savoir laquelle des 32 408
lignes du catalogue porte ce nom. On ne rapproche donc que sur des égalités —
référence exacte, puis désignation exacte chez le même fournisseur — et
tout ce qui ne tombe pas juste est laissé de côté et compté. Un rapprochement
approximatif collerait les caractéristiques d'un modèle 60M sur un 30M sans
que personne ne s'en aperçoive.

**Rien n'écrase le classeur.** Les caractéristiques atterrissent dans
`Produit.caracteristiques`, jamais dans les colonnes que la synchronisation
tient à jour. Une fiche mal lue ne peut pas corrompre un prix.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata

from django.utils import timezone

from ia import llm

logger = logging.getLogger(__name__)

MIMES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

#: Vocabulaire des caractéristiques. Le catalogue couvre quarante-quatre
#: familles, des disjoncteurs aux micro-asperseurs : imposer un schéma unique
#: le viderait de sens pour la moitié d'entre elles. On normalise donc les
#: grandeurs qui reviennent partout — celles sur lesquelles on voudra un jour
#: filtrer ou dimensionner — et on laisse le reste sous ses noms d'origine
#: dans `autres`, plutôt que de le perdre.
GRANDEURS = {
    "debit_l_h": "débit en litres par heure",
    "debit_m3_h": "débit en mètres cubes par heure",
    "pression_min_bar": "pression de service minimale en bar",
    "pression_max_bar": "pression de service maximale en bar",
    "pression_nominale_bar": "pression nominale (PN) en bar",
    "portee_m": "portée d'arrosage en mètres",
    "diametre_nominal_mm": "diamètre nominal DN en millimètres",
    "diametre_exterieur_mm": "diamètre extérieur en millimètres",
    "raccordement": "filetage ou type de raccordement",
    "materiau": "matériau principal",
    "temperature_max_c": "température maximale d'emploi en degrés Celsius",
    "filtration_requise_micron": "finesse de filtration exigée en microns",
    "puissance_kw": "puissance en kilowatts",
    "tension_v": "tension d'alimentation en volts",
    "intensite_a": "intensité nominale en ampères",
    "espacement_m": "espacement préconisé entre goutteurs en mètres",
    "couleur": "couleur du repère, quand elle identifie le calibre",
}

#: Grandeurs dont la valeur est un texte et non un nombre. La couleur en fait
#: partie parce qu'elle n'est pas décorative : Rivulis nomme ses buses
#: « Fogger - violet - 5,3 l/h », et c'est le mot « violet » qui distingue un
#: calibre d'un autre dans le catalogue comme sur la fiche.
TEXTUELLES = ("raccordement", "materiau", "couleur")

_PROMPT = (
    "Tu lis une fiche technique, une notice d'installation ou un catalogue de "
    "matériel d'irrigation agricole. Extrais les produits qu'elle décrit et "
    "leurs caractéristiques techniques, en JSON strict.\n"
    "\n"
    "Règle absolue : n'invente jamais une valeur. Mets null pour toute "
    "grandeur que le document ne donne pas explicitement. Une fiche qui ne "
    "mentionne aucune pression doit rendre null, pas une valeur plausible.\n"
    "\n"
    "Clés attendues à la racine :\n"
    "- fournisseur : la marque du matériel, telle qu'elle est écrite\n"
    "- famille : la nature du matériel en quelques mots (« micro-asperseur », "
    "« traitement de l'eau », « canon d'irrigation »…)\n"
    "- produits : la liste des modèles décrits\n"
    "\n"
    "Chaque produit porte :\n"
    "- designation : le nom du modèle, avec TOUT ce qui le distingue des "
    "autres lignes du même tableau — couleur du repère, calibre, débit, "
    "diamètre. Une fiche présentant vingt buses sous le titre « Buse Fogger » "
    "ne décrit pas vingt fois le même produit : reprends pour chacune ce que "
    "sa ligne porte de particulier (« Fogger violet 5,3 l/h »), jamais le seul "
    "titre du tableau.\n"
    "- reference : la référence commerciale si elle figure, sinon null\n"
    "- caracteristiques : un objet dont les clés sont prises dans la liste "
    "ci-dessous, avec des nombres (pas de texte, pas d'unité dans la valeur)\n"
    "- autres : un objet libre pour les caractéristiques utiles qui "
    "n'entrent dans aucune clé de la liste, sous leurs noms d'origine\n"
    "\n"
    "Clés de « caracteristiques » :\n"
    + "\n".join(f"- {cle} : {libelle}" for cle, libelle in GRANDEURS.items())
    + "\n"
    "\n"
    "Si le document présente un tableau de modèles, produis une entrée par "
    "ligne du tableau. Si une grandeur est donnée par plage (« 2 à 4 bar »), "
    "remplis les clés min et max correspondantes. Convertis les unités vers "
    "celles demandées."
)


class FicheIllisible(Exception):
    """Le document n'a pas pu être lu — type non géré ou IA indisponible."""


def mime_de(nom_fichier):
    """Type MIME déduit de l'extension, ou None si le modèle ne sait pas le lire."""
    return MIMES.get(os.path.splitext(nom_fichier)[1].lower())


def lire(data, nom_fichier):
    """Rend l'extraction brute d'une fiche, ou None si rien n'est exploitable.

    Repli silencieux volontaire, comme les autres lectures du projet : une IA
    indisponible ou un PDF illisible laisse le document archivé sans
    caractéristiques, jamais avec des valeurs inventées.
    """
    if not llm.is_configured() or not data:
        return None
    mime = mime_de(nom_fichier)
    if not mime:
        return None
    try:
        brut = llm.extract_json_from_document(data, mime, _PROMPT)
    except Exception:  # noqa: BLE001 — toute erreur IA → repli
        logger.exception("Lecture de la fiche %s impossible", nom_fichier)
        return None
    return _normaliser(brut) if brut else None


def _nombre(valeur):
    """Convertit en float ce qui peut l'être, sinon None.

    Le modèle glisse parfois l'unité dans la valeur (« 4 L/h ») malgré la
    consigne : on récupère le nombre plutôt que de jeter la caractéristique.
    """
    if valeur is None or isinstance(valeur, bool):
        return None
    if isinstance(valeur, (int, float)):
        return float(valeur)
    trouve = re.search(r"-?\d+(?:[.,]\d+)?", str(valeur))
    return float(trouve.group().replace(",", ".")) if trouve else None


def _normaliser(brut):
    """Ramène la sortie du modèle à la forme attendue, en écartant le reste.

    Le modèle peut rendre des clés hors vocabulaire ou des produits sans
    désignation : on ne les laisse pas entrer dans la base sous prétexte
    qu'ils sont arrivés.
    """
    produits = []
    for item in (brut.get("produits") or []):
        if not isinstance(item, dict):
            continue
        designation = (item.get("designation") or "").strip()
        if not designation:
            continue

        caracteristiques = {}
        for cle, valeur in (item.get("caracteristiques") or {}).items():
            if cle not in GRANDEURS or valeur in (None, ""):
                continue
            if cle in TEXTUELLES:
                caracteristiques[cle] = str(valeur).strip()
            else:
                nombre = _nombre(valeur)
                if nombre is not None:
                    caracteristiques[cle] = nombre

        autres = {
            str(c): v for c, v in (item.get("autres") or {}).items()
            if v not in (None, "", [], {})
        } if isinstance(item.get("autres"), dict) else {}

        produits.append({
            "designation": designation,
            "reference": (item.get("reference") or "").strip() or None,
            "caracteristiques": caracteristiques,
            "autres": autres,
        })

    return {
        "fournisseur": (brut.get("fournisseur") or "").strip(),
        "famille": (brut.get("famille") or "").strip(),
        "produits": produits,
    }


# ── Rattachement ──────────────────────────────────────────────────────────

def _cle(texte):
    """Forme comparable d'une désignation : sans accents, casse ni ponctuation.

    « TU H-A 60 » et « tu h a 60 » désignent le même produit ; « TU H-A 600 »
    n'en est pas un. La normalisation efface la mise en forme, jamais les
    chiffres ni l'ordre des mots.
    """
    sans_accent = "".join(
        c for c in unicodedata.normalize("NFD", texte or "")
        if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sans_accent.lower()).split())


#: Formes rencontrées d'une même couleur, ramenées à leur racine. La fiche
#: accorde au féminin (« Violette », « Noire »), le catalogue non
#: (« Fogger - violet »).
_RACINES = {
    "violet": "violet", "violette": "violet",
    "bleu": "bleu", "bleue": "bleu",
    "noir": "noir", "noire": "noir",
    "vert": "vert", "verte": "vert",
    "gris": "gris", "grise": "gris",
    "blanc": "blanc", "blanche": "blanc",
    "rouge": "rouge", "orange": "orange", "jaune": "jaune",
    "marron": "marron", "turquoise": "turquoise", "rose": "rose",
}

_DEBIT = re.compile(r"(\d+(?:[.,]\d+)?)\s*l\s*/\s*h")

#: Mots trop communs pour nommer une famille de matériel. Sans eux, « buse »
#: rapprocherait une buse de fogger d'une buse de canon.
_STOPMOTS = {"buse", "buses", "type", "types", "modele", "modeles", "serie",
             "avec", "pour", "sans", "haute", "basse", "pression", "sortie",
             "sorties", "accessoire", "accessoires", "assemblage", "kit"}


def _themes(texte):
    """Mots qui nomment la famille d'un produit — « fogger », « greenmist ».

    Couleurs, chiffres et mots passe-partout en sont exclus : ce qui reste
    doit distinguer une gamme d'une autre.
    """
    return {mot for mot in _cle(texte).split()
            if len(mot) >= 4 and not mot.isdigit()
            and mot not in _RACINES and mot not in _STOPMOTS}


def _signature_catalogue(nom):
    """(couleur, débit) lus dans le nom d'un produit du catalogue.

    Rivulis nomme ses buses « Fogger - violet - 5,3 l/h » : la couleur et le
    débit y sont, quand la fiche technique les range dans deux colonnes d'un
    tableau. C'est le seul point commun exploitable entre les deux écritures.
    """
    mots = _cle(nom).split()
    couleur = next((_RACINES[m] for m in mots if m in _RACINES), None)
    debit = _DEBIT.search(nom.replace(",", "."))
    if not couleur or not debit:
        return None
    return couleur, round(float(debit.group(1)), 1)


def _signature_fiche(ligne):
    """(couleur, débit) tirés d'une ligne extraite, si les deux y sont.

    La couleur est cherchée dans la désignation autant que dans la clé
    dédiée : d'un appel à l'autre, le modèle range « Violette » tantôt dans
    `caracteristiques`, tantôt seulement dans le nom. Ne dépendre que de la
    clé rendait le rapprochement intermittent sur une fiche pourtant lue
    correctement.
    """
    carac = ligne["caracteristiques"]
    couleur = _RACINES.get(_cle(str(carac.get("couleur") or "")))
    if couleur is None:
        couleur = next((_RACINES[mot] for mot in _cle(ligne["designation"]).split()
                        if mot in _RACINES), None)
    debit = carac.get("debit_l_h")
    if not couleur or debit is None:
        return None
    return couleur, round(float(debit), 1)


def rattacher(document, extraction):
    """Pose les caractéristiques extraites sur les produits du catalogue.

    Rend le compte-rendu : ce qui a été rapproché, et ce qui ne l'a pas été.
    Le second nombre est le plus utile — c'est lui qui dit si une fiche
    apporte vraiment quelque chose au référentiel.
    """
    from .models import Produit

    lignes = extraction.get("produits") or []
    if not lignes:
        return {"rapproches": 0, "orphelins": [], "produits": []}

    # Un seul chargement pour toute la fiche : les fournisseurs vont jusqu'à
    # 12 000 références, mais une requête par ligne de tableau en ferait des
    # dizaines par document.
    perimetre = Produit.objects.exclude(statut=Produit.Statut.RETIRE)
    if document.fournisseur_id:
        perimetre = perimetre.filter(fournisseur_id=document.fournisseur_id)

    par_reference, par_nom, par_signature = {}, {}, {}
    for produit in perimetre.only("id", "reference", "nom", "caracteristiques"):
        par_reference.setdefault(produit.reference.strip().lower(), []).append(produit)
        par_nom.setdefault(_cle(produit.nom), []).append(produit)
        signature = _signature_catalogue(produit.nom)
        if signature:
            par_signature.setdefault(signature, []).append(produit)

    rapproches, orphelins, touches = [], [], []
    for ligne in lignes:
        candidats = []
        if ligne["reference"]:
            candidats = par_reference.get(ligne["reference"].strip().lower(), [])
        if not candidats:
            candidats = par_nom.get(_cle(ligne["designation"]), [])
        if not candidats:
            # Dernier recours : couleur et débit, à l'intérieur d'une même
            # famille. Les trois doivent concorder et ne désigner qu'un
            # produit. Sans la famille, « noir 28 l/h » désigne dix articles
            # Rivulis de gammes différentes — un goutteur hériterait des
            # caractéristiques d'un brumisateur.
            signature = _signature_fiche(ligne)
            if signature:
                familles = _themes(ligne["designation"])
                candidats = [p for p in par_signature.get(signature, [])
                             if _themes(p.nom) & familles]

        # Une désignation qui désigne plusieurs produits ne désigne rien : sans
        # moyen de trancher, poser les caractéristiques sur l'un d'eux serait
        # un tirage au sort.
        if len(candidats) != 1:
            orphelins.append({
                "designation": ligne["designation"],
                "reference": ligne["reference"],
                "motif": "ambigu" if candidats else "inconnu",
                "candidats": len(candidats),
            })
            continue

        produit = candidats[0]
        valeurs = dict(ligne["caracteristiques"])
        if ligne["autres"]:
            valeurs["autres"] = ligne["autres"]
        if not valeurs:
            orphelins.append({
                "designation": ligne["designation"],
                "reference": ligne["reference"],
                "motif": "sans caractéristique",
                "candidats": 1,
            })
            continue

        produit.caracteristiques = valeurs
        produit.caracteristiques_source = document
        touches.append(produit)
        rapproches.append(produit.reference)

    if touches:
        from .models import Produit as P
        P.objects.bulk_update(touches, ["caracteristiques", "caracteristiques_source"],
                              batch_size=500)
        document.produits.add(*touches)

    return {"rapproches": len(touches), "orphelins": orphelins,
            "produits": rapproches}


def analyser(document, data):
    """Lit un document et rattache ce qu'il contient. Rend le compte-rendu.

    L'échec est enregistré sur le document plutôt que levé : une fiche
    illisible parmi cent ne doit pas interrompre la passe, et
    `erreur_extraction` garde la trace de ce qui n'a pas marché.
    """
    extraction = lire(data, document.nom_fichier)
    if extraction is None:
        document.erreur_extraction = (
            "Document illisible ou IA non configurée."
            if not llm.is_configured() else "Extraction sans résultat.")
        document.extrait_le = timezone.now()
        document.save(update_fields=["erreur_extraction", "extrait_le", "updated_at"])
        return {"rapproches": 0, "orphelins": [], "produits": []}

    compte_rendu = rattacher(document, extraction)
    document.extraction = {**extraction, "compte_rendu": compte_rendu}
    document.erreur_extraction = ""
    document.extrait_le = timezone.now()
    document.save(update_fields=["extraction", "erreur_extraction", "extrait_le",
                                 "updated_at"])
    return compte_rendu
