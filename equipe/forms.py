"""Formulaires équipe : membre (ajout et fiche salarié), tâche, compte sur invitation."""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Task, TeamMember

User = get_user_model()


class TeamMemberForm(forms.ModelForm):
    """Crée un membre d'équipe : identité et rôle."""

    first_name = forms.CharField(
        label=_("Prénom"), max_length=120,
        widget=forms.TextInput(attrs={"placeholder": "Jean"}),
    )
    last_name = forms.CharField(
        label=_("Nom"), max_length=120,
        widget=forms.TextInput(attrs={"placeholder": "Dupont"}),
    )

    class Meta:
        model = TeamMember
        fields = ["email", "phone", "role"]
        widgets = {
            "email": forms.EmailInput(attrs={"placeholder": "jean@exemple.fr"}),
            "phone": forms.TextInput(attrs={"placeholder": "+33 6 12 34 56 78"}),
        }
        labels = {
            "email": _("Email"),
            "phone": _("Téléphone"),
            "role": _("Rôle"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # L'email sert d'identifiant de connexion : obligatoire.
        self.fields["email"].required = True

        member = self.instance
        if member and member.pk:
            # Édition : pré-remplir les champs non mappés depuis le nom complet.
            first, _sep, last = (member.name or "").partition(" ")
            self.fields["first_name"].initial = first
            self.fields["last_name"].initial = last

    def save(self, exploitation=None, managed_by=None, commit=True):
        member = super().save(commit=False)
        member.name = f"{self.cleaned_data['first_name']} {self.cleaned_data['last_name']}".strip()
        if exploitation is not None:
            member.exploitation = exploitation
        if managed_by is not None:
            member.managed_by = managed_by
        if commit:
            member.save()
        return member


def _date():
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class TeamMemberEditForm(TeamMemberForm):
    """Édite un membre, y compris ce que son contrat de travail demandera.

    L'ajout reste court — un nom, un email, un rôle — ; c'est à l'édition que
    l'on complète l'état civil, l'adresse et le poste, qui rempliront ensuite
    les blancs du contrat.
    """

    #: Déclaré ici plutôt que déduit du modèle : on le saisit volontiers par
    #: groupes, « 1 85 05… », et ses 15 caractères ne se comptent qu'une fois
    #: les espaces retirés.
    numero_securite_sociale = forms.CharField(
        label=_("N° de sécurité sociale"), max_length=25, required=False,
        help_text=_("15 chiffres, clé comprise."),
        widget=forms.TextInput(attrs={
            "placeholder": "1 85 05 33 063 042 26", "inputmode": "numeric", "autocomplete": "off"}),
    )

    class Meta(TeamMemberForm.Meta):
        fields = TeamMemberForm.Meta.fields + [
            "civilite", "nom_naissance", "date_naissance", "lieu_naissance", "nationalite",
            "numero_securite_sociale", "adresse", "code_postal", "ville",
            "titre_sejour_numero", "titre_sejour_expire_le", "poste", "qualification",
        ]
        widgets = {
            **TeamMemberForm.Meta.widgets,
            "date_naissance": _date(),
            "titre_sejour_expire_le": _date(),
            "lieu_naissance": forms.TextInput(attrs={"placeholder": _("Ex. Bordeaux (33)")}),
            "nationalite": forms.TextInput(attrs={"placeholder": _("Ex. française")}),
            "adresse": forms.TextInput(attrs={"placeholder": _("Ex. 12 chemin des Vignes")}),
            "qualification": forms.TextInput(attrs={"placeholder": _("Ex. palier 3, coefficient 150")}),
        }
        labels = {
            **TeamMemberForm.Meta.labels,
            "civilite": _("Civilité"),
            "nom_naissance": _("Nom de naissance"),
            "date_naissance": _("Date de naissance"),
            "lieu_naissance": _("Lieu de naissance"),
            "nationalite": _("Nationalité"),
            "adresse": _("Adresse"),
            "code_postal": _("Code postal"),
            "ville": _("Ville"),
            "titre_sejour_numero": _("N° du titre de séjour"),
            "titre_sejour_expire_le": _("Valable jusqu'au"),
            "poste": _("Intitulé du poste"),
            "qualification": _("Qualification"),
        }
        help_texts = {
            "nom_naissance": _("Seulement s'il diffère du nom d'usage."),
            "poste": _("Choisi dans la banque, il reprend sa qualification. "
                       "Laissé vide, le contrat reprend le rôle."),
            "qualification": _("Niveau, échelon ou coefficient de la convention collective."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for champ in ("date_naissance", "titre_sejour_expire_le"):
            self.fields[champ].input_formats = ["%Y-%m-%d"]
        self.fields["civilite"].choices = [("", "—"), *TeamMember.Civilite.choices]

    def clean_numero_securite_sociale(self):
        """Le NIR sans espaces, et seulement si sa clé tombe juste.

        Treize caractères puis une clé de deux chiffres : 97 moins le reste de
        la division par 97. La Corse écrit son département 2A ou 2B, que le
        calcul lit 19 et 18. Une faute de frappe se voit ici plutôt que sur la
        DPAE, où elle ferait rejeter la déclaration.
        """
        nir = "".join(self.cleaned_data.get("numero_securite_sociale", "").split()).upper()
        nir = nir.replace(".", "").replace("-", "")
        if not nir:
            return ""
        corps, cle = nir[:13], nir[13:]
        numerique = corps[:5] + corps[5:7].replace("2A", "19").replace("2B", "18") + corps[7:]
        if len(nir) != 15 or not (numerique.isdigit() and cle.isdigit()):
            raise forms.ValidationError(_("Un n° de sécurité sociale compte 15 chiffres, clé comprise."))
        if 97 - int(numerique) % 97 != int(cle):
            raise forms.ValidationError(_("La clé ne correspond pas : vérifiez le numéro."))
        return nir

    def clean(self):
        donnees = super().clean()
        naissance = donnees.get("date_naissance")
        if naissance and naissance >= timezone.localdate():
            self.add_error("date_naissance", _("La date de naissance doit être passée."))
        return donnees


class TaskForm(forms.ModelForm):
    """Crée une tâche (titre, assignation, priorité, période)."""

    class Meta:
        model = Task
        fields = ["title", "assigned_to", "priority", "status", "start_date", "due_date", "description"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": _("Ex. Tailler la parcelle nord")}),
            "description": forms.Textarea(attrs={"rows": 3, "placeholder": _("Détails (optionnel)")}),
            "start_date": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "due_date": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        }
        labels = {
            "title": _("Titre"),
            "assigned_to": _("Assignée à"),
            "priority": _("Priorité"),
            "status": _("Statut"),
            "start_date": _("Début"),
            "due_date": _("Échéance"),
            "description": _("Description"),
        }

    def __init__(self, *args, exploitation=None, **kwargs):
        super().__init__(*args, **kwargs)
        members = (
            TeamMember.objects.filter(exploitation=exploitation)
            if exploitation else TeamMember.objects.none()
        )
        self.fields["assigned_to"].queryset = members
        self.fields["assigned_to"].required = False
        self.fields["assigned_to"].empty_label = _("Non assignée")
        self.fields["description"].required = False
        for champ in ("start_date", "due_date"):
            self.fields[champ].required = False
            self.fields[champ].input_formats = ["%Y-%m-%dT%H:%M"]


class InvitationAccountForm(UserCreationForm):
    """Crée le compte d'un membre invité.

    Volontairement plus court que `RegisterForm` : l'email vient de la fiche
    (il est la cible de l'invitation, pas une saisie), et on ne réclame ni date
    de naissance ni adresse — un salarié qu'on invite doit pouvoir entrer en
    deux champs. Le reste se complète depuis son profil.
    """

    first_name = forms.CharField(label=_("Prénom"), max_length=150)
    last_name = forms.CharField(label=_("Nom"), max_length=150)

    class Meta:
        model = User
        fields = ("first_name", "last_name")

    field_order = ["first_name", "last_name", "password1", "password2"]

    def __init__(self, *args, email="", **kwargs):
        super().__init__(*args, **kwargs)
        self.email = email

    def clean(self):
        # `email` n'étant pas un champ du formulaire, sa contrainte d'unicité
        # n'est pas vérifiée par le ModelForm : on la rejoue ici. Le cas normal
        # est intercepté par la vue (qui renvoie vers la connexion) ; ceci ferme
        # la course entre deux inscriptions simultanées.
        if User.objects.filter(email__iexact=self.email).exists():
            raise forms.ValidationError(
                _("Un compte existe déjà pour %(email)s. Connectez-vous pour "
                  "rejoindre l'équipe.") % {"email": self.email}
            )
        return super().clean()

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = User.objects.normalize_email(self.email)
        if commit:
            user.save()
        return user
