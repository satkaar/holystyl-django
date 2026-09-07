"""Met le référentiel en conformité avec le classeur du Drive.

Tant que la synchronisation automatique du Drive n'est pas branchée, la
commande accepte un chemin local : c'est ce qui permet de rejouer un export
téléchargé à la main sans attendre les identifiants de service.
"""

import hashlib
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from catalogue import synchronisation
from catalogue.classeur import ClasseurInvalide


def _empreinte(chemin):
    """SHA-256 du classeur, lu par blocs — le fichier dépasse les 25 Mo."""
    digest = hashlib.sha256()
    with open(chemin, "rb") as flux:
        for bloc in iter(lambda: flux.read(1024 * 1024), b""):
            digest.update(bloc)
    return digest.hexdigest()


class Command(BaseCommand):
    help = "Synchronise le référentiel produit depuis le classeur catalogue."

    def add_arguments(self, parseur):
        parseur.add_argument("classeur", type=str,
                             help="Chemin du fichier .xlsx exporté du Drive.")

    def handle(self, *args, **options):
        chemin = Path(options["classeur"])
        if not chemin.exists():
            raise CommandError(f"Classeur introuvable : {chemin}")

        self.stdout.write(f"Lecture de {chemin.name}…")
        try:
            synchro = synchronisation.synchroniser(chemin, empreinte=_empreinte(chemin))
        except ClasseurInvalide as erreur:
            raise CommandError(str(erreur))

        self.stdout.write(self.style.SUCCESS(
            f"  créés     {synchro.crees:>7,}\n"
            f"  modifiés  {synchro.modifies:>7,}\n"
            f"  inchangés {synchro.inchanges:>7,}\n"
            f"  retirés   {synchro.retires:>7,}"))

        homonymes = synchro.rapport.get("references_homonymes") or {}
        if homonymes:
            self.stdout.write(self.style.WARNING(
                f"\n{len(homonymes)} référence(s) portent plusieurs produits :"))
            for reference, noms in list(homonymes.items())[:20]:
                self.stdout.write(f"  {reference} → {' | '.join(noms)}")

        rejets = synchro.rapport.get("rejets_total") or 0
        if rejets:
            self.stdout.write(self.style.WARNING(f"\n{rejets} ligne(s) rejetée(s)."))
