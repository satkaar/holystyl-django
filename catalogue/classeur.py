"""Lecture du classeur catalogue exporté par Cultiveau.

Le fichier fait 25 Mo pour 32 410 lignes, et il grossira. Le lire d'un bloc —
`openpyxl` en mode normal, ou pire un `pandas.read_excel` — construirait
l'intégralité du tableau en mémoire avant la première ligne utile. On le
parcourt donc en flux : un `.xlsx` est une archive ZIP de XML, et
`iterparse` rend une ligne à la fois, sans jamais tenir plus qu'elle.

Ce module ne connaît ni Django ni la base : il transforme un chemin de fichier
en une suite de dictionnaires normalisés. C'est `synchronisation` qui décide
quoi en faire.
"""

from __future__ import annotations

import zipfile
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

#: Colonne du classeur → nom de champ. L'export porte dix-neuf colonnes là où
#: le guide n'en documente que treize : les six dernières, propres à la
#: plateforme logistique, sont pourtant celles qui portent le prix de vente.
COLONNES = {
    "A": "reference",
    "B": "nom",
    "C": "fournisseur",
    "D": "categorie",
    "E": "description",
    "F": "prix_ht",
    "G": "unite",
    "H": "conditionnement",
    "I": "poids_kg",
    "J": "image_url",
    "K": "stock_initial",
    "L": "seuil_alerte",
    "M": "actif",
    "N": "stocke_plateforme",
    "O": "stock_plateforme",
    "P": "cout_stockage",
    "Q": "cout_manutention",
    "R": "marge_logistique",
    "S": "prix_plateforme",
}

DECIMAUX = ("prix_ht", "poids_kg", "cout_stockage", "cout_manutention",
            "marge_logistique", "prix_plateforme")
ENTIERS = ("stock_initial", "seuil_alerte", "stock_plateforme")
BOOLEENS = ("actif", "stocke_plateforme")


class ClasseurInvalide(Exception):
    """Le fichier n'a pas la forme attendue — mieux vaut refuser que deviner."""


def _texte(cellule):
    """Valeur d'une cellule, qu'elle soit inline ou référencée."""
    inline = cellule.find(NS + "is")
    if inline is not None:
        return "".join(inline.itertext())
    valeur = cellule.find(NS + "v")
    return valeur.text if valeur is not None else None


def _colonne(reference):
    """« BC12 » → « BC ». La lettre porte la colonne, le chiffre la ligne."""
    return "".join(c for c in reference if c.isalpha())


def _decimal(brut):
    if brut in (None, ""):
        return None
    try:
        return Decimal(str(brut).replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return None


def _entier(brut):
    if brut in (None, ""):
        return 0
    try:
        return int(float(str(brut).replace(",", ".").strip()))
    except (TypeError, ValueError):
        return 0


def _booleen(brut):
    """« OUI » / « NON », tels que le guide les impose."""
    return str(brut or "").strip().upper() in ("OUI", "1", "TRUE", "VRAI")


def _feuille_catalogue(archive):
    """Chemin XML de la feuille « Catalogue ».

    Le classeur en porte deux — les données et un guide des colonnes — et rien
    ne garantit que l'ordre des feuilles restera celui d'aujourd'hui.
    """
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    cibles = {
        r.get("Id"): r.get("Target")
        for r in rels
    }
    rid_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    for feuille in workbook.iter(NS + "sheet"):
        if (feuille.get("name") or "").strip().lower() == "catalogue":
            cible = cibles.get(feuille.get(rid_ns), "")
            return "xl/" + cible.lstrip("/")
    raise ClasseurInvalide("Aucune feuille « Catalogue » dans le classeur.")


def _lignes_brutes(chemin):
    """Rend chaque ligne du classeur comme un dict {colonne: texte}."""
    with zipfile.ZipFile(chemin) as archive:
        feuille = _feuille_catalogue(archive)
        with archive.open(feuille) as flux:
            for _, element in ET.iterparse(flux, events=("end",)):
                if element.tag != NS + "row":
                    continue
                ligne = {}
                for cellule in element.findall(NS + "c"):
                    colonne = _colonne(cellule.get("r") or "")
                    if colonne:
                        ligne[colonne] = _texte(cellule)
                yield ligne
                # Sans ce vidage, l'arbre entier resterait en mémoire et le
                # gain du parcours en flux serait perdu.
                element.clear()


def entetes_attendues(ligne):
    """Vérifie que l'export n'a pas changé de forme sous nos pieds.

    Une colonne insérée en amont décalerait toutes les suivantes : les prix
    atterriraient dans les unités sans qu'aucune exception ne se lève. On
    compare donc les intitulés, pas seulement leur nombre.
    """
    attendu = ["Référence", "Nom du produit", "Fournisseur", "Catégorie"]
    trouve = [(ligne.get(c) or "").strip() for c in ("A", "B", "C", "D")]
    return trouve == attendu


def lire(chemin):
    """Parcourt le classeur et rend un dict normalisé par produit.

    Les lignes sans référence ni nom sont ignorées : ce sont les lignes vides
    que laisse un tableur en fin de feuille, pas des produits.
    """
    lignes = _lignes_brutes(chemin)

    try:
        entete = next(lignes)
    except StopIteration:
        raise ClasseurInvalide("Classeur vide.")

    if not entetes_attendues(entete):
        raise ClasseurInvalide(
            "Les colonnes du classeur ne sont plus celles attendues : "
            f"{[entete.get(c) for c in ('A', 'B', 'C', 'D')]}")

    for numero, ligne in enumerate(lignes, start=2):
        produit = {
            champ: (ligne.get(colonne) or "").strip()
            for colonne, champ in COLONNES.items()
        }
        if not produit["reference"] and not produit["nom"]:
            continue

        for champ in DECIMAUX:
            produit[champ] = _decimal(produit[champ])
        for champ in ENTIERS:
            produit[champ] = _entier(produit[champ])
        for champ in BOOLEENS:
            produit[champ] = _booleen(produit[champ])

        produit["ligne"] = numero
        yield produit
