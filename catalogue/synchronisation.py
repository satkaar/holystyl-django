"""Réconciliation du référentiel avec le classeur du Drive.

Le Drive est la source de vérité : cette base n'importe pas, elle se met en
conformité. La question posée à chaque passage n'est donc pas « que
créer ? » mais « qu'est-ce qui a changé depuis la dernière fois ? ».

Trois exigences en découlent.

**Rejouable sans dégât.** Relancer une synchronisation sur un classeur
inchangé ne doit rien modifier — ni horodatage, ni statut. C'est ce qui permet
de la programmer sans crainte et de la relancer après un incident.

**Aucune requête par ligne.** À 32 000 produits, un `get_or_create` par ligne
ferait 32 000 allers-retours. Tout se joue en trois requêtes : on charge
l'existant en mémoire, on compare, on écrit en lots.

**Une disparition n'est pas une suppression.** Un produit absent du classeur
passe en `retire`. Les DTI qui le citent continuent de s'afficher, et un
produit revenu au catalogue redevient actif sans avoir perdu ses
caractéristiques ni ses fiches.
"""

from __future__ import annotations

import logging
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from . import classeur
from .models import Categorie, Fournisseur, Produit, SynchroDrive

logger = logging.getLogger(__name__)

#: Champs recopiés du classeur vers le produit. `description` en fait partie
#: bien qu'elle soit vide partout aujourd'hui : le jour où Cultiveau la
#: remplira, elle arrivera sans changement de code.
CHAMPS = (
    "description", "prix_ht", "unite", "conditionnement", "poids_kg",
    "image_url", "stock_initial", "seuil_alerte", "stocke_plateforme",
    "stock_plateforme", "cout_stockage", "cout_manutention",
    "marge_logistique", "prix_plateforme",
)

#: Taille des lots d'écriture. Assez grand pour que le nombre de requêtes
#: reste négligeable, assez petit pour ne pas bâtir une requête que la base
#: refuserait.
LOT = 1000


def _slug_unique(nom, modele, longueur):
    """Slug dérivé du nom, suffixé si un homonyme l'occupe déjà."""
    base = slugify(nom)[:longueur - 6] or "sans-nom"
    slug = base
    suffixe = 2
    while modele.objects.filter(slug=slug).exists():
        slug = f"{base}-{suffixe}"
        suffixe += 1
    return slug


def _referentiels(lignes_fournisseurs, lignes_categories):
    """Assure l'existence des fournisseurs et catégories, et les indexe par nom.

    Ils sont peu nombreux — neuf et quarante-quatre — mais créés à la volée :
    le guide du classeur annonce explicitement qu'une catégorie inconnue est
    « créée auto si inexistante ».
    """
    fournisseurs = {f.nom: f for f in Fournisseur.objects.all()}
    for nom in sorted(lignes_fournisseurs):
        if nom and nom not in fournisseurs:
            fournisseurs[nom] = Fournisseur.objects.create(
                nom=nom, slug=_slug_unique(nom, Fournisseur, 140))

    categories = {c.nom: c for c in Categorie.objects.all()}
    for nom in sorted(lignes_categories):
        if nom and nom not in categories:
            categories[nom] = Categorie.objects.create(
                nom=nom, slug=_slug_unique(nom, Categorie, 160))

    return fournisseurs, categories


def _differe(produit, ligne, fournisseur, categorie):
    """Vrai si la ligne du classeur dit autre chose que ce qu'on a en base.

    Comparer avant d'écrire évite de repousser `updated_at` sur 32 000 lignes
    à chaque passage : sans cela, « qu'est-ce qui a bougé cette semaine ? »
    n'aurait plus de réponse.
    """
    if produit.fournisseur_id != fournisseur.pk:
        return True
    if produit.categorie_id != (categorie.pk if categorie else None):
        return True
    statut_attendu = Produit.Statut.ACTIF if ligne["actif"] else Produit.Statut.INACTIF
    if produit.statut != statut_attendu:
        return True
    return any(getattr(produit, champ) != ligne[champ] for champ in CHAMPS)


def _appliquer(produit, ligne, fournisseur, categorie):
    produit.fournisseur = fournisseur
    produit.categorie = categorie
    produit.statut = Produit.Statut.ACTIF if ligne["actif"] else Produit.Statut.INACTIF
    for champ in CHAMPS:
        setattr(produit, champ, ligne[champ])
    return produit


@transaction.atomic
def synchroniser(chemin_classeur, empreinte=""):
    """Met le référentiel en conformité avec le classeur, et rend la synchro.

    Toute la passe tient dans une transaction : un classeur tronqué ou une
    ligne illisible ne doit pas laisser un catalogue à moitié retiré.
    """
    synchro = SynchroDrive.objects.create(empreinte_classeur=empreinte)
    maintenant = timezone.now()
    collisions, rejets = {}, []

    # ── 1. Lire le classeur ───────────────────────────────────────────────
    # Une passe unique : les 32 000 lignes tiennent en mémoire, pas les 25 Mo
    # de XML qui les portent.
    lignes = {}
    for ligne in classeur.lire(chemin_classeur):
        if not ligne["reference"] or not ligne["nom"]:
            rejets.append({"ligne": ligne["ligne"], "motif": "référence ou nom manquant"})
            continue
        cle = (ligne["reference"], ligne["nom"])
        if cle in lignes:
            # Deux lignes strictement identiques : un doublon d'export, la
            # seconde n'apporte rien.
            collisions.setdefault(ligne["reference"], []).append(ligne["ligne"])
            continue
        lignes[cle] = ligne

    # Références portant plusieurs produits distincts. Le guide du classeur
    # annonce la référence comme unique ; elle ne l'est pas. On ne tranche pas
    # à leur place — on les signale.
    par_reference = {}
    for reference, nom in lignes:
        par_reference.setdefault(reference, []).append(nom)
    homonymes = {r: noms for r, noms in par_reference.items() if len(noms) > 1}

    fournisseurs, categories = _referentiels(
        {l["fournisseur"] for l in lignes.values()},
        {l["categorie"] for l in lignes.values() if l["categorie"]},
    )

    # ── 2. Comparer à l'existant ──────────────────────────────────────────
    existants = {(p.reference, p.nom): p for p in Produit.objects.all()}

    a_creer, a_modifier, inchanges = [], [], 0
    for cle, ligne in lignes.items():
        fournisseur = fournisseurs[ligne["fournisseur"]]
        categorie = categories.get(ligne["categorie"])
        produit = existants.get(cle)

        if produit is None:
            nouveau = Produit(reference=cle[0], nom=cle[1], vu_le=maintenant)
            a_creer.append(_appliquer(nouveau, ligne, fournisseur, categorie))
        elif _differe(produit, ligne, fournisseur, categorie):
            produit.vu_le = maintenant
            a_modifier.append(_appliquer(produit, ligne, fournisseur, categorie))
        else:
            # Vu, mais identique : seul `vu_le` bouge, et il est mis à jour en
            # lot plus bas pour ne pas toucher `updated_at`.
            inchanges += 1

    # ── 3. Écrire ─────────────────────────────────────────────────────────
    Produit.objects.bulk_create(a_creer, batch_size=LOT)
    if a_modifier:
        Produit.objects.bulk_update(
            a_modifier,
            ["fournisseur", "categorie", "statut", "vu_le", *CHAMPS],
            batch_size=LOT)

    # `vu_le` sur les inchangés : une seule requête, sans réveiller
    # `updated_at`, pour que « ce produit est-il encore au catalogue ? » ait
    # une réponse même quand rien n'a changé.
    vus = [p.pk for cle, p in existants.items() if cle in lignes]
    for debut in range(0, len(vus), LOT):
        Produit.objects.filter(pk__in=vus[debut:debut + LOT]).update(vu_le=maintenant)

    # ── 4. Retirer les disparus ───────────────────────────────────────────
    disparus = [p.pk for cle, p in existants.items()
                if cle not in lignes and p.statut != Produit.Statut.RETIRE]
    retires = 0
    for debut in range(0, len(disparus), LOT):
        retires += Produit.objects.filter(
            pk__in=disparus[debut:debut + LOT]).update(statut=Produit.Statut.RETIRE)

    # ── 5. Rendre compte ──────────────────────────────────────────────────
    synchro.crees = len(a_creer)
    synchro.modifies = len(a_modifier)
    synchro.inchanges = inchanges
    synchro.retires = retires
    synchro.statut = SynchroDrive.Statut.TERMINEE
    synchro.terminee_le = timezone.now()
    synchro.rapport = {
        "lignes_lues": len(lignes) + len(rejets),
        "rejets": rejets[:50],
        "rejets_total": len(rejets),
        "doublons_exacts": collisions,
        "references_homonymes": homonymes,
        "fournisseurs": len(fournisseurs),
        "categories": len(categories),
    }
    synchro.save()

    logger.info("Catalogue synchronisé : %d créés, %d modifiés, %d inchangés, %d retirés",
                synchro.crees, synchro.modifies, synchro.inchanges, synchro.retires)
    return synchro
