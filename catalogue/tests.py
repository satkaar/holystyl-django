"""Contrat de synchronisation avec le Drive catalogue.

Ces tests portent sur les propriétés dont dépend une source de vérité
extérieure : qu'une passe rejouée ne modifie rien, qu'une disparition ne
détruise rien, et qu'un export ayant changé de forme soit refusé au lieu
d'être relu de travers.

Le classeur de référence est fabriqué à la volée par `construire_classeur` :
un `.xlsx` minimal mais authentique — même archive ZIP, mêmes espaces de
noms — pour éprouver le lecteur sur ce qu'il rencontrera vraiment, sans
embarquer les 25 Mo de l'export réel dans le dépôt.
"""

import zipfile
from decimal import Decimal

from django.test import TestCase

from . import classeur, synchronisation
from .models import Categorie, Fournisseur, Produit, SynchroDrive

ENTETE = ["Référence", "Nom du produit", "Fournisseur", "Catégorie",
          "Description", "Prix HT (€)", "Unité", "Conditionnement", "Poids (kg)",
          "URL Image", "Stock initial", "Seuil alerte stock", "Actif",
          "Stocké plateforme", "Stock plateforme", "Coût stockage (€/u)",
          "Coût manutention (€/u)", "Marge logistique (%)", "Prix plateforme (€)"]

LIGNE_TYPE = ["63000-003000", "Antigoutteur Profit 4L/h", "HB SYSTEM",
              "Goutte à Goutte", "", "0.25", "pièce", "Boîte de 100", "0.015",
              "https://example.org/p.jpg", "100", "5", "OUI",
              "NON", "0", "0", "0", "0", ""]


def _colonne(index):
    """0 → A, 25 → Z, 26 → AA."""
    nom = ""
    while True:
        nom = chr(ord("A") + index % 26) + nom
        index = index // 26 - 1
        if index < 0:
            return nom


def construire_classeur(chemin, lignes, nom_feuille="Catalogue"):
    """Écrit un .xlsx minimal contenant l'entête puis `lignes`."""
    def cellules(numero, valeurs):
        cs = []
        for i, valeur in enumerate(valeurs):
            texte = str(valeur).replace("&", "&amp;").replace("<", "&lt;")
            cs.append(f'<c r="{_colonne(i)}{numero}" t="inlineStr">'
                      f"<is><t>{texte}</t></is></c>")
        return f'<row r="{numero}">' + "".join(cs) + "</row>"

    corps = [cellules(1, ENTETE)]
    corps += [cellules(i, l) for i, l in enumerate(lignes, start=2)]
    feuille = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>" + "".join(corps) + "</sheetData></worksheet>")
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{nom_feuille}" sheetId="1" r:id="rId1"/></sheets></workbook>')
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="worksheets/sheet1.xml" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
        "</Relationships>")
    with zipfile.ZipFile(chemin, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/worksheets/sheet1.xml", feuille)
    return chemin


def ligne(**remplacements):
    """Une ligne type, dont on ne change que ce qui compte pour le test."""
    valeurs = list(LIGNE_TYPE)
    rangs = {champ: i for i, champ in enumerate(classeur.COLONNES.values())}
    for champ, valeur in remplacements.items():
        valeurs[rangs[champ]] = valeur
    return valeurs


class LectureClasseurTests(TestCase):
    """Le lecteur doit rendre des types exploitables, pas des chaînes."""

    def test_lit_une_ligne_et_convertit_les_types(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as f:
            construire_classeur(f.name, [LIGNE_TYPE])
            produits = list(classeur.lire(f.name))

        self.assertEqual(len(produits), 1)
        p = produits[0]
        self.assertEqual(p["reference"], "63000-003000")
        self.assertEqual(p["prix_ht"], Decimal("0.25"))
        self.assertEqual(p["poids_kg"], Decimal("0.015"))
        self.assertEqual(p["stock_initial"], 100)
        self.assertIs(p["actif"], True)
        self.assertIs(p["stocke_plateforme"], False)
        self.assertIsNone(p["prix_plateforme"])

    def test_refuse_un_classeur_dont_les_colonnes_ont_change(self):
        """Une colonne insérée décalerait tout sans lever d'erreur.

        C'est le scénario silencieux qu'on veut rendre bruyant : les prix
        atterriraient dans les unités et personne ne le verrait avant qu'un
        devis parte faux.
        """
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as f:
            decalee = ["Code produit"] + ENTETE
            construire_classeur(f.name, [])
            self._reecrire_entete(f.name, decalee)

            with self.assertRaises(classeur.ClasseurInvalide):
                list(classeur.lire(f.name))

    @staticmethod
    def _reecrire_entete(chemin, entete):
        """Remplace la première ligne de la feuille, archive intacte par ailleurs."""
        with zipfile.ZipFile(chemin) as archive:
            contenu = {n: archive.read(n) for n in archive.namelist()}
        cellules = "".join(
            f'<c r="{_colonne(i)}1" t="inlineStr"><is><t>{v}</t></is></c>'
            for i, v in enumerate(entete))
        contenu["xl/worksheets/sheet1.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData><row r="1">{cellules}</row></sheetData></worksheet>'
        ).encode()
        with zipfile.ZipFile(chemin, "w") as archive:
            for nom, octets in contenu.items():
                archive.writestr(nom, octets)

    def test_trouve_la_feuille_catalogue_par_son_nom(self):
        """L'ordre des feuilles n'est pas un contrat ; leur nom l'est."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as f:
            construire_classeur(f.name, [LIGNE_TYPE], nom_feuille="Guide")
            with self.assertRaises(classeur.ClasseurInvalide):
                list(classeur.lire(f.name))


class SynchronisationTests(TestCase):
    """Le référentiel suit le Drive, sans jamais rien perdre en route."""

    def setUp(self):
        import tempfile
        self.dossier = tempfile.mkdtemp()

    def _classeur(self, lignes, nom="c.xlsx"):
        from pathlib import Path
        chemin = Path(self.dossier) / nom
        return construire_classeur(chemin, lignes)

    def test_cree_produits_fournisseurs_et_categories(self):
        chemin = self._classeur([
            LIGNE_TYPE,
            ligne(reference="A1", nom="Vanne 40", fournisseur="ELYDAN",
                  categorie="Vannes fonte", prix_ht="12.50"),
        ])
        synchro = synchronisation.synchroniser(chemin)

        self.assertEqual(synchro.crees, 2)
        self.assertEqual(Produit.objects.count(), 2)
        self.assertEqual(Fournisseur.objects.count(), 2)
        self.assertEqual(Categorie.objects.count(), 2)
        self.assertEqual(synchro.statut, SynchroDrive.Statut.TERMINEE)

    def test_rejouee_sur_un_classeur_inchange_ne_modifie_rien(self):
        """La propriété qui permet de la programmer sans surveillance."""
        chemin = self._classeur([LIGNE_TYPE])
        synchronisation.synchroniser(chemin)
        avant = Produit.objects.get().updated_at

        seconde = synchronisation.synchroniser(chemin)

        self.assertEqual(seconde.crees, 0)
        self.assertEqual(seconde.modifies, 0)
        self.assertEqual(seconde.inchanges, 1)
        self.assertEqual(Produit.objects.get().updated_at, avant)

    def test_met_a_jour_un_prix_qui_a_bouge(self):
        chemin = self._classeur([LIGNE_TYPE])
        synchronisation.synchroniser(chemin)

        chemin = self._classeur([ligne(prix_ht="0.30")], nom="c2.xlsx")
        seconde = synchronisation.synchroniser(chemin)

        self.assertEqual(seconde.modifies, 1)
        self.assertEqual(Produit.objects.get().prix_ht, Decimal("0.30"))

    def test_un_produit_disparu_est_retire_et_non_supprime(self):
        """Les DTI qui le citent doivent rester lisibles."""
        chemin = self._classeur([
            LIGNE_TYPE,
            ligne(reference="A1", nom="Vanne 40"),
        ])
        synchronisation.synchroniser(chemin)

        chemin = self._classeur([LIGNE_TYPE], nom="c2.xlsx")
        seconde = synchronisation.synchroniser(chemin)

        self.assertEqual(seconde.retires, 1)
        self.assertEqual(Produit.objects.count(), 2)
        disparu = Produit.objects.get(reference="A1")
        self.assertEqual(disparu.statut, Produit.Statut.RETIRE)

    def test_un_produit_revenu_redevient_actif(self):
        chemin = self._classeur([LIGNE_TYPE])
        synchronisation.synchroniser(chemin)
        Produit.objects.update(statut=Produit.Statut.RETIRE)

        synchronisation.synchroniser(chemin)

        self.assertEqual(Produit.objects.get().statut, Produit.Statut.ACTIF)

    def test_actif_non_donne_un_produit_inactif(self):
        chemin = self._classeur([ligne(actif="NON")])
        synchronisation.synchroniser(chemin)

        self.assertEqual(Produit.objects.get().statut, Produit.Statut.INACTIF)

    def test_deux_produits_sous_une_meme_reference_coexistent(self):
        """Le classeur annonce la référence unique ; elle ne l'est pas.

        Sept références de l'export réel portent plusieurs produits, dont un
        Aqua4D H-A et un H-B au prix du simple au double. Dédupliquer sur la
        seule référence en perdrait un, au hasard de l'ordre des lignes.
        """
        chemin = self._classeur([
            ligne(reference="8299112", nom="TU H-A 60", prix_ht="1210.50"),
            ligne(reference="8299112", nom="TU H-B 60", prix_ht="2294"),
        ])
        synchro = synchronisation.synchroniser(chemin)

        self.assertEqual(Produit.objects.filter(reference="8299112").count(), 2)
        self.assertIn("8299112", synchro.rapport["references_homonymes"])

    def test_deux_lignes_identiques_ne_font_qu_un_produit_et_sont_signalees(self):
        chemin = self._classeur([LIGNE_TYPE, LIGNE_TYPE])
        synchro = synchronisation.synchroniser(chemin)

        self.assertEqual(Produit.objects.count(), 1)
        self.assertIn("63000-003000", synchro.rapport["doublons_exacts"])

    def test_une_ligne_sans_reference_est_rejetee_et_comptee(self):
        chemin = self._classeur([LIGNE_TYPE, ligne(reference="", nom="Orphelin")])
        synchro = synchronisation.synchroniser(chemin)

        self.assertEqual(Produit.objects.count(), 1)
        self.assertEqual(synchro.rapport["rejets_total"], 1)

    def test_la_synchro_rend_compte_meme_quand_rien_ne_bouge(self):
        """Une passe qui n'a rien fait doit se distinguer d'une passe absente."""
        chemin = self._classeur([LIGNE_TYPE])
        synchronisation.synchroniser(chemin)
        synchronisation.synchroniser(chemin)

        self.assertEqual(SynchroDrive.objects.count(), 2)
        derniere = SynchroDrive.objects.first()
        self.assertEqual(derniere.rapport["lignes_lues"], 1)
