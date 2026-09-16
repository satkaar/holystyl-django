"""Vues web équipe : équipe, tâches, mes-tâches (technicien), partage géoloc public."""

import json

from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.core.mail import BadHeaderError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from core import espaces as espaces_service
from core.decorators import espace_requis
from core.espaces import EMPLOYE, EXPLOITANT
from exploitations.models import Exploitation
from parcelles import carte as carte_parcelles
from parcelles.models import Parcelle

from . import invitations
from .forms import InvitationAccountForm, TaskForm, TeamMemberEditForm, TeamMemberForm
from . import contrats as contrats_service, postes as postes_service, services
from .models import (Candidature, ContratTravail, FichePaie, LignePaie, ModeleContrat,
                     OffreEmploi, Poste, Task, TeamMember)

User = get_user_model()


def _exploitation(request):
    return Exploitation.objects.filter(owner=request.user).first()


@login_required
@espace_requis(EXPLOITANT)
def equipe(request):
    exploitation = _exploitation(request)
    form = TeamMemberForm()
    if request.method == "POST":
        if exploitation is None:
            messages.error(request, _("Créez d'abord votre exploitation avant d'ajouter un membre."))
        else:
            form = TeamMemberForm(request.POST)
            if form.is_valid():
                member = form.save(exploitation=exploitation, managed_by=request.user)
                messages.success(request, _("%(name)s a été ajouté à l'équipe.") % {"name": member.name})
                return redirect("equipe:equipe")
    members = TeamMember.objects.filter(exploitation=exploitation) if exploitation else TeamMember.objects.none()
    return render(request, "equipe/equipe.html", {
        "members": members, "form": form, "roles": TeamMember.Role.choices, "page_title": _("Équipe"),
    })


@login_required
def membre_edit(request, pk):
    exploitation = _exploitation(request)
    member = get_object_or_404(TeamMember, pk=pk, exploitation=exploitation)
    # Ce que le contrat attend du salarié, et ce que la fiche enregistrée en
    # sait déjà. Relevé avant la validation, qui écrit la saisie sur `member`
    # même quand elle est refusée.
    dossier = [
        (_("Civilité"), bool(member.civilite)),
        (_("Date de naissance"), bool(member.date_naissance)),
        (_("Lieu de naissance"), bool(member.lieu_naissance)),
        (_("Nationalité"), bool(member.nationalite)),
        (_("N° de sécurité sociale"), bool(member.numero_securite_sociale)),
        (_("Adresse"), bool(member.adresse and member.code_postal and member.ville)),
        (_("Intitulé du poste"), bool(member.poste)),
        (_("Qualification"), bool(member.qualification)),
    ]
    if request.method == "POST":
        form = TeamMemberEditForm(request.POST, instance=member)
        if form.is_valid():
            form.save()
            messages.success(request, _("%(name)s a été mis à jour.") % {"name": member.name})
            return redirect("equipe:equipe")
    else:
        form = TeamMemberEditForm(instance=member)
    return render(request, "equipe/edit.html", {
        "form": form, "member": member, "page_title": _("Modifier le membre"),
        "banque_postes": postes_service.suggestions(exploitation),
        "dossier": dossier, "dossier_complet": sum(rempli for _libelle, rempli in dossier),
    })


@login_required
@require_POST
def membre_inviter(request, pk):
    """Envoie au membre le lien qui lui ouvrira son espace employé."""
    exploitation = _exploitation(request)
    member = get_object_or_404(TeamMember, pk=pk, exploitation=exploitation)
    if member.user_id is not None:
        messages.info(request, _("%(name)s a déjà un compte lié.") % {"name": member.name})
    elif not member.email:
        messages.error(request, _("Renseignez un email avant d'inviter %(name)s.") % {"name": member.name})
    else:
        try:
            invitations.envoyer(member, request)
        except (BadHeaderError, OSError):
            # SMTP indisponible ou refusé : le dire, ne pas laisser croire à un envoi.
            messages.error(request, _("L'invitation n'a pas pu être envoyée à %(email)s. "
                                      "Réessayez dans un instant.") % {"email": member.email})
        else:
            messages.success(request, _("Invitation envoyée à %(email)s.") % {"email": member.email})
    return redirect("equipe:membre_edit", pk=member.pk)


def invitation(request, token):
    """Acceptation d'une invitation — publique, le jeton fait l'authentification.

    Quatre situations, une seule page : lien mort, compte à créer, compte
    existant à connecter, ou compte déjà connecté à confirmer.
    """
    member = invitations.membre_du_jeton(token)
    if member is None:
        return render(request, "equipe/invitation.html",
                      {"etat": "invalide", "page_title": _("Invitation")}, status=410)

    if member.user_id is not None:
        if request.user.is_authenticated and request.user.pk == member.user_id:
            return redirect("core:dashboard")
        return render(request, "equipe/invitation.html",
                      {"etat": "deja_utilisee", "membre": member, "page_title": _("Invitation")},
                      status=410)

    if request.user.is_authenticated:
        etat = "confirmer"
        form = None
    elif User.objects.filter(email__iexact=member.email).exists():
        etat = "connexion"
        form = None
    else:
        etat = "creation"
        form = InvitationAccountForm(email=member.email)

    if request.method == "POST" and etat in ("confirmer", "creation"):
        user = request.user
        if etat == "creation":
            form = InvitationAccountForm(request.POST, email=member.email)
            if not form.is_valid():
                return _rendre_invitation(request, etat, member, form)
            user = form.save()
            login(request, user)
        invitations.accepter(member, user)
        # Le rattachement vient de naître : le contexte d'espaces posé par le
        # middleware en début de requête l'ignore encore.
        espaces_service.invalider(request)
        # L'invitation porte sur l'espace employé : l'ouvrir tout de suite,
        # sans quoi un exploitant invité resterait sur son espace habituel.
        espaces_service.definir_espace(request, EMPLOYE)
        messages.success(request, _("Bienvenue dans l'équipe de %(expl)s.")
                         % {"expl": member.exploitation.name})
        return redirect("core:dashboard")

    return _rendre_invitation(request, etat, member, form)


def _rendre_invitation(request, etat, member, form):
    return render(request, "equipe/invitation.html", {
        "etat": etat, "membre": member, "form": form,
        "url_connexion": f"{reverse('accounts:login')}?next={request.path}",
        "page_title": _("Invitation"),
    })


@login_required
@require_POST
def membre_delete(request, pk):
    """Supprime un membre d'équipe."""
    exploitation = _exploitation(request)
    get_object_or_404(TeamMember, pk=pk, exploitation=exploitation).delete()
    messages.success(request, _("Membre supprimé."))
    return redirect("equipe:equipe")


@login_required
def taches(request):
    exploitation = _exploitation(request)
    form = TaskForm(exploitation=exploitation)
    if request.method == "POST":
        if exploitation is None:
            messages.error(request, _("Créez d'abord votre exploitation avant d'ajouter une tâche."))
        else:
            form = TaskForm(request.POST, exploitation=exploitation)
            if form.is_valid():
                task = form.save(commit=False)
                task.exploitation = exploitation
                task.created_by = request.user
                task.save()
                # Parcelles et sous-tâches passent par les mêmes services que la
                # modale du planning : les deux écrans créent la même tâche.
                services.enregistrer_parcelles(task, request, exploitation)
                services.enregistrer_sous_taches(task, request, exploitation)
                messages.success(request, _("Tâche « %(t)s » ajoutée.") % {"t": task.title})
                return redirect("equipe:taches")
    # « Mes tâches » : membre lié à mon compte (par user, ou à défaut par email).
    my_member = None
    if exploitation:
        q = Q(user=request.user)
        if request.user.email:
            q |= Q(email__iexact=request.user.email)
        my_member = TeamMember.objects.filter(exploitation=exploitation).filter(q).first()
    tasks = list(
        Task.objects.filter(exploitation=exploitation).select_related("assigned_to", "parent")
        if exploitation else Task.objects.none()
    )
    for t in tasks:
        t.is_mine = my_member is not None and t.assigned_to_id == my_member.id
    has_mine = any(t.is_mine for t in tasks)
    # De quoi proposer le même choix que la modale du planning : parcelles à
    # cocher (avec leur carte) et assignés des sous-tâches.
    parcelles = list(Parcelle.objects.filter(exploitation=exploitation)) if exploitation else []
    return render(request, "equipe/taches.html", {
        "tasks": tasks, "form": form, "has_mine": has_mine,
        "priorities": Task.Priority.choices, "statuses": Task.Status.choices,
        "team_members": (TeamMember.objects.filter(exploitation=exploitation)
                         if exploitation else TeamMember.objects.none()),
        **carte_parcelles.contexte(parcelles),
        "page_title": _("Tâches"),
    })


@login_required
def taches_edit(request, pk):
    """Modifie une tâche (soumis depuis la modale de /taches/)."""
    exploitation = _exploitation(request)
    task = get_object_or_404(Task, pk=pk, exploitation=exploitation)
    if request.method == "POST":
        form = TaskForm(request.POST, instance=task, exploitation=exploitation)
        if form.is_valid():
            form.save()
            messages.success(request, _("Tâche « %(t)s » modifiée.") % {"t": task.title})
        else:
            messages.error(request, _("Modification impossible : vérifiez les champs."))
    return redirect("equipe:taches")


@login_required
@require_POST
def taches_delete(request, pk):
    """Supprime une tâche."""
    exploitation = _exploitation(request)
    get_object_or_404(Task, pk=pk, exploitation=exploitation).delete()
    messages.success(request, _("Tâche supprimée."))
    return redirect("equipe:taches")


def location_share(request, token):
    """Page publique de partage de position (lien 24h)."""
    member = TeamMember.objects.filter(
        location_token=token, location_token_expires_at__gt=timezone.now()
    ).first()
    return render(request, "equipe/location_share.html", {"member": member, "token": token})


def _rh_placeholder(request, title):
    """Page « en préparation » pour une sous-section RH."""
    return render(request, "equipe/placeholder.html", {"title": title, "page_title": title})


def _to_float(valeur, defaut=None):
    try:
        return float(str(valeur).replace(",", ".").replace(" ", "").strip())
    except (TypeError, ValueError):
        return defaut


def _to_date(valeur):
    from django.utils.dateparse import parse_date

    return parse_date((valeur or "").strip()) or None


@login_required
@espace_requis(EXPLOITANT)
def contrats(request):
    """Modèles de contrat et contrats établis."""
    exploitation = _exploitation(request)
    return render(request, "equipe/contrats.html", {
        "modeles": ModeleContrat.objects.filter(exploitation=exploitation) if exploitation else [],
        "contrats": (ContratTravail.objects.filter(exploitation=exploitation)
                     .select_related("membre", "modele") if exploitation else []),
        "membres": TeamMember.objects.filter(exploitation=exploitation) if exploitation else [],
        "types": ModeleContrat.Type.choices,
        "statuts": ContratTravail.Statut.choices,
        "jetons": contrats_service.JETONS,
        "squelettes": contrats_service.SQUELETTES,
        "page_title": _("Contrats de travail"),
    })


@login_required
@espace_requis(EXPLOITANT)
@require_POST
def modeles_importer(request):
    """Copie les squelettes fournis dans les modèles de l'exploitation.

    Ce sont des points de départ : une fois copiés, ils appartiennent à la
    ferme et s'adaptent sans que la mise à jour de l'application les écrase.
    """
    exploitation = _exploitation(request)
    if exploitation is None:
        messages.error(request, _("Créez d'abord votre exploitation."))
        return redirect("equipe:contrats")

    ajoutes = 0
    for squelette in contrats_service.SQUELETTES:
        _modele, cree = ModeleContrat.objects.get_or_create(
            exploitation=exploitation, nom=str(squelette["nom"]),
            defaults={"type_contrat": squelette["type_contrat"],
                      "corps": str(squelette["corps"])})
        ajoutes += 1 if cree else 0
    messages.success(request, _("%(n)s modèle(s) ajouté(s).") % {"n": ajoutes})
    return redirect("equipe:contrats")


@login_required
@espace_requis(EXPLOITANT)
@require_POST
def modele_save(request, pk=None):
    exploitation = _exploitation(request)
    if exploitation is None:
        messages.error(request, _("Créez d'abord votre exploitation."))
        return redirect("equipe:contrats")

    modele = (get_object_or_404(ModeleContrat, pk=pk, exploitation=exploitation)
              if pk else ModeleContrat(exploitation=exploitation))
    nom = (request.POST.get("nom") or "").strip()
    corps = (request.POST.get("corps") or "").strip()
    if not (nom and corps):
        messages.error(request, _("Un modèle a besoin d'un nom et d'un corps."))
        return redirect("equipe:contrats")

    type_contrat = request.POST.get("type_contrat") or ModeleContrat.Type.CDI
    modele.nom = nom[:255]
    modele.corps = corps
    modele.type_contrat = (type_contrat if type_contrat in ModeleContrat.Type.values
                           else ModeleContrat.Type.CDI)
    modele.notes = (request.POST.get("notes") or "").strip()
    modele.save()
    return redirect("equipe:contrats")


@login_required
@espace_requis(EXPLOITANT)
@require_POST
def modele_delete(request, pk):
    exploitation = _exploitation(request)
    get_object_or_404(ModeleContrat, pk=pk, exploitation=exploitation).delete()
    return redirect("equipe:contrats")


def _champs_contrat(request):
    return {
        "poste": (request.POST.get("poste") or "").strip()[:255],
        "lieu": (request.POST.get("lieu") or "").strip()[:255],
        "date_debut": _to_date(request.POST.get("date_debut")),
        "date_fin": _to_date(request.POST.get("date_fin")),
        "duree_hebdo": _to_float(request.POST.get("duree_hebdo")),
        "remuneration": _to_float(request.POST.get("remuneration")),
    }


#: Les blancs qui appellent un calendrier, et ceux qui appellent un nombre.
_BLANCS_DATE = {"date_debut", "date_fin", "date_du_jour", "salarie_date_naissance"}
_BLANCS_NOMBRE = {"duree_hebdo", "remuneration"}


def _propositions(exploitation):
    """Ce que l'exploitation sait déjà, par blanc.

    Rien n'est inventé : les postes viennent des rôles de l'équipe, les lieux
    du siège et des communes des parcelles. Un blanc sans proposition reste
    une saisie libre.
    """
    lieux = [valeur for valeur in (
        getattr(exploitation, "city", ""),
        " ".join(p for p in (getattr(exploitation, "address", ""),
                             getattr(exploitation, "postal_code", ""),
                             getattr(exploitation, "city", "")) if p),
        exploitation.name,
    ) if valeur]
    lieux += list(Parcelle.objects.filter(exploitation=exploitation)
                  .exclude(commune="").values_list("commune", flat=True).distinct())
    vus, uniques = set(), []
    for lieu in lieux:
        if lieu not in vus:
            vus.add(lieu)
            uniques.append(lieu)
    postes = list(dict.fromkeys(
        [libelle for _code, libelle in TeamMember.Role.choices]
        + list(TeamMember.objects.filter(exploitation=exploitation).exclude(poste="")
               .values_list("poste", flat=True))
        + [p["intitule"] for p in postes_service.suggestions(exploitation)]))
    return {
        "poste": postes,
        "lieu": uniques,
        "duree_hebdo": ["35", "39"],
    }


@login_required
@espace_requis(EXPLOITANT)
def contrat_etablir(request, pk):
    """Le contrat comme un document, chaque blanc prêt à être choisi.

    Partir du modèle et remplir les blancs sur place vaut mieux qu'un
    formulaire à côté : on lit la phrase où la valeur va s'inscrire, et on
    voit ce qu'il reste à compléter avant de remettre le contrat.
    """
    exploitation = _exploitation(request)
    modele = ModeleContrat.objects.filter(pk=pk, exploitation=exploitation).first()
    if not modele:
        messages.error(request, _("Ce modèle n'existe plus."))
        return redirect("equipe:contrats")

    membres = list(TeamMember.objects.filter(exploitation=exploitation, is_active=True))
    connues = contrats_service.valeurs_pour(ContratTravail(
        exploitation=exploitation, membre=membres[0] if membres else TeamMember()))
    propositions = _propositions(exploitation)
    de_la_fiche = set(contrats_service.valeurs_salarie(TeamMember()))

    blancs, valeurs, libre = {}, {}, {}
    for cle in contrats_service.jetons_utilises(modele.corps):
        # Ce que dit la fiche du salarié attend qu'on en choisisse un ; le
        # reste part de ce que l'exploitation sait déjà.
        valeur = "" if cle in de_la_fiche else (connues.get(cle) or "")
        if cle in _BLANCS_DATE and hasattr(valeur, "isoformat"):
            valeur = valeur.isoformat()  # ce qu'attend <input type="date">
        options = propositions.get(cle, [])
        blancs[cle] = {
            "cle": cle, "libelle": dict(contrats_service.JETONS)[cle],
            "genre": ("date" if cle in _BLANCS_DATE
                      else "nombre" if cle in _BLANCS_NOMBRE else "texte"),
            "options": options,
        }
        valeurs[cle] = str(valeur)
        # Une valeur connue absente de la liste ouvre d'emblée la saisie libre.
        libre[cle] = bool(valeur) and str(valeur) not in options

    segments = [{"texte": valeur} if genre == "texte" else {"blanc": blancs[valeur]}
                for genre, valeur in contrats_service.decouper(modele.corps)]

    # La signature se choisit là où l'employeur signe, c'est-à-dire à sa
    # dernière mention. Si le modèle ne le nomme jamais, elle rejoint la barre
    # du haut plutôt que de disparaître.
    from identite.models import Piece

    rang_signature = max((i for i, seg in enumerate(segments)
                          if seg.get("blanc") and seg["blanc"]["cle"] == "employeur"),
                         default=None)
    if rang_signature is not None:
        segments[rang_signature]["signature"] = True
    signatures = Piece.objects.filter(exploitation=exploitation,
                                      type_piece=Piece.Type.SIGNATURE).exclude(fichier="")
    active = Piece.signature_active(exploitation)

    return render(request, "equipe/etablir.html", {
        "signatures": signatures,
        "signature_active": active.pk if active else "",
        "signature_en_tete": rang_signature is None,
        "modele": modele,
        "membres": membres,
        "segments": segments,
        "etat": json.dumps({
            "valeurs": valeurs, "libre": libre,
            "signature": str(active.pk) if active else "",
            "urls_signatures": {str(p.pk): p.fichier.url for p in signatures},
        }),
        "fiches_membres": json.dumps({
            str(m.pk): {cle: (v.isoformat() if hasattr(v, "isoformat") else v or "")
                        for cle, v in contrats_service.valeurs_salarie(m).items()}
            for m in membres}),
        "page_title": _("Établir un contrat"),
    })


def _signature_choisie(request, exploitation):
    """La signature retenue pour ce contrat, et rien d'autre.

    Un champ vide veut dire « signer à la main » et doit être respecté. Le
    champ absent, lui, vient d'un envoi qui ignore la question : on reprend
    alors la signature par défaut de la ferme, comme avant.
    """
    from identite.models import Piece

    if "signature" not in request.POST:
        return Piece.signature_active(exploitation)
    return Piece.objects.filter(pk=request.POST.get("signature") or 0,
                                exploitation=exploitation,
                                type_piece=Piece.Type.SIGNATURE).first()


@login_required
@espace_requis(EXPLOITANT)
@require_POST
def contrat_create(request):
    """Établit le contrat d'un salarié à partir d'un modèle.

    Le corps est rendu maintenant et figé : retoucher le modèle ensuite ne
    réécrit pas un contrat déjà remis.
    """
    exploitation = _exploitation(request)
    membre = TeamMember.objects.filter(
        pk=request.POST.get("membre") or 0, exploitation=exploitation).first()
    modele = ModeleContrat.objects.filter(
        pk=request.POST.get("modele") or 0, exploitation=exploitation).first()
    if not (exploitation and membre and modele):
        messages.error(request, _("Choisissez un salarié et un modèle."))
        return redirect("equipe:contrats")

    contrat = ContratTravail(exploitation=exploitation, membre=membre, modele=modele,
                             type_contrat=modele.type_contrat, **_champs_contrat(request))
    contrat.signature = _signature_choisie(request, exploitation)
    valeurs = contrats_service.valeurs_pour(contrat)
    valeurs.update(contrats_service.valeurs_saisies(request.POST))
    contrat.corps = contrats_service.remplir(modele.corps, valeurs)
    contrat.save()
    messages.success(request, _("Contrat établi. Relisez-le avant de le remettre."))
    return redirect("equipe:contrats")


@login_required
@espace_requis(EXPLOITANT)
@require_POST
def contrat_edit(request, pk):
    exploitation = _exploitation(request)
    contrat = get_object_or_404(ContratTravail, pk=pk, exploitation=exploitation)
    for champ, valeur in _champs_contrat(request).items():
        setattr(contrat, champ, valeur)

    statut = request.POST.get("statut") or contrat.statut
    if statut in ContratTravail.Statut.values:
        contrat.statut = statut
    contrat.date_signature = _to_date(request.POST.get("date_signature"))
    if request.POST.get("corps") is not None:
        contrat.corps = request.POST.get("corps")
    contrat.save()
    return redirect("equipe:contrats")


@login_required
@espace_requis(EXPLOITANT)
@require_POST
def contrat_delete(request, pk):
    exploitation = _exploitation(request)
    get_object_or_404(ContratTravail, pk=pk, exploitation=exploitation).delete()
    return redirect("equipe:contrats")


@login_required
def modele_pdf(request, pk):
    """Le modèle en PDF, vierge : une feuille à remplir à la main.

    Les jetons deviennent des pointillés — c'est déjà ce que fait `remplir`
    quand une valeur manque, on lui passe simplement un dictionnaire vide.
    """
    exploitation = _exploitation(request)
    modele = ModeleContrat.objects.filter(pk=pk, exploitation=exploitation).first()
    if modele is None:
        messages.info(request, _("Ce modèle n'existe plus."))
        return redirect("equipe:contrats")

    contexte = {"modele": modele, "exploitation": exploitation,
                "corps": contrats_service.remplir(modele.corps, {})}
    html = render(request, "equipe/modele_pdf.html", contexte).content.decode()

    try:
        from weasyprint import HTML
    except Exception:  # noqa: BLE001 — libs système absentes : on rend la page
        return render(request, "equipe/modele_pdf.html", contexte)

    from django.http import HttpResponse

    pdf = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
    reponse = HttpResponse(pdf, content_type="application/pdf")
    reponse["Content-Disposition"] = (
        'inline; filename="%s.pdf"' % slugify(modele.nom or "modele"))
    return reponse


@login_required
@espace_requis(EXPLOITANT)
def contrat_pdf(request, pk):
    """Le contrat en PDF, ou en page imprimable si WeasyPrint n'est pas là."""
    exploitation = _exploitation(request)
    contrat = get_object_or_404(
        ContratTravail.objects.select_related("membre", "signature"), pk=pk, exploitation=exploitation)
    # La signature choisie à l'établissement, figée avec le contrat : en
    # changer dans la section Identité ne resigne pas un contrat déjà remis.
    contexte = {"contrat": contrat, "exploitation": exploitation,
                "signature": contrat.signature}
    html = render(request, "equipe/contrat_pdf.html", contexte).content.decode()

    try:
        from weasyprint import HTML
    except Exception:  # noqa: BLE001 — libs système absentes : on rend la page
        return render(request, "equipe/contrat_pdf.html", contexte)

    from django.http import HttpResponse

    pdf = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
    reponse = HttpResponse(pdf, content_type="application/pdf")
    nom = f"contrat-{contrat.membre.name}-{contrat.pk}.pdf".replace(" ", "-").lower()
    reponse["Content-Disposition"] = f'inline; filename="{nom}"'
    return reponse


# ── Fiches de paie ───────────────────────────────────────────────────


@login_required
@espace_requis(EXPLOITANT)
def paie(request):
    """Les bulletins de paie établis, du plus récent au plus ancien."""
    exploitation = _exploitation(request)
    fiches = list(
        FichePaie.objects.filter(exploitation=exploitation)
        .select_related("membre", "contrat").prefetch_related("lignes")
    ) if exploitation else []
    return render(request, "equipe/paie.html", {
        "fiches": fiches,
        # Les rubriques de chaque fiche, pour rouvrir une fiche à la modifier.
        "lignes_par_fiche": {
            str(f.pk): [
                {"libelle": l.libelle, "base": l.base, "taux": l.taux,
                 "part_salariale": l.part_salariale, "part_patronale": l.part_patronale}
                for l in f.lignes.all()
            ] for f in fiches
        },
        "membres": TeamMember.objects.filter(exploitation=exploitation) if exploitation else [],
        "contrats": (ContratTravail.objects.filter(exploitation=exploitation)
                     .select_related("membre") if exploitation else []),
        "statuts": FichePaie.Statut.choices,
        "page_title": _("Paie"),
    })


def _champs_fiche(request, exploitation):
    """Les champs d'une fiche lus du POST, ou None si l'essentiel manque."""
    membre = TeamMember.objects.filter(
        pk=request.POST.get("membre") or 0, exploitation=exploitation).first()
    debut = _to_date(request.POST.get("periode_debut"))
    fin = _to_date(request.POST.get("periode_fin"))
    if not (membre and debut and fin):
        return None
    if fin < debut:
        debut, fin = fin, debut
    return {
        "membre": membre,
        "contrat": ContratTravail.objects.filter(
            pk=request.POST.get("contrat") or 0, exploitation=exploitation).first(),
        "periode_debut": debut,
        "periode_fin": fin,
        "heures_travaillees": _to_float(request.POST.get("heures_travaillees")),
        "heures_supplementaires": _to_float(request.POST.get("heures_supplementaires")),
        "salaire_brut": _to_float(request.POST.get("salaire_brut"), 0) or 0,
        "cotisations_salariales": _to_float(request.POST.get("cotisations_salariales"), 0) or 0,
        "cotisations_patronales": _to_float(request.POST.get("cotisations_patronales"), 0) or 0,
        "net_imposable": _to_float(request.POST.get("net_imposable")),
        "net_a_payer": _to_float(request.POST.get("net_a_payer"), 0) or 0,
        "date_paiement": _to_date(request.POST.get("date_paiement")),
        "mode_paiement": (request.POST.get("mode_paiement") or "").strip()[:60],
        "notes": (request.POST.get("notes") or "").strip(),
    }


def _enregistrer_lignes(fiche, request):
    """Synchronise les rubriques depuis le JSON du champ caché `lignes`."""
    import json

    try:
        rows = json.loads(request.POST.get("lignes") or "[]")
    except json.JSONDecodeError:
        return
    if not isinstance(rows, list):
        return

    fiche.lignes.all().delete()
    LignePaie.objects.bulk_create([
        LignePaie(fiche=fiche, ordre=i,
                  libelle=str(row.get("libelle") or "").strip()[:255],
                  base=_to_float(row.get("base")),
                  taux=_to_float(row.get("taux")),
                  part_salariale=_to_float(row.get("part_salariale")),
                  part_patronale=_to_float(row.get("part_patronale")))
        for i, row in enumerate(rows)
        if isinstance(row, dict) and str(row.get("libelle") or "").strip()
    ])


@login_required
@espace_requis(EXPLOITANT)
@require_POST
def fiche_save(request, pk=None):
    exploitation = _exploitation(request)
    if exploitation is None:
        messages.error(request, _("Créez d'abord votre exploitation."))
        return redirect("equipe:paie")

    champs = _champs_fiche(request, exploitation)
    if champs is None:
        messages.error(request, _("Une fiche de paie a besoin d'un salarié et d'une période."))
        return redirect("equipe:paie")

    fiche = (get_object_or_404(FichePaie, pk=pk, exploitation=exploitation)
             if pk else FichePaie(exploitation=exploitation))
    for champ, valeur in champs.items():
        setattr(fiche, champ, valeur)
    statut = request.POST.get("statut")
    if statut in FichePaie.Statut.values:
        fiche.statut = statut

    from django.db import IntegrityError, transaction

    # Le point de reprise est indispensable : une IntegrityError non isolée
    # laisse la transaction cassée, et plus aucune requête ne passe ensuite.
    try:
        with transaction.atomic():
            fiche.save()
    except IntegrityError:
        messages.error(request, _("Ce salarié a déjà une fiche pour cette période."))
        return redirect("equipe:paie")

    _enregistrer_lignes(fiche, request)
    if not fiche.addition_coherente:
        messages.warning(request, _(
            "Les rubriques ne totalisent pas les montants annoncés : %(lig_s)s € "
            "et %(lig_p)s € contre %(fic_s)s € et %(fic_p)s €.") % {
                "lig_s": fiche.total_lignes_salariales, "lig_p": fiche.total_lignes_patronales,
                "fic_s": fiche.cotisations_salariales, "fic_p": fiche.cotisations_patronales})
    return redirect("equipe:paie")


@login_required
@espace_requis(EXPLOITANT)
@require_POST
def fiche_statut(request, pk):
    exploitation = _exploitation(request)
    fiche = get_object_or_404(FichePaie, pk=pk, exploitation=exploitation)
    statut = request.POST.get("statut")
    if statut in FichePaie.Statut.values:
        fiche.statut = statut
        fiche.save(update_fields=["statut", "updated_at"])
    return redirect("equipe:paie")


@login_required
@espace_requis(EXPLOITANT)
@require_POST
def fiche_delete(request, pk):
    exploitation = _exploitation(request)
    get_object_or_404(FichePaie, pk=pk, exploitation=exploitation).delete()
    return redirect("equipe:paie")


@login_required
@espace_requis(EXPLOITANT)
def fiche_pdf(request, pk):
    """Le bulletin en PDF, ou en page imprimable si WeasyPrint n'est pas là."""
    exploitation = _exploitation(request)
    fiche = get_object_or_404(
        FichePaie.objects.select_related("membre", "contrat").prefetch_related("lignes"),
        pk=pk, exploitation=exploitation)
    contexte = {"fiche": fiche, "exploitation": exploitation}
    html = render(request, "equipe/fiche_paie_pdf.html", contexte).content.decode()

    try:
        from weasyprint import HTML
    except Exception:  # noqa: BLE001 — libs système absentes : on rend la page
        return render(request, "equipe/fiche_paie_pdf.html", contexte)

    from django.http import HttpResponse

    pdf = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
    reponse = HttpResponse(pdf, content_type="application/pdf")
    nom = f"paie-{fiche.membre.name}-{fiche.periode_debut:%Y-%m}.pdf".replace(" ", "-").lower()
    reponse["Content-Disposition"] = f'inline; filename="{nom}"'
    return reponse


# ── Offres d'emploi : back-office ────────────────────────────────────


def _champs_offre(request):
    type_contrat = request.POST.get("type_contrat") or ModeleContrat.Type.SAISONNIER
    return {
        "titre": (request.POST.get("titre") or "").strip()[:255],
        "type_contrat": (type_contrat if type_contrat in ModeleContrat.Type.values
                         else ModeleContrat.Type.SAISONNIER),
        "description": (request.POST.get("description") or "").strip(),
        "profil": (request.POST.get("profil") or "").strip(),
        "lieu": (request.POST.get("lieu") or "").strip()[:255],
        "date_debut": _to_date(request.POST.get("date_debut")),
        "duree_hebdo": _to_float(request.POST.get("duree_hebdo")),
        "remuneration": (request.POST.get("remuneration") or "").strip()[:255],
        "logement": request.POST.get("logement") == "on",
        "expire_le": _to_date(request.POST.get("expire_le")),
    }


def _communes(exploitation):
    """Les communes où l'exploitation a des parcelles, sans doublon."""
    if exploitation is None:
        return []
    return sorted({
        c.strip() for c in Parcelle.objects
        .filter(exploitation=exploitation)
        .exclude(commune="")
        .values_list("commune", flat=True) if c.strip()
    })


@login_required
@espace_requis(EXPLOITANT)
def offres(request):
    """Les offres de l'exploitation et les candidatures reçues."""
    exploitation = _exploitation(request)
    lot = (OffreEmploi.objects.filter(exploitation=exploitation)
           .prefetch_related("candidatures") if exploitation else [])
    return render(request, "equipe/offres.html", {
        "offres": lot,
        # Le lieu de travail se choisit parmi les communes des parcelles : une
        # offre situe le poste là où la ferme travaille, pas ailleurs.
        "communes": _communes(exploitation),
        "types": ModeleContrat.Type.choices,
        "statuts": OffreEmploi.Statut.choices,
        "statuts_candidature": Candidature.Statut.choices,
        "banque_postes": postes_service.suggestions(exploitation),
        "page_title": _("Offres d'emploi"),
    })


@login_required
@espace_requis(EXPLOITANT)
@require_POST
def offre_save(request, pk=None):
    exploitation = _exploitation(request)
    if exploitation is None:
        messages.error(request, _("Créez d'abord votre exploitation."))
        return redirect("equipe:offres")

    offre = (get_object_or_404(OffreEmploi, pk=pk, exploitation=exploitation)
             if pk else OffreEmploi(exploitation=exploitation))
    champs = _champs_offre(request)
    if not (champs["titre"] and champs["description"]):
        messages.error(request, _("Une offre a besoin d'un intitulé et d'une description."))
        return redirect("equipe:offres")

    for champ, valeur in champs.items():
        setattr(offre, champ, valeur)
    offre.save()
    return redirect("equipe:offres")


@login_required
@espace_requis(EXPLOITANT)
@require_POST
def offre_statut(request, pk):
    """Publie, dépublie, ou clôt une offre."""
    exploitation = _exploitation(request)
    offre = get_object_or_404(OffreEmploi, pk=pk, exploitation=exploitation)
    statut = request.POST.get("statut")
    if statut in OffreEmploi.Statut.values:
        offre.statut = statut
        # La date de publication se pose à la première mise en ligne et ne
        # bouge plus : elle ordonne la liste publique.
        if statut == OffreEmploi.Statut.PUBLIEE and offre.publiee_le is None:
            offre.publiee_le = timezone.now()
        offre.save(update_fields=["statut", "publiee_le", "updated_at"])
    return redirect("equipe:offres")


@login_required
@espace_requis(EXPLOITANT)
@require_POST
def offre_delete(request, pk):
    exploitation = _exploitation(request)
    get_object_or_404(OffreEmploi, pk=pk, exploitation=exploitation).delete()
    return redirect("equipe:offres")


@login_required
@espace_requis(EXPLOITANT)
def postes(request):
    """La banque des postes de l'exploitation, et combien chacun sert."""
    exploitation = _exploitation(request)
    banque = list(Poste.objects.filter(exploitation=exploitation)) if exploitation else []
    # Un poste « sert » quand un membre ou une offre porte son intitulé.
    usages = {}
    if exploitation:
        for intitule in (list(TeamMember.objects.filter(exploitation=exploitation)
                              .exclude(poste="").values_list("poste", flat=True))
                         + list(OffreEmploi.objects.filter(exploitation=exploitation)
                                .values_list("titre", flat=True))):
            usages[intitule.casefold()] = usages.get(intitule.casefold(), 0) + 1
    for poste in banque:
        poste.usages = usages.get(poste.intitule.casefold(), 0)
    connus = {p.intitule.casefold() for p in banque}
    return render(request, "equipe/postes.html", {
        "postes": banque,
        "courants_manquants": sum(1 for p in postes_service.courants()
                                  if p["intitule"].casefold() not in connus),
        "page_title": _("Banque des postes"),
    })


@login_required
@espace_requis(EXPLOITANT)
@require_POST
def poste_save(request, pk=None):
    exploitation = _exploitation(request)
    if exploitation is None:
        messages.error(request, _("Créez d'abord votre exploitation."))
        return redirect("equipe:postes")

    poste = (get_object_or_404(Poste, pk=pk, exploitation=exploitation)
             if pk else Poste(exploitation=exploitation))
    intitule = (request.POST.get("intitule") or "").strip()[:255]
    if not intitule:
        messages.error(request, _("Un poste a besoin d'un intitulé."))
        return redirect("equipe:postes")
    doublon = (Poste.objects.filter(exploitation=exploitation, intitule__iexact=intitule)
               .exclude(pk=poste.pk).exists())
    if doublon:
        messages.error(request, _("« %(intitule)s » est déjà dans la banque.") % {"intitule": intitule})
        return redirect("equipe:postes")

    poste.intitule = intitule
    poste.qualification = (request.POST.get("qualification") or "").strip()[:255]
    poste.missions = (request.POST.get("missions") or "").strip()
    poste.profil = (request.POST.get("profil") or "").strip()
    poste.save()
    return redirect("equipe:postes")


@login_required
@espace_requis(EXPLOITANT)
@require_POST
def poste_delete(request, pk):
    """Retire un poste de la banque. Les fiches qui le portent gardent leur intitulé."""
    exploitation = _exploitation(request)
    get_object_or_404(Poste, pk=pk, exploitation=exploitation).delete()
    return redirect("equipe:postes")


@login_required
@espace_requis(EXPLOITANT)
@require_POST
def postes_importer(request):
    """Copie dans la banque les postes courants qu'elle n'a pas encore."""
    exploitation = _exploitation(request)
    if exploitation is None:
        messages.error(request, _("Créez d'abord votre exploitation."))
        return redirect("equipe:postes")

    connus = {i.casefold() for i in Poste.objects.filter(exploitation=exploitation)
              .values_list("intitule", flat=True)}
    nouveaux = [Poste(exploitation=exploitation, intitule=p["intitule"], missions=p["missions"],
                      profil=p["profil"])
                for p in postes_service.courants() if p["intitule"].casefold() not in connus]
    Poste.objects.bulk_create(nouveaux, ignore_conflicts=True)
    messages.success(request, _("%(n)s poste(s) ajouté(s) à la banque.") % {"n": len(nouveaux)})
    return redirect("equipe:postes")


@login_required
@espace_requis(EXPLOITANT)
@require_POST
def candidature_statut(request, pk):
    exploitation = _exploitation(request)
    candidature = get_object_or_404(
        Candidature, pk=pk, offre__exploitation=exploitation)
    statut = request.POST.get("statut")
    if statut in Candidature.Statut.values:
        candidature.statut = statut
        candidature.save(update_fields=["statut", "updated_at"])
    return redirect("equipe:offres")
