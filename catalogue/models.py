"""Référentiel produit : le matériel d'irrigation que le marché propose.

Le catalogue vit sur un Drive tenu par Cultiveau — c'est lui la source de
vérité, pas cette base. Isidor en tient un reflet interrogeable : un classeur
de 32 000 références, et les fiches techniques PDF qui les documentent.

Trois partis pris structurent ces modèles.

**Le référentiel est global.** Un goutteur Rivulis 4 L/h est le même objet pour
toutes les exploitations : aucune FK vers `Exploitation` ici, exactement comme
`operations.CatalogueEngin` tient les modèles d'engins du marché face aux
`Machine` que possède une ferme. Ce qu'une exploitation possède se dit
ailleurs — dans `dti.Composant`, qui pointe vers un `Produit` d'ici.

**Rien n'est jamais supprimé.** Le Drive est vivant : un produit peut
disparaître du classeur à la synchronisation suivante. Le supprimer casserait
les DTI qui le référencent et effacerait l'histoire d'un réseau installé. Un
produit absent de l'export devient donc `retire` — il ne remonte plus dans les
recherches, mais les diagnostics qui le citent restent lisibles.

**Le classeur ne dit pas tout.** Sa colonne « Description » est vide sur la
totalité des 32 410 lignes : le référentiel connaît les prix et les photos,
jamais les débits, les pressions ni les portées. Cette moitié manquante vit
dans les PDF des fiches techniques, et atterrit dans
`Produit.caracteristiques` — d'où la séparation entre ce qui vient du classeur
(colonnes, requêtables, fiables) et ce qui vient des fiches (JSON, extrait,
daté de sa source).
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import TimeStampedModel


class Fournisseur(TimeStampedModel):
    """Une marque du catalogue.

    Le classeur ne donne qu'un nom ; le Drive, lui, range les fiches
    techniques par fournisseur. `dossier_drive` est ce qui relie les deux :
    sans lui, rattacher un PDF à ses produits demanderait de deviner.
    """

    nom = models.CharField(_("nom"), max_length=120, unique=True)
    slug = models.SlugField(_("slug"), max_length=140, unique=True)
    dossier_drive = models.CharField(
        _("dossier Drive"), max_length=100, blank=True,
        help_text=_("Identifiant du dossier qui porte ses fiches techniques."))
    site_web = models.URLField(_("site web"), blank=True)

    class Meta:
        ordering = ("nom",)
        verbose_name = _("fournisseur")
        verbose_name_plural = _("fournisseurs")

    def __str__(self):
        return self.nom


class Categorie(TimeStampedModel):
    """Famille de produits, telle que nommée par le classeur.

    Les 44 catégories vont de « Disjoncteurs & protection » à « Micro-asperseurs » :
    c'est la nomenclature de Cultiveau, reprise sans la retravailler. La
    renommer ici la désynchroniserait du prochain export.
    """

    nom = models.CharField(_("nom"), max_length=140, unique=True)
    slug = models.SlugField(_("slug"), max_length=160, unique=True)

    class Meta:
        ordering = ("nom",)
        verbose_name = _("catégorie")
        verbose_name_plural = _("catégories")

    def __str__(self):
        return self.nom


class Produit(TimeStampedModel):
    """Une référence du catalogue.

    La clé est `(reference, nom)` et non la seule référence, bien que le guide
    du classeur annonce celle-ci comme unique. Sept références la contredisent,
    dont quatre portent de vrais produits distincts — un Aqua4D H-A et un H-B
    partagent `8299112` pour des prix du simple au double, deux tés GAER de
    diamètres différents partagent `481052185`. Dédupliquer sur la seule
    référence en perdrait un des deux, silencieusement et au hasard de l'ordre
    des lignes. Le couple les distingue, et `SynchroDrive.rapport` remonte les
    collisions pour qu'elles soient corrigées à la source plutôt qu'absorbées
    ici.
    """

    class Statut(models.TextChoices):
        ACTIF = "actif", _("Actif")
        INACTIF = "inactif", _("Inactif")
        RETIRE = "retire", _("Retiré du catalogue")

    # ── Identité ──
    reference = models.CharField(_("référence"), max_length=80, db_index=True)
    nom = models.CharField(_("nom"), max_length=300)
    fournisseur = models.ForeignKey(
        Fournisseur, on_delete=models.PROTECT, related_name="produits",
        verbose_name=_("fournisseur"))
    categorie = models.ForeignKey(
        Categorie, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="produits", verbose_name=_("catégorie"))
    description = models.TextField(_("description"), blank=True)

    # ── Commercial ──
    prix_ht = models.DecimalField(_("prix HT (€)"), max_digits=12, decimal_places=2,
                                  null=True, blank=True)
    unite = models.CharField(_("unité"), max_length=40, blank=True)
    conditionnement = models.CharField(_("conditionnement"), max_length=120, blank=True)
    poids_kg = models.DecimalField(_("poids (kg)"), max_digits=10, decimal_places=3,
                                   null=True, blank=True)
    image_url = models.URLField(_("URL de l'image"), max_length=500, blank=True)

    # ── Logistique plateforme ──
    #: Colonnes ajoutées par l'export au-delà de ce que documente le guide du
    #: classeur. Conservées telles quelles : elles pilotent le prix plateforme.
    stocke_plateforme = models.BooleanField(_("stocké en plateforme"), default=False)
    stock_plateforme = models.IntegerField(_("stock plateforme"), default=0)
    cout_stockage = models.DecimalField(_("coût de stockage (€/u)"), max_digits=10,
                                        decimal_places=2, null=True, blank=True)
    cout_manutention = models.DecimalField(_("coût de manutention (€/u)"), max_digits=10,
                                           decimal_places=2, null=True, blank=True)
    marge_logistique = models.DecimalField(_("marge logistique (%)"), max_digits=6,
                                           decimal_places=2, null=True, blank=True)
    prix_plateforme = models.DecimalField(_("prix plateforme (€)"), max_digits=12,
                                          decimal_places=2, null=True, blank=True)

    # ── Stock de référence ──
    stock_initial = models.IntegerField(_("stock initial"), default=0)
    seuil_alerte = models.IntegerField(_("seuil d'alerte"), default=5)

    # ── Caractéristiques techniques ──
    caracteristiques = models.JSONField(
        _("caractéristiques"), default=dict, blank=True,
        help_text=_("Débits, pressions, DN… extraits des fiches techniques. "
                    "Absents du classeur, qui ne porte aucune description."))
    caracteristiques_source = models.ForeignKey(
        "catalogue.DocumentProduit", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="produits_documentes", verbose_name=_("fiche d'origine"),
        help_text=_("Le document dont les caractéristiques ont été tirées."))

    # ── Suivi ──
    statut = models.CharField(_("statut"), max_length=10, choices=Statut.choices,
                              default=Statut.ACTIF, db_index=True)
    vu_le = models.DateTimeField(
        _("vu pour la dernière fois"), null=True, blank=True,
        help_text=_("Dernière synchronisation où le classeur portait encore "
                    "cette référence."))

    class Meta:
        ordering = ("fournisseur__nom", "reference")
        verbose_name = _("produit")
        verbose_name_plural = _("produits")
        constraints = [
            models.UniqueConstraint(fields=["reference", "nom"],
                                    name="produit_reference_nom_unique"),
        ]
        indexes = [
            models.Index(fields=["fournisseur", "categorie"]),
            models.Index(fields=["statut", "reference"]),
        ]

    def __str__(self):
        return f"{self.reference} · {self.nom}"

    @property
    def documente(self):
        """Vrai si une fiche technique a nourri ses caractéristiques."""
        return bool(self.caracteristiques)


class DocumentProduit(TimeStampedModel):
    """Une fiche technique ou notice du Drive.

    Un document parle souvent de plusieurs produits — une notice Aqua4D couvre
    les modèles 30, 40, 50 et 60M — d'où une relation multiple plutôt qu'une
    FK. Le rattachement est fait par extraction, et `produits` peut rester vide
    tant que personne n'a su relier le PDF à des références.

    `md5_drive` est ce qui évite de retélécharger et de réanalyser à chaque
    passage : tant que l'empreinte du Drive n'a pas bougé, le document est
    inchangé.
    """

    class Nature(models.TextChoices):
        FICHE = "fiche", _("Fiche technique")
        NOTICE = "notice", _("Notice d'installation")
        CHIFFRAGE = "chiffrage", _("Chiffrage")
        AUTRE = "autre", _("Autre")

    # ── Provenance Drive ──
    drive_file_id = models.CharField(_("identifiant Drive"), max_length=100, unique=True)
    chemin_drive = models.CharField(
        _("chemin dans le Drive"), max_length=600,
        help_text=_("Arborescence lisible, p. ex. « AQUA 4D/FICHES TECHNIQUES/… »."))
    nom_fichier = models.CharField(_("nom du fichier"), max_length=300)
    md5_drive = models.CharField(_("empreinte Drive"), max_length=32, blank=True, db_index=True)
    octets = models.PositiveIntegerField(_("taille"), null=True, blank=True)
    modifie_le = models.DateTimeField(_("modifié le"), null=True, blank=True)

    # ── Contenu ──
    nature = models.CharField(_("nature"), max_length=12, choices=Nature.choices,
                              default=Nature.AUTRE)
    fournisseur = models.ForeignKey(
        Fournisseur, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="documents", verbose_name=_("fournisseur"))
    fichier = models.FileField(_("fichier"), upload_to="catalogue/fiches/%Y/%m/", blank=True)
    produits = models.ManyToManyField(
        Produit, blank=True, related_name="documents", verbose_name=_("produits"))

    # ── Extraction ──
    extrait_le = models.DateTimeField(_("analysé le"), null=True, blank=True)
    extraction = models.JSONField(
        _("extraction"), default=dict, blank=True,
        help_text=_("Ce que la lecture du PDF a produit, avant répartition "
                    "sur les produits."))
    erreur_extraction = models.TextField(_("erreur d'extraction"), blank=True)

    class Meta:
        ordering = ("chemin_drive", "nom_fichier")
        verbose_name = _("document produit")
        verbose_name_plural = _("documents produits")

    def __str__(self):
        return self.nom_fichier

    @property
    def a_analyser(self):
        """Vrai si le document n'a jamais été lu, ou a changé depuis."""
        return self.extrait_le is None or (
            self.modifie_le is not None and self.modifie_le > self.extrait_le)


class SynchroDrive(TimeStampedModel):
    """Une passe de synchronisation, datée et rendue compte.

    Le Drive étant la source de vérité, la question n'est jamais « qu'a-t-on
    importé ? » mais « qu'est-ce qui a changé depuis la dernière fois ? ». Sans
    cette trace, une synchronisation qui n'aurait silencieusement rien fait —
    classeur illisible, dossier déplacé, jeton expiré — passerait pour un
    catalogue stable.
    """

    class Statut(models.TextChoices):
        EN_COURS = "en_cours", _("En cours")
        TERMINEE = "terminee", _("Terminée")
        ERREUR = "erreur", _("Erreur")

    demarree_le = models.DateTimeField(_("démarrée le"), auto_now_add=True)
    terminee_le = models.DateTimeField(_("terminée le"), null=True, blank=True)
    statut = models.CharField(_("statut"), max_length=10, choices=Statut.choices,
                              default=Statut.EN_COURS)

    #: Empreinte du classeur traité. Identique à la précédente : le classeur
    #: n'a pas bougé, seuls les documents ont pu changer.
    empreinte_classeur = models.CharField(_("empreinte du classeur"), max_length=64, blank=True)

    crees = models.PositiveIntegerField(_("produits créés"), default=0)
    modifies = models.PositiveIntegerField(_("produits modifiés"), default=0)
    inchanges = models.PositiveIntegerField(_("produits inchangés"), default=0)
    retires = models.PositiveIntegerField(_("produits retirés"), default=0)
    documents_vus = models.PositiveIntegerField(_("documents vus"), default=0)

    rapport = models.JSONField(
        _("rapport"), default=dict, blank=True,
        help_text=_("Collisions de références, lignes rejetées, catégories "
                    "créées — ce qui mérite l'œil d'un humain."))
    erreur = models.TextField(_("erreur"), blank=True)

    class Meta:
        ordering = ("-demarree_le",)
        verbose_name = _("synchronisation du Drive")
        verbose_name_plural = _("synchronisations du Drive")

    def __str__(self):
        return f"Synchro {self.demarree_le:%d/%m/%Y %H:%M} · {self.get_statut_display()}"
