"""Lit les fiches techniques et pose leurs caractéristiques sur les produits.

Tant que la synchronisation du Drive n'est pas branchée, la commande travaille
sur un dossier local : c'est ce qui permet d'éprouver l'extraction sur de
vraies fiches sans attendre les identifiants de service.

Une fiche déjà analysée et inchangée n'est pas relue — chaque lecture est un
appel facturé au modèle, et rejouer la commande ne doit pas coûter le prix
d'une première passe.
"""

import hashlib
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from catalogue import fiches
from catalogue.models import DocumentProduit, Fournisseur


class Command(BaseCommand):
    help = "Analyse les fiches techniques d'un dossier et enrichit le référentiel."

    def add_arguments(self, parseur):
        parseur.add_argument("dossier", type=str,
                             help="Dossier contenant les fiches (PDF ou images).")
        parseur.add_argument("--fournisseur", type=str, default="",
                             help="Force le fournisseur pour toutes les fiches. "
                                  "Par défaut, il est déduit du dossier parent, "
                                  "comme sur le Drive.")
        parseur.add_argument("--relire", action="store_true",
                             help="Relit même les fiches déjà analysées.")

    def handle(self, *args, **options):
        dossier = Path(options["dossier"])
        if not dossier.is_dir():
            raise CommandError(f"Dossier introuvable : {dossier}")

        impose = None
        if options["fournisseur"]:
            impose = Fournisseur.objects.filter(
                nom__iexact=options["fournisseur"]).first()
            if impose is None:
                raise CommandError(
                    f"Fournisseur inconnu : {options['fournisseur']}")

        # Sur le Drive, une fiche vit sous le dossier de sa marque
        # (« BRUMISATEURS ET NEBULISEURS/RIVULIS »). Déduire le fournisseur du
        # chemin évite d'attribuer à Rivulis un catalogue Nelson — ce qui
        # restreindrait le rapprochement au mauvais périmètre.
        connus = {f.nom.upper(): f for f in Fournisseur.objects.all()}

        chemins = sorted(c for c in dossier.iterdir()
                         if c.is_file() and fiches.mime_de(c.name))
        if not chemins:
            self.stdout.write(self.style.WARNING("Aucune fiche lisible dans ce dossier."))
            return

        total_rapproches = total_orphelins = 0
        for chemin in chemins:
            octets = chemin.read_bytes()
            empreinte = hashlib.md5(octets).hexdigest()
            fournisseur = impose or connus.get(chemin.parent.name.upper())

            document, cree = DocumentProduit.objects.get_or_create(
                # En l'absence du Drive, le fichier est identifié par son
                # empreinte. La synchronisation le remplacera par le vrai
                # identifiant Drive sans rien perdre.
                drive_file_id=f"local:{empreinte}",
                defaults={
                    "chemin_drive": str(chemin.parent),
                    "nom_fichier": chemin.name,
                    "md5_drive": empreinte,
                    "octets": len(octets),
                    "fournisseur": fournisseur,
                })

            if not cree and not options["relire"] and document.extrait_le:
                self.stdout.write(f"  {chemin.name} — déjà analysée, ignorée")
                continue

            if fournisseur and document.fournisseur_id != fournisseur.pk:
                document.fournisseur = fournisseur
                document.save(update_fields=["fournisseur", "updated_at"])

            self.stdout.write(f"  {chemin.name} — lecture…")
            compte_rendu = fiches.analyser(document, octets)

            if document.erreur_extraction:
                self.stdout.write(self.style.ERROR(
                    f"    {document.erreur_extraction}"))
                continue

            total_rapproches += compte_rendu["rapproches"]
            total_orphelins += len(compte_rendu["orphelins"])
            self.stdout.write(
                f"    {compte_rendu['rapproches']} produit(s) enrichi(s), "
                f"{len(compte_rendu['orphelins'])} non rapproché(s)")

        self.stdout.write(self.style.SUCCESS(
            f"\n{total_rapproches} produit(s) enrichi(s) au total."))
        if total_orphelins:
            self.stdout.write(self.style.WARNING(
                f"{total_orphelins} ligne(s) de fiche sans produit au catalogue. "
                "Le détail est dans DocumentProduit.extraction."))
