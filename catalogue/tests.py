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
from unittest.mock import patch

from django.test import TestCase

from . import classeur, fiches, synchronisation
from .models import (Categorie, DocumentProduit, Fournisseur, Produit,
                     SynchroDrive)

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


class LectureFicheTests(TestCase):
    """L'extraction ne doit laisser entrer que ce qu'elle a compris."""

    def test_normalise_les_grandeurs_et_ecarte_les_cles_inconnues(self):
        brut = {
            "fournisseur": "Rivulis",
            "famille": "Micro-asperseur",
            "produits": [{
                "designation": "Fogger violet 5,3 l/h",
                "reference": "  101081222 ",
                "caracteristiques": {
                    "debit_l_h": "5.3 L/h",     # unité collée malgré la consigne
                    "couleur": "Violette",
                    "pression_nominale_bar": 4,
                    "prix": 12,                  # hors vocabulaire
                },
                "autres": {"Anti-vidange": "Super LPD", "Vide": ""},
            }],
        }
        res = fiches._normaliser(brut)
        produit = res["produits"][0]

        self.assertEqual(produit["reference"], "101081222")
        self.assertEqual(produit["caracteristiques"]["debit_l_h"], 5.3)
        self.assertEqual(produit["caracteristiques"]["couleur"], "Violette")
        self.assertNotIn("prix", produit["caracteristiques"])
        self.assertEqual(produit["autres"], {"Anti-vidange": "Super LPD"})

    def test_ecarte_un_produit_sans_designation(self):
        res = fiches._normaliser({"produits": [{"caracteristiques": {"debit_l_h": 4}}]})
        self.assertEqual(res["produits"], [])

    def test_signature_lit_couleur_et_debit_dans_un_nom_de_catalogue(self):
        self.assertEqual(fiches._signature_catalogue("Fogger - violet - 5,3 l/h"),
                         ("violet", 5.3))
        self.assertEqual(fiches._signature_catalogue("Fogger - noir - 28 l/h"),
                         ("noir", 28.0))
        # Sans débit, pas de signature : la couleur seule ne désigne rien.
        self.assertIsNone(fiches._signature_catalogue("Fogger - noir"))

    def test_signature_accorde_le_feminin_de_la_fiche_au_masculin_du_catalogue(self):
        ligne = {"caracteristiques": {"couleur": "Violette", "debit_l_h": 5.3}}
        self.assertEqual(fiches._signature_fiche(ligne), ("violet", 5.3))

    def test_themes_ecartent_couleurs_chiffres_et_mots_passe_partout(self):
        self.assertEqual(fiches._themes("Buse Fogger Violette 4.0 bar"), {"fogger"})


class RattachementTests(TestCase):
    """Un rapprochement douteux doit être refusé, pas deviné."""

    def setUp(self):
        self.rivulis = Fournisseur.objects.create(nom="RIVULIS", slug="rivulis")
        self.document = DocumentProduit.objects.create(
            drive_file_id="test-fogger", chemin_drive="DTI/RIVULIS",
            nom_fichier="fogger.pdf", fournisseur=self.rivulis)

    def _produit(self, reference, nom):
        return Produit.objects.create(reference=reference, nom=nom,
                                      fournisseur=self.rivulis)

    def _ligne(self, designation, **caracteristiques):
        return {"designation": designation, "reference": None,
                "caracteristiques": caracteristiques, "autres": {}}

    def test_rapproche_sur_la_reference_exacte(self):
        produit = self._produit("101081222", "Fogger - violet - 5,3 l/h")
        extraction = {"produits": [{
            "designation": "Peu importe", "reference": "101081222",
            "caracteristiques": {"debit_l_h": 5.3}, "autres": {}}]}

        compte_rendu = fiches.rattacher(self.document, extraction)

        produit.refresh_from_db()
        self.assertEqual(compte_rendu["rapproches"], 1)
        self.assertEqual(produit.caracteristiques["debit_l_h"], 5.3)
        self.assertEqual(produit.caracteristiques_source, self.document)

    def test_rapproche_sur_couleur_et_debit_quand_les_noms_different(self):
        """« Fogger - noir - 28 l/h » et « Buse Fogger Noire 4.0 bar ».

        Les deux écritures n'ont aucune chaîne commune ; couleur et débit sont
        le seul pont entre le catalogue et la fiche.
        """
        produit = self._produit("101081225", "Fogger - noir - 28 l/h")
        extraction = {"produits": [
            self._ligne("Buse Fogger Noire 4.0 bar", couleur="Noire", debit_l_h=28.0)]}

        compte_rendu = fiches.rattacher(self.document, extraction)

        produit.refresh_from_db()
        self.assertEqual(compte_rendu["rapproches"], 1)
        self.assertEqual(produit.caracteristiques["debit_l_h"], 28.0)

    def test_refuse_quand_deux_produits_portent_la_meme_signature(self):
        """Le catalogue contient trois « Fogger - bleu - 7 l/h ».

        Poser les caractéristiques sur l'un d'eux serait un tirage au sort.
        """
        self._produit("101029552", "Fogger - bleu - 7 l/h")
        self._produit("101029596", "Fogger - bleu - 7 l/h")
        extraction = {"produits": [
            self._ligne("Buse Fogger Bleue 4.0 bar", couleur="Bleue", debit_l_h=7.0)]}

        compte_rendu = fiches.rattacher(self.document, extraction)

        self.assertEqual(compte_rendu["rapproches"], 0)
        self.assertEqual(compte_rendu["orphelins"][0]["motif"], "ambigu")
        self.assertEqual(compte_rendu["orphelins"][0]["candidats"], 2)

    def test_la_signature_ne_traverse_pas_les_familles(self):
        """« noir 28 l/h » désigne dix articles Rivulis de gammes différentes.

        Sans le garde-fou de la famille, un goutteur héritait des
        caractéristiques d'un brumisateur.
        """
        goutteur = self._produit("999", "Goutteur - noir - 28 l/h")
        extraction = {"produits": [
            self._ligne("Buse Fogger Noire 4.0 bar", couleur="Noire", debit_l_h=28.0)]}

        compte_rendu = fiches.rattacher(self.document, extraction)

        goutteur.refresh_from_db()
        self.assertEqual(compte_rendu["rapproches"], 0)
        self.assertEqual(goutteur.caracteristiques, {})

    def test_un_produit_retire_n_est_plus_rapproche(self):
        self._produit("101081225", "Fogger - noir - 28 l/h")
        Produit.objects.update(statut=Produit.Statut.RETIRE)
        extraction = {"produits": [
            self._ligne("Buse Fogger Noire 4.0 bar", couleur="Noire", debit_l_h=28.0)]}

        self.assertEqual(fiches.rattacher(self.document, extraction)["rapproches"], 0)

    def test_une_ligne_sans_caracteristique_n_ecrase_rien(self):
        produit = self._produit("101081225", "Fogger - noir - 28 l/h")
        produit.caracteristiques = {"debit_l_h": 28.0}
        produit.save()
        extraction = {"produits": [{"designation": "Fogger - noir - 28 l/h",
                                    "reference": None, "caracteristiques": {},
                                    "autres": {}}]}

        compte_rendu = fiches.rattacher(self.document, extraction)

        produit.refresh_from_db()
        self.assertEqual(compte_rendu["rapproches"], 0)
        self.assertEqual(produit.caracteristiques, {"debit_l_h": 28.0})

    def test_analyser_sans_ia_configuree_archive_sans_inventer(self):
        """Repli propre : le document reste, les produits restent vierges."""
        produit = self._produit("101081225", "Fogger - noir - 28 l/h")
        with patch.object(fiches.llm, "is_configured", return_value=False):
            compte_rendu = fiches.analyser(self.document, b"%PDF-1.4")

        self.document.refresh_from_db()
        produit.refresh_from_db()
        self.assertEqual(compte_rendu["rapproches"], 0)
        self.assertTrue(self.document.erreur_extraction)
        self.assertIsNotNone(self.document.extrait_le)
        self.assertEqual(produit.caracteristiques, {})

    def test_analyser_enregistre_extraction_et_compte_rendu(self):
        produit = self._produit("101081225", "Fogger - noir - 28 l/h")
        sortie = {"fournisseur": "Rivulis", "famille": "Micro-asperseur",
                  "produits": [{"designation": "Buse Fogger Noire 4.0 bar",
                                "reference": None,
                                "caracteristiques": {"couleur": "Noire",
                                                     "debit_l_h": 28.0},
                                "autres": {}}]}
        with patch.object(fiches.llm, "is_configured", return_value=True), \
             patch.object(fiches.llm, "extract_json_from_document", return_value=sortie):
            compte_rendu = fiches.analyser(self.document, b"%PDF-1.4")

        self.document.refresh_from_db()
        produit.refresh_from_db()
        self.assertEqual(compte_rendu["rapproches"], 1)
        self.assertEqual(produit.caracteristiques["debit_l_h"], 28.0)
        self.assertEqual(self.document.extraction["famille"], "Micro-asperseur")
        self.assertEqual(self.document.extraction["compte_rendu"]["rapproches"], 1)
        self.assertEqual(self.document.erreur_extraction, "")

    def test_une_fiche_illisible_n_interrompt_pas_la_passe(self):
        with patch.object(fiches.llm, "is_configured", return_value=True), \
             patch.object(fiches.llm, "extract_json_from_document",
                          side_effect=RuntimeError("quota dépassé")):
            compte_rendu = fiches.analyser(self.document, b"%PDF-1.4")

        self.document.refresh_from_db()
        self.assertEqual(compte_rendu["rapproches"], 0)
        self.assertTrue(self.document.erreur_extraction)
