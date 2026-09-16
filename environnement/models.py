"""Taxonomie EU — fiches d'activité et évaluation d'alignement (règlement UE 2020/852)."""

from django.db import models
from django.utils.translation import gettext_lazy as _


class ActiviteTaxonomie(models.Model):
    """Une fiche = une activité économique évaluée au regard de la Taxonomie EU."""

    class Objectif(models.TextChoices):
        ATTENUATION = "attenuation", _("Atténuation du changement climatique")
        ADAPTATION = "adaptation", _("Adaptation au changement climatique")
        EAU = "eau", _("Eau et ressources marines")
        CIRCULAIRE = "circulaire", _("Économie circulaire")
        POLLUTION = "pollution", _("Prévention de la pollution")
        BIODIVERSITE = "biodiversite", _("Biodiversité et écosystèmes")

    exploitation = models.ForeignKey(
        "exploitations.Exploitation", on_delete=models.CASCADE, related_name="activites_taxonomie"
    )
    campagne = models.PositiveIntegerField(_("campagne"))
    libelle = models.CharField(_("activité"), max_length=255)
    code_nace = models.CharField(_("code NACE"), max_length=20, blank=True)
    objectif = models.CharField(
        _("objectif de contribution"), max_length=15, choices=Objectif.choices, default=Objectif.ATTENUATION
    )
    eligible = models.BooleanField(_("éligible"), default=False)
    contribution = models.BooleanField(_("contribution substantielle"), default=False)
    # DNSH : {"adaptation": true, "eau": false, …} — un statut par objectif
    dnsh = models.JSONField(_("DNSH par objectif"), default=dict, blank=True)
    garanties = models.BooleanField(_("garanties minimales"), default=False)
    chiffre_affaires = models.FloatField(_("chiffre d'affaires (€)"), null=True, blank=True)
    capex = models.FloatField(_("CapEx (€)"), null=True, blank=True)
    opex = models.FloatField(_("OpEx (€)"), null=True, blank=True)
    justification = models.TextField(_("justification"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("activité Taxonomie")
        verbose_name_plural = _("activités Taxonomie")
        ordering = ("-campagne", "libelle")
        indexes = [models.Index(fields=["exploitation", "campagne"])]

    def __str__(self):
        return f"{self.libelle} ({self.campagne})"

    @property
    def autres_objectifs(self):
        """Les 5 objectifs autres que celui de contribution (cibles du DNSH)."""
        return [o for o in self.Objectif.values if o != self.objectif]

    @property
    def dnsh_ok(self):
        return all(self.dnsh.get(o, False) for o in self.autres_objectifs)

    @property
    def aligne(self):
        """Activité alignée = éligible + contribution + DNSH (5 autres) + garanties."""
        return bool(self.eligible and self.contribution and self.garanties and self.dnsh_ok)

    @property
    def statut(self):
        if self.aligne:
            return "aligne"
        if self.eligible:
            return "eligible"
        return "non_eligible"


class Biodiversite(models.Model):
    """Fiche de suivi de la biodiversité d'une parcelle (IAE, espèces, couverts)."""

    exploitation = models.ForeignKey(
        "exploitations.Exploitation", on_delete=models.CASCADE, related_name="fiches_biodiversite"
    )
    parcelle = models.ForeignKey(
        "parcelles.Parcelle", on_delete=models.CASCADE, related_name="fiches_biodiversite"
    )
    date = models.DateField(_("date"))
    score = models.IntegerField(_("score biodiversité (0-100)"), null=True, blank=True)
    especes_vegetales = models.IntegerField(_("espèces végétales"), null=True, blank=True)
    especes_animales = models.IntegerField(_("espèces animales"), null=True, blank=True)
    haies_ml = models.FloatField(_("haies (ml)"), null=True, blank=True)
    jachere_ha = models.FloatField(_("jachère (ha)"), null=True, blank=True)
    observations = models.TextField(_("observations"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("fiche biodiversité")
        verbose_name_plural = _("fiches biodiversité")
        ordering = ("-date",)

    def __str__(self):
        return f"{self.parcelle} — {self.date}"


class ApportAzote(models.Model):
    """Un apport d'azote sur une parcelle : ce que le cahier d'épandage doit dire.

    L'azote se compte en deux temps : une dose de produit à l'hectare et la
    teneur en azote de ce produit. Le résultat (`n_kg_ha`) est figé à
    l'enregistrement, car la composition d'un engrais change d'un lot à
    l'autre : recalculer plus tard réécrirait un apport déjà réalisé.

    La nature distingue l'azote organique issu des effluents d'élevage : c'est
    lui, et lui seul, que la directive Nitrates plafonne à 170 kg/ha/an en
    zone vulnérable.
    """

    class Nature(models.TextChoices):
        MINERAL = "mineral", _("Engrais minéral")
        ORGANIQUE_ELEVAGE = "organique_elevage", _("Effluent d'élevage")
        ORGANIQUE_AUTRE = "organique_autre", _("Autre apport organique")

    exploitation = models.ForeignKey(
        "exploitations.Exploitation", on_delete=models.CASCADE, related_name="apports_azote"
    )
    parcelle = models.ForeignKey(
        "parcelles.Parcelle", on_delete=models.CASCADE, related_name="apports_azote"
    )
    date = models.DateField(_("date de l'apport"))
    #: Campagne culturale (« 2025/2026 »), déduite de la date à l'enregistrement.
    campagne = models.CharField(_("campagne"), max_length=20, blank=True)
    nature = models.CharField(_("nature"), max_length=20, choices=Nature.choices, default=Nature.MINERAL)
    produit = models.CharField(_("produit"), max_length=255, blank=True)
    dose_kg_ha = models.FloatField(_("dose (kg/ha de produit)"), default=0)
    teneur_n_pct = models.FloatField(_("teneur en azote (%)"), default=0)
    #: Azote apporté, en kg/ha — figé au moment de l'enregistrement.
    n_kg_ha = models.FloatField(_("azote apporté (kg N/ha)"), default=0)
    #: Surface effectivement couverte : un apport ne couvre pas toujours toute
    #: la parcelle, et c'est elle qui fait le total.
    surface_ha = models.FloatField(_("surface concernée (ha)"), null=True, blank=True)
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("apport d'azote")
        verbose_name_plural = _("apports d'azote")
        ordering = ("-date", "-created_at")
        indexes = [models.Index(fields=["exploitation", "campagne"])]

    def __str__(self):
        return f"{self.parcelle} — {self.date} — {self.n_kg_ha} kg N/ha"

    def save(self, *args, **kwargs):
        from parcelles.models import ParcelleCampagne

        self.n_kg_ha = round((self.dose_kg_ha or 0) * (self.teneur_n_pct or 0) / 100, 2)
        if not self.campagne and self.date:
            self.campagne = ParcelleCampagne.libelle_courant(self.date)
        if self.surface_ha is None:
            self.surface_ha = self.parcelle.area
        super().save(*args, **kwargs)

    @property
    def n_total(self):
        """Azote apporté sur la surface concernée, en kg."""
        return round((self.n_kg_ha or 0) * (self.surface_ha or 0), 1)

    @property
    def est_effluent(self):
        return self.nature == self.Nature.ORGANIQUE_ELEVAGE


class PosteCarbone(models.Model):
    """Un poste du bilan carbone saisi à la main, pour une campagne.

    Le facteur d'émission est copié sur le poste plutôt que lu dans un
    référentiel partagé : les facteurs sont révisés régulièrement, et un bilan
    déjà rendu ne doit pas changer parce qu'une valeur a bougé ailleurs. Sa
    provenance est gardée avec lui — un chiffre sans source ne se défend pas.

    Le stockage de carbone (haies, prairies, couverts) se saisit ici aussi,
    avec `est_stockage` : il se retranche des émissions au lieu de s'y ajouter.
    """

    class Poste(models.TextChoices):
        CARBURANT = "carburant", _("Carburants")
        ELECTRICITE = "electricite", _("Électricité et gaz")
        ENGRAIS = "engrais", _("Engrais et amendements")
        PHYTO = "phyto", _("Produits phytosanitaires")
        CHEPTEL = "cheptel", _("Cheptel")
        ALIMENTS = "aliments", _("Aliments achetés")
        MATERIEL = "materiel", _("Matériel et bâtiments")
        AUTRE = "autre", _("Autre poste")
        STOCKAGE = "stockage", _("Stockage de carbone")

    exploitation = models.ForeignKey(
        "exploitations.Exploitation", on_delete=models.CASCADE, related_name="postes_carbone"
    )
    campagne = models.CharField(_("campagne"), max_length=20)
    poste = models.CharField(_("poste"), max_length=15, choices=Poste.choices, default=Poste.AUTRE)
    libelle = models.CharField(_("libellé"), max_length=255)
    quantite = models.FloatField(_("quantité"), default=0)
    unite = models.CharField(_("unité"), max_length=30, blank=True)
    facteur = models.FloatField(_("facteur d'émission (kg CO₂e par unité)"), default=0)
    source_facteur = models.CharField(_("source du facteur"), max_length=255, blank=True)
    #: Émissions figées au calcul : quantité × facteur.
    emissions_kg = models.FloatField(_("émissions (kg CO₂e)"), default=0)
    est_stockage = models.BooleanField(_("stockage de carbone"), default=False)
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("poste du bilan carbone")
        verbose_name_plural = _("postes du bilan carbone")
        ordering = ("poste", "libelle")
        indexes = [models.Index(fields=["exploitation", "campagne"])]

    def __str__(self):
        return f"{self.libelle} — {self.emissions_kg} kg CO₂e"

    def save(self, *args, **kwargs):
        self.emissions_kg = round((self.quantite or 0) * (self.facteur or 0), 1)
        self.est_stockage = self.poste == self.Poste.STOCKAGE
        super().save(*args, **kwargs)


class Traitement(models.Model):
    """Une application de produit phytosanitaire — la ligne du registre.

    Le registre des traitements se tient quelle que soit la taille de la
    ferme et se conserve ; il est demandé au contrôle comme à la
    certification. On y note donc ce qu'il exige : la parcelle, la culture, la
    date, le produit et son AMM, la cible, la dose et la surface traitée.
    """

    class Cible(models.TextChoices):
        MALADIE = "maladie", _("Maladie")
        RAVAGEUR = "ravageur", _("Ravageur")
        ADVENTICE = "adventice", _("Adventice")
        REGULATEUR = "regulateur", _("Régulateur de croissance")
        AUTRE = "autre", _("Autre")

    exploitation = models.ForeignKey(
        "exploitations.Exploitation", on_delete=models.CASCADE, related_name="traitements"
    )
    parcelle = models.ForeignKey(
        "parcelles.Parcelle", on_delete=models.CASCADE, related_name="traitements"
    )
    date = models.DateField(_("date du traitement"))
    campagne = models.CharField(_("campagne"), max_length=20, blank=True)
    culture = models.CharField(_("culture traitée"), max_length=100, blank=True)
    produit = models.CharField(_("produit"), max_length=255)
    numero_amm = models.CharField(_("n° AMM"), max_length=20, blank=True)
    type_cible = models.CharField(_("type de cible"), max_length=12,
                                  choices=Cible.choices, default=Cible.MALADIE)
    cible = models.CharField(_("cible visée"), max_length=255, blank=True)
    dose = models.FloatField(_("dose appliquée"), null=True, blank=True)
    unite_dose = models.CharField(_("unité de dose"), max_length=20, blank=True)
    surface_ha = models.FloatField(_("surface traitée (ha)"), null=True, blank=True)
    delai_avant_recolte = models.PositiveIntegerField(
        _("délai avant récolte (jours)"), null=True, blank=True)
    operateur = models.CharField(_("applicateur"), max_length=255, blank=True)
    conditions = models.CharField(_("conditions d'application"), max_length=255, blank=True)
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("traitement phytosanitaire")
        verbose_name_plural = _("traitements phytosanitaires")
        ordering = ("-date", "-created_at")
        indexes = [models.Index(fields=["exploitation", "campagne"])]

    def __str__(self):
        return f"{self.produit} — {self.parcelle} — {self.date}"

    def save(self, *args, **kwargs):
        from parcelles.models import ParcelleCampagne

        if not self.campagne and self.date:
            self.campagne = ParcelleCampagne.libelle_courant(self.date)
        if self.surface_ha is None:
            self.surface_ha = self.parcelle.area
        super().save(*args, **kwargs)

    @property
    def recolte_possible_le(self):
        """La date à partir de laquelle on peut récolter, délai respecté."""
        import datetime

        if self.delai_avant_recolte is None or not self.date:
            return None
        return self.date + datetime.timedelta(days=self.delai_avant_recolte)


class ObservationSanitaire(models.Model):
    """Ce qu'on a vu sur une parcelle : maladie, ravageur, adventice.

    Une observation n'est pas un traitement : elle le précède, le justifie, ou
    montre qu'il n'était pas nécessaire. Les deux se lisent ensemble.
    """

    class Intensite(models.IntegerChoices):
        NULLE = 0, _("Rien à signaler")
        FAIBLE = 1, _("Présence faible")
        MOYENNE = 2, _("Présence moyenne")
        FORTE = 3, _("Présence forte")

    exploitation = models.ForeignKey(
        "exploitations.Exploitation", on_delete=models.CASCADE, related_name="observations_sanitaires"
    )
    parcelle = models.ForeignKey(
        "parcelles.Parcelle", on_delete=models.CASCADE, related_name="observations_sanitaires"
    )
    date = models.DateField(_("date de l'observation"))
    campagne = models.CharField(_("campagne"), max_length=20, blank=True)
    type_cible = models.CharField(_("type"), max_length=12,
                                  choices=Traitement.Cible.choices, default=Traitement.Cible.MALADIE)
    nom = models.CharField(_("nom"), max_length=255)
    intensite = models.IntegerField(_("intensité"), choices=Intensite.choices, default=Intensite.FAIBLE)
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("observation sanitaire")
        verbose_name_plural = _("observations sanitaires")
        ordering = ("-date", "-created_at")
        indexes = [models.Index(fields=["exploitation", "campagne"])]

    def __str__(self):
        return f"{self.nom} — {self.parcelle} — {self.date}"

    def save(self, *args, **kwargs):
        from parcelles.models import ParcelleCampagne

        if not self.campagne and self.date:
            self.campagne = ParcelleCampagne.libelle_courant(self.date)
        super().save(*args, **kwargs)
