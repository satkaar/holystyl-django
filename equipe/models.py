"""Équipe et tâches.

Fidèle aux tables Drizzle `team_members`, `tasks`, `task_reminders`.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import TimeStampedModel


class TeamMember(TimeStampedModel):
    class Role(models.TextChoices):
        ASSOCIE = "associe", _("Associé")
        CHEF = "chef", _("Chef d'équipe")
        OUVRIER = "ouvrier", _("Ouvrier")
        SAISONNIER = "saisonnier", _("Saisonnier")
        PRESTATAIRE = "prestataire", _("Prestataire")

    exploitation = models.ForeignKey("exploitations.Exploitation", on_delete=models.CASCADE, related_name="team_members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(_("nom"), max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(_("téléphone"), max_length=30, blank=True)
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.OUVRIER)
    color = models.CharField(max_length=7, default="#29738f")
    is_active = models.BooleanField(default=True)
    is_online = models.BooleanField(default=False)
    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    location_token = models.CharField(max_length=64, blank=True)
    location_token_expires_at = models.DateTimeField(null=True, blank=True)
    allowed_modules = models.JSONField(default=list, blank=True)
    #: Invitation à ouvrir un espace employé. Aucun jeton stocké : il est signé
    #: (`equipe.invitations`) et porte l'email, donc changer l'email d'un membre
    #: périme les liens déjà envoyés. Ces deux dates ne servent qu'à l'affichage.
    invitation_sent_at = models.DateTimeField(_("invitation envoyée le"), null=True, blank=True)
    invitation_accepted_at = models.DateTimeField(_("invitation acceptée le"), null=True, blank=True)
    preferred_locale = models.CharField(max_length=5, default="fr")
    managed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="managed_members"
    )

    #: Ce qu'un contrat de travail et la DPAE demandent du salarié. Tout est
    #: facultatif : un prestataire n'en a pas besoin, et un blanc laissé vide
    #: s'imprime en pointillés sur le contrat plutôt que d'en bloquer l'édition.
    class Civilite(models.TextChoices):
        MADAME = "mme", _("Madame")
        MONSIEUR = "m", _("Monsieur")

    civilite = models.CharField(_("civilité"), max_length=3, choices=Civilite.choices, blank=True)
    nom_naissance = models.CharField(_("nom de naissance"), max_length=120, blank=True)
    date_naissance = models.DateField(_("date de naissance"), null=True, blank=True)
    lieu_naissance = models.CharField(_("lieu de naissance"), max_length=255, blank=True)
    nationalite = models.CharField(_("nationalité"), max_length=80, blank=True)
    numero_securite_sociale = models.CharField(_("n° de sécurité sociale"), max_length=15, blank=True)
    adresse = models.CharField(_("adresse"), max_length=255, blank=True)
    code_postal = models.CharField(_("code postal"), max_length=10, blank=True)
    ville = models.CharField(_("ville"), max_length=120, blank=True)
    #: Hors Union européenne, l'employeur doit vérifier l'autorisation de
    #: travail avant l'embauche et la suivre jusqu'à son terme.
    titre_sejour_numero = models.CharField(_("n° du titre de séjour"), max_length=40, blank=True)
    titre_sejour_expire_le = models.DateField(_("titre de séjour valable jusqu'au"), null=True, blank=True)
    #: L'intitulé du poste, plus précis que le rôle — « tractoriste »,
    #: « ouvrier viticole » — et la qualification de la convention collective.
    poste = models.CharField(_("intitulé du poste"), max_length=255, blank=True)
    qualification = models.CharField(_("qualification"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("membre d'équipe")
        verbose_name_plural = _("membres d'équipe")
        ordering = ("name",)

    def __str__(self):
        return self.name

    @property
    def peut_etre_invite(self) -> bool:
        """Un membre s'invite s'il a un email et pas encore de compte lié."""
        return bool(self.email) and self.user_id is None

    @property
    def adresse_complete(self) -> str:
        return " ".join(p for p in (self.adresse, self.code_postal, self.ville) if p)

    @property
    def intitule_poste(self) -> str:
        """Le poste saisi, ou à défaut le rôle dans l'équipe."""
        return self.poste or self.get_role_display()


class Task(TimeStampedModel):
    class Priority(models.TextChoices):
        HAUTE = "haute", _("Haute")
        NORMALE = "normale", _("Normale")
        BASSE = "basse", _("Basse")

    class Status(models.TextChoices):
        TODO = "todo", _("À faire")
        INPROGRESS = "inprogress", _("En cours")
        DONE = "done", _("Terminée")
        VALIDATED = "validated", _("Validée")

    exploitation = models.ForeignKey("exploitations.Exploitation", on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(_("titre"), max_length=255)
    description = models.TextField(blank=True)
    assigned_to = models.ForeignKey(TeamMember, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")
    #: Parcelle principale — conservée (API, espace employé) et tenue à jour
    #: avec la première parcelle de `parcelles`.
    parcelle = models.ForeignKey("parcelles.Parcelle", on_delete=models.SET_NULL, null=True, blank=True)
    #: Une tâche peut couvrir plusieurs parcelles.
    parcelles = models.ManyToManyField(
        "parcelles.Parcelle", blank=True, related_name="tasks", verbose_name=_("parcelles")
    )
    #: Une tâche peut s'étaler sur plusieurs jours : `start_date` est le début,
    #: `due_date` la fin (et reste l'échéance utilisée par les rappels).
    start_date = models.DateTimeField(_("début"), null=True, blank=True)
    due_date = models.DateTimeField(null=True, blank=True)
    #: Sous-tâche : une tâche fille est une tâche à part entière (assignable,
    #: datée, avec son statut) rattachée à son parent.
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="subtasks", verbose_name=_("tâche parente")
    )
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMALE)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.TODO)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    reminder_sent_24h = models.BooleanField(default=False)
    reminder_sent_1h = models.BooleanField(default=False)
    sms_sent_24h = models.BooleanField(default=False)
    sms_sent_1h = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("tâche")
        verbose_name_plural = _("tâches")
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["exploitation", "status"])]

    def __str__(self):
        return self.title

    @property
    def periode(self):
        """(début, fin) en dates, l'une valant l'autre si une seule est saisie."""
        debut = self.start_date or self.due_date
        fin = self.due_date or self.start_date
        return debut, fin

    @property
    def is_done(self) -> bool:
        return self.status in (self.Status.DONE, self.Status.VALIDATED)

    @property
    def subtasks_done(self) -> int:
        return sum(1 for st in self.subtasks.all() if st.is_done)

    @property
    def subtasks_total(self) -> int:
        return len(self.subtasks.all())


class TaskReminder(models.Model):
    class Type(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"

    class Status(models.TextChoices):
        PENDING = "pending", _("En attente")
        SENT = "sent", _("Envoyé")
        FAILED = "failed", _("Échoué")

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="reminders")
    type = models.CharField(max_length=10, choices=Type.choices)
    scheduled_at = models.DateTimeField()
    sent_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("rappel de tâche")
        verbose_name_plural = _("rappels de tâche")


class Poste(TimeStampedModel):
    """Un poste de la banque de l'exploitation, repris d'une saisie à l'autre.

    Il se propose partout où l'on écrit un intitulé de poste — fiche d'un
    membre, offre d'emploi, contrat — et apporte avec lui ce qu'on retaperait
    sinon : la qualification, les missions, le profil recherché.
    """

    exploitation = models.ForeignKey(
        "exploitations.Exploitation", on_delete=models.CASCADE, related_name="postes")
    intitule = models.CharField(_("intitulé du poste"), max_length=255)
    qualification = models.CharField(_("qualification"), max_length=255, blank=True)
    missions = models.TextField(_("missions"), blank=True)
    profil = models.TextField(_("profil recherché"), blank=True)

    class Meta:
        verbose_name = _("poste")
        verbose_name_plural = _("postes")
        ordering = ("intitule",)
        constraints = [
            models.UniqueConstraint(fields=["exploitation", "intitule"], name="un_intitule_par_exploitation"),
        ]

    def __str__(self):
        return self.intitule


class ModeleContrat(TimeStampedModel):
    """Un modèle de contrat de travail, adapté une fois puis réutilisé.

    Le corps est du texte libre semé de jetons — `{{ salarie }}`, `{{ poste }}` —
    que la génération remplace par les données du salarié. C'est ce qui évite
    de retaper le même contrat à chaque embauche.
    """

    class Type(models.TextChoices):
        CDI = "cdi", _("CDI")
        CDD = "cdd", _("CDD")
        SAISONNIER = "saisonnier", _("Contrat saisonnier")
        APPRENTISSAGE = "apprentissage", _("Contrat d'apprentissage")
        PROFESSIONNALISATION = "professionnalisation", _("Contrat de professionnalisation")
        STAGE = "stage", _("Convention de stage")
        TESA = "tesa", _("TESA — titre emploi simplifié agricole")
        AUTRE = "autre", _("Autre")

    exploitation = models.ForeignKey(
        "exploitations.Exploitation", on_delete=models.CASCADE, related_name="modeles_contrat")
    nom = models.CharField(_("nom du modèle"), max_length=255)
    type_contrat = models.CharField(_("type"), max_length=25,
                                    choices=Type.choices, default=Type.CDI)
    corps = models.TextField(_("corps du contrat"))
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("modèle de contrat")
        verbose_name_plural = _("modèles de contrat")
        ordering = ("nom",)

    def __str__(self):
        return self.nom


class ContratTravail(TimeStampedModel):
    """Le contrat d'un salarié, établi à partir d'un modèle.

    Son corps est figé à l'établissement : retoucher le modèle plus tard ne
    doit pas réécrire un contrat déjà remis, encore moins signé.
    """

    class Statut(models.TextChoices):
        BROUILLON = "brouillon", _("Brouillon")
        ETABLI = "etabli", _("Établi")
        SIGNE = "signe", _("Signé")
        TERMINE = "termine", _("Terminé")

    exploitation = models.ForeignKey(
        "exploitations.Exploitation", on_delete=models.CASCADE, related_name="contrats_travail")
    membre = models.ForeignKey(TeamMember, on_delete=models.CASCADE,
                               related_name="contrats", verbose_name=_("salarié"))
    modele = models.ForeignKey(ModeleContrat, on_delete=models.SET_NULL, null=True, blank=True,
                               verbose_name=_("modèle d'origine"))
    type_contrat = models.CharField(_("type"), max_length=25,
                                    choices=ModeleContrat.Type.choices,
                                    default=ModeleContrat.Type.CDI)
    statut = models.CharField(_("statut"), max_length=12,
                              choices=Statut.choices, default=Statut.BROUILLON)

    poste = models.CharField(_("poste"), max_length=255, blank=True)
    lieu = models.CharField(_("lieu de travail"), max_length=255, blank=True)
    date_debut = models.DateField(_("date de début"), null=True, blank=True)
    date_fin = models.DateField(_("date de fin"), null=True, blank=True)
    duree_hebdo = models.FloatField(_("durée hebdomadaire (h)"), null=True, blank=True)
    remuneration = models.FloatField(_("rémunération brute mensuelle"), null=True, blank=True)
    date_signature = models.DateField(_("date de signature"), null=True, blank=True)
    #: La signature apposée sur le PDF. Choisie à l'établissement — une
    #: exploitation peut en avoir plusieurs — et figée avec le contrat :
    #: changer de signature ensuite ne doit pas resigner un contrat remis.
    signature = models.ForeignKey(
        "identite.Piece", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="contrats_signes", verbose_name=_("signature de l'employeur"))

    #: Texte figé au moment de l'établissement, jetons déjà remplacés.
    corps = models.TextField(_("corps du contrat"), blank=True)

    class Meta:
        verbose_name = _("contrat de travail")
        verbose_name_plural = _("contrats de travail")
        ordering = ("-date_debut", "-created_at")
        indexes = [models.Index(fields=["exploitation", "statut"])]

    def __str__(self):
        return f"{self.membre.name} — {self.get_type_contrat_display()}"

    @property
    def est_en_cours(self):
        """Un contrat court tant qu'il n'a pas de terme, ou que le terme est à venir."""
        from django.utils import timezone

        if self.statut in (self.Statut.BROUILLON, self.Statut.TERMINE):
            return False
        return self.date_fin is None or self.date_fin >= timezone.localdate()


class OffreEmploi(TimeStampedModel):
    """Une offre d'emploi de l'exploitation, publiable sur l'espace public.

    Le slug sert d'adresse publique et ne change plus une fois l'offre en
    ligne : un lien partagé ou indexé doit continuer de fonctionner même si le
    titre est retouché.
    """

    class Statut(models.TextChoices):
        BROUILLON = "brouillon", _("Brouillon")
        PUBLIEE = "publiee", _("Publiée")
        POURVUE = "pourvue", _("Pourvue")
        CLOSE = "close", _("Close")

    exploitation = models.ForeignKey(
        "exploitations.Exploitation", on_delete=models.CASCADE, related_name="offres_emploi")
    titre = models.CharField(_("intitulé du poste"), max_length=255)
    slug = models.SlugField(_("adresse publique"), max_length=280, unique=True, blank=True)
    type_contrat = models.CharField(_("type de contrat"), max_length=25,
                                    choices=ModeleContrat.Type.choices,
                                    default=ModeleContrat.Type.SAISONNIER)
    description = models.TextField(_("description du poste"))
    profil = models.TextField(_("profil recherché"), blank=True)
    lieu = models.CharField(_("lieu de travail"), max_length=255, blank=True)
    date_debut = models.DateField(_("prise de poste"), null=True, blank=True)
    duree_hebdo = models.FloatField(_("durée hebdomadaire (h)"), null=True, blank=True)
    #: Texte libre : une offre annonce souvent « selon profil » ou une fourchette.
    remuneration = models.CharField(_("rémunération"), max_length=255, blank=True)
    logement = models.BooleanField(_("logement possible"), default=False)

    statut = models.CharField(_("statut"), max_length=10,
                              choices=Statut.choices, default=Statut.BROUILLON)
    publiee_le = models.DateTimeField(_("publiée le"), null=True, blank=True)
    expire_le = models.DateField(_("visible jusqu'au"), null=True, blank=True)

    class Meta:
        verbose_name = _("offre d'emploi")
        verbose_name_plural = _("offres d'emploi")
        ordering = ("-publiee_le", "-created_at")
        indexes = [models.Index(fields=["statut", "publiee_le"])]

    def __str__(self):
        return self.titre

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._slug_libre()
        super().save(*args, **kwargs)

    def _slug_libre(self):
        from django.utils.text import slugify

        base = slugify(f"{self.titre}-{self.exploitation.name}")[:250] or "offre"
        candidat, n = base, 2
        while OffreEmploi.objects.filter(slug=candidat).exclude(pk=self.pk).exists():
            candidat = f"{base}-{n}"
            n += 1
        return candidat

    @property
    def est_visible(self):
        """Publiée, et pas encore expirée."""
        from django.utils import timezone

        if self.statut != self.Statut.PUBLIEE:
            return False
        return self.expire_le is None or self.expire_le >= timezone.localdate()


class Candidature(TimeStampedModel):
    """Une candidature déposée depuis l'espace public."""

    class Statut(models.TextChoices):
        RECUE = "recue", _("Reçue")
        VUE = "vue", _("Vue")
        RETENUE = "retenue", _("Retenue")
        REFUSEE = "refusee", _("Refusée")

    offre = models.ForeignKey(OffreEmploi, on_delete=models.CASCADE, related_name="candidatures")
    nom = models.CharField(_("nom"), max_length=255)
    email = models.EmailField(_("email"))
    telephone = models.CharField(_("téléphone"), max_length=30, blank=True)
    message = models.TextField(_("message"), blank=True)
    cv = models.FileField(_("CV"), upload_to="candidatures/%Y/%m/", blank=True)
    statut = models.CharField(_("statut"), max_length=10,
                              choices=Statut.choices, default=Statut.RECUE)

    class Meta:
        verbose_name = _("candidature")
        verbose_name_plural = _("candidatures")
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.nom} — {self.offre.titre}"


class FichePaie(TimeStampedModel):
    """Le bulletin de paie d'un salarié pour une période.

    L'application ne calcule pas la paie : les taux MSA, la convention
    collective et les exonérations relèvent du droit et changent. Elle
    enregistre ce que l'employeur ou son comptable a établi, le présente, et
    vérifie que les lignes s'additionnent bien aux totaux annoncés.
    """

    class Statut(models.TextChoices):
        BROUILLON = "brouillon", _("Brouillon")
        EMISE = "emise", _("Émise")
        PAYEE = "payee", _("Payée")

    exploitation = models.ForeignKey(
        "exploitations.Exploitation", on_delete=models.CASCADE, related_name="fiches_paie")
    membre = models.ForeignKey(TeamMember, on_delete=models.CASCADE,
                               related_name="fiches_paie", verbose_name=_("salarié"))
    contrat = models.ForeignKey(ContratTravail, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="fiches_paie", verbose_name=_("contrat"))

    periode_debut = models.DateField(_("période du"))
    periode_fin = models.DateField(_("période au"))

    heures_travaillees = models.FloatField(_("heures travaillées"), null=True, blank=True)
    heures_supplementaires = models.FloatField(_("heures supplémentaires"), null=True, blank=True)

    salaire_brut = models.FloatField(_("salaire brut"), default=0)
    cotisations_salariales = models.FloatField(_("cotisations salariales"), default=0)
    cotisations_patronales = models.FloatField(_("cotisations patronales"), default=0)
    net_imposable = models.FloatField(_("net imposable"), null=True, blank=True)
    net_a_payer = models.FloatField(_("net à payer"), default=0)

    date_paiement = models.DateField(_("payée le"), null=True, blank=True)
    mode_paiement = models.CharField(_("mode de paiement"), max_length=60, blank=True)
    statut = models.CharField(_("statut"), max_length=10,
                              choices=Statut.choices, default=Statut.BROUILLON)
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("fiche de paie")
        verbose_name_plural = _("fiches de paie")
        ordering = ("-periode_debut", "membre__name")
        indexes = [models.Index(fields=["exploitation", "periode_debut"])]
        constraints = [
            models.UniqueConstraint(fields=["membre", "periode_debut", "periode_fin"],
                                    name="une_fiche_par_salarie_et_periode"),
        ]

    def __str__(self):
        return f"{self.membre.name} — {self.periode_debut:%m/%Y}"

    @property
    def total_lignes_salariales(self):
        return round(sum(l.part_salariale or 0 for l in self.lignes.all()), 2)

    @property
    def total_lignes_patronales(self):
        return round(sum(l.part_patronale or 0 for l in self.lignes.all()), 2)

    @property
    def addition_coherente(self):
        """Les lignes s'additionnent-elles aux totaux annoncés ?

        Vrai quand il n'y a pas de ligne : on ne reproche rien à une fiche
        saisie en totaux. Un centime d'écart est toléré, les arrondis existent.
        """
        if not self.lignes.exists():
            return True
        return (abs(self.total_lignes_salariales - (self.cotisations_salariales or 0)) <= 0.01
                and abs(self.total_lignes_patronales - (self.cotisations_patronales or 0)) <= 0.01)

    @property
    def cout_employeur(self):
        """Brut plus charges patronales : ce que la paie coûte à la ferme."""
        return round((self.salaire_brut or 0) + (self.cotisations_patronales or 0), 2)


class LignePaie(models.Model):
    """Une rubrique du bulletin : cotisation, retenue ou complément."""

    fiche = models.ForeignKey(FichePaie, on_delete=models.CASCADE, related_name="lignes")
    libelle = models.CharField(_("libellé"), max_length=255)
    base = models.FloatField(_("base"), null=True, blank=True)
    taux = models.FloatField(_("taux (%)"), null=True, blank=True)
    part_salariale = models.FloatField(_("part salariale"), null=True, blank=True)
    part_patronale = models.FloatField(_("part patronale"), null=True, blank=True)
    ordre = models.PositiveIntegerField(_("ordre"), default=0)

    class Meta:
        verbose_name = _("ligne de paie")
        verbose_name_plural = _("lignes de paie")
        ordering = ("ordre", "id")

    def __str__(self):
        return self.libelle
