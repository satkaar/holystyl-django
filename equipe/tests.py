"""Tests équipe : API tâches/membres, SMS d'affectation, rappels Celery, lien géoloc."""

import html
import json
import sys
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from equipe import invitations, services, tasks as celery_tasks
from equipe.models import Task, TeamMember
from exploitations.models import Exploitation

User = get_user_model()


@pytest.fixture
def setup(db):
    user = User.objects.create_user(email="eq@ex.com", password="pwd12345")
    exploitation = Exploitation.objects.create(owner=user, name="Ferme Eq")
    member = TeamMember.objects.create(exploitation=exploitation, name="Aude", phone="+33600000000", email="aude@ex.com")
    return user, exploitation, member


@pytest.mark.django_db
def test_create_task_sends_sms_on_assignment(client, setup, monkeypatch):
    user, exploitation, member = setup
    calls = {}
    monkeypatch.setattr("equipe.services.send_sms", lambda to, body: calls.update(to=to, body=body) or True)
    client.force_login(user)
    resp = client.post(
        "/api/tasks/",
        {"title": "Tailler parcelle nord", "assigned_to": member.id, "priority": "haute"},
        content_type="application/json",
    )
    assert resp.status_code == 201
    assert calls.get("to") == "+33600000000"
    assert "Tailler" in calls.get("body", "")


@pytest.mark.django_db
def test_task_api_tenant_scoped(client, setup):
    user, exploitation, member = setup
    Task.objects.create(exploitation=exploitation, title="Mine")
    other = User.objects.create_user(email="o@ex.com", password="pwd12345")
    other_exp = Exploitation.objects.create(owner=other, name="Autre")
    Task.objects.create(exploitation=other_exp, title="Theirs")
    client.force_login(user)
    titles = {t["title"] for t in client.get("/api/tasks/").json()}
    assert titles == {"Mine"}


@pytest.mark.django_db
def test_add_member_via_form(client, setup):
    user, exploitation, _ = setup
    client.force_login(user)
    resp = client.post(
        "/equipe/",
        {
            "first_name": "Jean",
            "last_name": "Dupont",
            "email": "jean@exemple.fr",
            "phone": "+33 6 12 34 56 78",
            "role": "ouvrier",
        },
    )
    assert resp.status_code == 302
    m = TeamMember.objects.get(email="jean@exemple.fr")
    assert m.exploitation == exploitation and m.managed_by == user
    assert m.name == "Jean Dupont" and m.role == "ouvrier"


@pytest.mark.django_db
def test_edit_member(client, setup):
    user, exploitation, member = setup  # member: Aude, ouvrier par défaut
    client.force_login(user)
    resp = client.post(
        f"/equipe/{member.id}/modifier/",
        {
            "first_name": "Aude",
            "last_name": "Lemaire",
            "email": "aude@ex.com",
            "phone": "+33611111111",
            "role": "chef",
        },
    )
    assert resp.status_code == 302
    member.refresh_from_db()
    assert member.name == "Aude Lemaire"
    assert member.role == "chef"


@pytest.mark.django_db
def test_edit_member_scoped_to_exploitation(client, setup):
    user, _, _ = setup
    other = User.objects.create_user(email="other@ex.com", password="pwd12345")
    other_exp = Exploitation.objects.create(owner=other, name="Autre")
    foreign = TeamMember.objects.create(exploitation=other_exp, name="Pas à moi")
    client.force_login(user)
    assert client.get(f"/equipe/{foreign.id}/modifier/").status_code == 404


@pytest.mark.django_db
def test_location_link_generated(setup):
    _, _, member = setup
    token = services.generate_location_link(member)
    member.refresh_from_db()
    assert member.location_token == token
    assert member.location_token_expires_at > timezone.now()


@pytest.mark.django_db
def test_reminder_job_sets_flag_and_notifies(setup, monkeypatch):
    user, exploitation, member = setup
    sms = []
    monkeypatch.setattr("equipe.tasks.send_sms", lambda to, body: sms.append(to) or True)
    monkeypatch.setattr("equipe.tasks.send_mail", lambda *a, **k: 1)
    task = Task.objects.create(
        exploitation=exploitation, title="Arrosage", assigned_to=member,
        due_date=timezone.now() + timedelta(hours=24),
    )
    celery_tasks.check_task_reminders()
    task.refresh_from_db()
    assert task.reminder_sent_24h is True
    assert sms == ["+33600000000"]


# ── Invitation à ouvrir un espace employé ────────────────────────────────────


@pytest.mark.django_db
def test_invitation_envoyee_et_horodatee(client, setup, mailoutbox):
    user, _expl, member = setup
    client.force_login(user)
    r = client.post(f"/equipe/{member.id}/inviter/")
    assert r.status_code == 302
    member.refresh_from_db()
    assert member.invitation_sent_at is not None
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == ["aude@ex.com"]
    assert invitations.jeton(member) in mailoutbox[0].body


@pytest.mark.django_db
def test_fiche_membre_propose_l_invitation(client, setup):
    """La carte « Espace employé » suit l'état du membre : inviter → renvoyer → lié."""
    user, _expl, member = setup
    client.force_login(user)
    url = f"/equipe/{member.id}/modifier/"

    html = client.get(url).content.decode()
    assert "Inviter par email" in html
    assert f"/equipe/{member.id}/inviter/" in html

    client.post(f"/equipe/{member.id}/inviter/")
    assert "Renvoyer l'invitation" in client.get(url).content.decode()

    member.refresh_from_db()
    member.user = User.objects.create_user(email="lie@ex.com", password="pwd12345")
    member.save(update_fields=["user"])
    html = client.get(url).content.decode()
    assert "Compte actif" in html
    assert "Inviter par email" not in html


@pytest.mark.django_db
def test_invitation_refusee_sans_email(client, setup):
    user, exploitation, _m = setup
    sans_email = TeamMember.objects.create(exploitation=exploitation, name="Sans mail")
    client.force_login(user)
    client.post(f"/equipe/{sans_email.id}/inviter/")
    sans_email.refresh_from_db()
    assert sans_email.invitation_sent_at is None


@pytest.mark.django_db
def test_invitation_scoped_to_exploitation(client, setup, mailoutbox):
    """Un autre exploitant ne peut pas inviter les membres d'une équipe voisine."""
    _user, _expl, member = setup
    intrus = User.objects.create_user(email="intrus@ex.com", password="pwd12345")
    Exploitation.objects.create(owner=intrus, name="Ferme voisine")
    client.force_login(intrus)
    assert client.post(f"/equipe/{member.id}/inviter/").status_code == 404
    assert mailoutbox == []


@pytest.mark.django_db
def test_acceptation_cree_le_compte_et_ouvre_l_espace(client, setup):
    _user, _expl, member = setup
    url = f"/equipe/invitation/{invitations.jeton(member)}/"
    assert client.get(url).status_code == 200

    r = client.post(url, {"first_name": "Aude", "last_name": "Martin",
                          "password1": "Tr0ubadour!42", "password2": "Tr0ubadour!42"})
    assert r.status_code == 302
    member.refresh_from_db()
    assert member.user is not None
    assert member.user.email == "aude@ex.com"
    assert member.invitation_accepted_at is not None
    # L'espace employé est ouvert et actif, pas seulement disponible.
    assert client.session["espace"] == "employe"


@pytest.mark.django_db
def test_acceptation_par_un_compte_connecte(client, setup):
    _user, _expl, member = setup
    autre = User.objects.create_user(email="aude@ex.com", password="pwd12345")
    client.force_login(autre)
    r = client.post(f"/equipe/invitation/{invitations.jeton(member)}/")
    assert r.status_code == 302
    member.refresh_from_db()
    assert member.user == autre


@pytest.mark.django_db
def test_jeton_perime_si_l_email_change(setup):
    """Le lien visait une personne : le réattribuer serait une fuite d'accès."""
    _user, _expl, member = setup
    token = invitations.jeton(member)
    assert invitations.membre_du_jeton(token) == member
    member.email = "quelquun.dautre@ex.com"
    member.save(update_fields=["email"])
    assert invitations.membre_du_jeton(token) is None


@pytest.mark.django_db
def test_jeton_bidon_repond_410(client, setup):
    assert client.get("/equipe/invitation/nimportequoi/").status_code == 410


@pytest.mark.django_db
def test_invitation_deja_utilisee(client, setup):
    _user, _expl, member = setup
    token = invitations.jeton(member)
    member.user = User.objects.create_user(email="deja@ex.com", password="pwd12345")
    member.save(update_fields=["user"])
    assert client.get(f"/equipe/invitation/{token}/").status_code == 410


# ── Contrats de travail : modèles et contrats nominatifs ─────────────


@pytest.fixture
def ferme_rh(db):
    from equipe.models import TeamMember
    from exploitations.models import Exploitation

    patron = User.objects.create_user(email="rh@ex.com", password="pwd12345", full_name="Jean Dupont")
    exploitation = Exploitation.objects.create(owner=patron, name="Ferme des Coteaux",
                                               siret="12345678900012")
    membre = TeamMember.objects.create(exploitation=exploitation, name="Paul Martin",
                                       email="paul@ex.com", phone="0600000000")
    return patron, exploitation, membre


@pytest.mark.django_db
def test_importer_les_modeles_types(client, ferme_rh):
    from equipe.contrats import SQUELETTES
    from equipe.models import ModeleContrat

    patron, exploitation, _membre = ferme_rh
    client.force_login(patron)
    assert client.post("/contrats-travail/modeles/importer/").status_code == 302
    assert ModeleContrat.objects.filter(exploitation=exploitation).count() == len(SQUELETTES)

    # Réimporter n'ajoute pas de doublon.
    client.post("/contrats-travail/modeles/importer/")
    assert ModeleContrat.objects.filter(exploitation=exploitation).count() == len(SQUELETTES)


@pytest.mark.django_db
def test_etablir_un_contrat_remplit_les_jetons(client, ferme_rh):
    from equipe.models import ContratTravail, ModeleContrat

    patron, exploitation, membre = ferme_rh
    modele = ModeleContrat.objects.create(
        exploitation=exploitation, nom="CDI maison", type_contrat="cdi",
        corps="{{ salarie }} est engagé par {{ exploitation }} (SIRET {{ exploitation_siret }}) "
              "au poste de {{ poste }} à compter du {{ date_debut }}. "
              "Employeur : {{ employeur }}. Fin : {{ date_fin }}.")

    client.force_login(patron)
    assert client.post("/contrats-travail/etablir/", {
        "membre": str(membre.pk), "modele": str(modele.pk), "poste": "Ouvrier arboricole",
        "date_debut": "2026-09-01", "duree_hebdo": "35", "remuneration": "1 850,50",
    }).status_code == 302

    contrat = ContratTravail.objects.get(membre=membre)
    assert contrat.type_contrat == "cdi" and contrat.remuneration == 1850.50
    assert "Paul Martin est engagé par Ferme des Coteaux" in contrat.corps
    assert "SIRET 12345678900012" in contrat.corps
    assert "au poste de Ouvrier arboricole" in contrat.corps
    assert "Jean Dupont" in contrat.corps
    # La date de fin n'est pas renseignée : le contrat montre où compléter.
    assert "Fin : ……………………" in contrat.corps
    # Aucun jeton ne subsiste.
    assert "{{" not in contrat.corps


@pytest.mark.django_db
def test_le_contrat_est_fige_a_l_etablissement(client, ferme_rh):
    """Retoucher le modèle ne réécrit pas un contrat déjà remis."""
    from equipe.models import ContratTravail, ModeleContrat

    patron, exploitation, membre = ferme_rh
    modele = ModeleContrat.objects.create(
        exploitation=exploitation, nom="CDI", corps="Version initiale pour {{ salarie }}.")
    client.force_login(patron)
    client.post("/contrats-travail/etablir/", {
        "membre": str(membre.pk), "modele": str(modele.pk), "date_debut": "2026-09-01"})

    client.post(f"/contrats-travail/modeles/{modele.pk}/enregistrer/", {
        "nom": "CDI", "corps": "Version refondue pour {{ salarie }}."})

    contrat = ContratTravail.objects.get(membre=membre)
    assert "Version initiale" in contrat.corps


@pytest.mark.django_db
def test_le_contrat_d_une_autre_ferme_est_hors_de_portee(client, ferme_rh):
    from equipe.models import ContratTravail, ModeleContrat, TeamMember
    from exploitations.models import Exploitation

    patron, _exploitation, _membre = ferme_rh
    voisin = User.objects.create_user(email="voisin-rh@ex.com", password="pwd12345")
    ferme_voisine = Exploitation.objects.create(owner=voisin, name="Ferme voisine")
    son_membre = TeamMember.objects.create(exploitation=ferme_voisine, name="Luc")
    son_modele = ModeleContrat.objects.create(
        exploitation=ferme_voisine, nom="CDI", corps="…")
    son_contrat = ContratTravail.objects.create(
        exploitation=ferme_voisine, membre=son_membre, corps="Confidentiel")

    client.force_login(patron)
    # Ni établir avec ses éléments…
    client.post("/contrats-travail/etablir/", {
        "membre": str(son_membre.pk), "modele": str(son_modele.pk)})
    assert ContratTravail.objects.filter(membre=son_membre).count() == 1
    # …ni ouvrir ou supprimer le sien.
    assert client.get(f"/contrats-travail/{son_contrat.pk}/pdf/").status_code == 404
    assert client.post(f"/contrats-travail/{son_contrat.pk}/supprimer/").status_code == 404


@pytest.mark.django_db
def test_la_page_affiche_l_avertissement_et_les_jetons(client, ferme_rh):
    patron, _exploitation, _membre = ferme_rh
    client.force_login(patron)
    html = client.get("/contrats-travail/").content.decode()
    assert "Faites relire un contrat avant de le remettre." in html
    # Les jetons s'affichent en clair, sans être interprétés par Django.
    assert "{{ salarie }}" in html
    assert "Importer les modèles types" in html


# ── Offres d'emploi : back-office et espace public ───────────────────


@pytest.fixture
def offre_publiee(ferme_rh):
    from equipe.models import OffreEmploi

    _patron, exploitation, _membre = ferme_rh
    return OffreEmploi.objects.create(
        exploitation=exploitation, titre="Ouvrier arboricole pour la récolte",
        type_contrat="saisonnier", description="Récolte des abricots, 6 semaines.",
        lieu="Carpentras", statut=OffreEmploi.Statut.PUBLIEE,
        publiee_le=timezone.now())


@pytest.mark.django_db
def test_l_offre_recoit_une_adresse_publique_stable(ferme_rh):
    from equipe.models import OffreEmploi

    _patron, exploitation, _membre = ferme_rh
    o = OffreEmploi.objects.create(exploitation=exploitation, titre="Tractoriste",
                                   description="…")
    assert o.slug == "tractoriste-ferme-des-coteaux"

    # Deux offres du même nom ne se marchent pas dessus.
    o2 = OffreEmploi.objects.create(exploitation=exploitation, titre="Tractoriste",
                                    description="…")
    assert o2.slug == "tractoriste-ferme-des-coteaux-2"

    # Le titre change, l'adresse déjà partagée reste.
    o.titre = "Tractoriste expérimenté"
    o.save()
    assert o.slug == "tractoriste-ferme-des-coteaux"


@pytest.mark.django_db
def test_la_page_publique_est_ouverte_a_tous(client, offre_publiee):
    """Ni compte ni connexion : c'est le principe de la page emplois."""
    liste = client.get("/emplois/")
    assert liste.status_code == 200
    assert "Ouvrier arboricole pour la récolte" in liste.content.decode()

    detail = client.get(f"/emplois/{offre_publiee.slug}/")
    assert detail.status_code == 200
    corps = detail.content.decode()
    assert "Ferme des Coteaux" in corps and "Postuler" in corps
    # Le bandeau de vitrine, pas l'ossature de l'application.
    assert 'class="lp-header"' in corps and "Tableau de bord" not in corps
    # Un {# … #} à cheval sur deux lignes s'afficherait en clair.
    assert "{#" not in corps and "{{" not in corps


@pytest.mark.django_db
def test_une_offre_en_brouillon_ne_fuit_pas(client, ferme_rh):
    from equipe.models import OffreEmploi

    _patron, exploitation, _membre = ferme_rh
    brouillon = OffreEmploi.objects.create(
        exploitation=exploitation, titre="Poste secret", description="…")
    assert "Poste secret" not in client.get("/emplois/").content.decode()
    # Le lien direct ne l'ouvre pas davantage.
    assert client.get(f"/emplois/{brouillon.slug}/").status_code == 410


@pytest.mark.django_db
def test_une_offre_expiree_sort_de_la_liste(client, offre_publiee):
    from datetime import timedelta

    offre_publiee.expire_le = timezone.localdate() - timedelta(days=1)
    offre_publiee.save()
    assert "Ouvrier arboricole" not in client.get("/emplois/").content.decode()
    assert client.get(f"/emplois/{offre_publiee.slug}/").status_code == 410


@pytest.mark.django_db
def test_candidater_sans_compte(client, offre_publiee):
    from equipe.models import Candidature
    from notifications.models import Notification

    resp = client.post(f"/emplois/{offre_publiee.slug}/candidater/", {
        "nom": "Marie Durand", "email": "marie@exemple.fr",
        "telephone": "0611223344", "message": "Disponible dès juillet."})
    assert resp.status_code == 302

    c = Candidature.objects.get(offre=offre_publiee)
    assert c.nom == "Marie Durand" and c.statut == Candidature.Statut.RECUE
    # La ferme est prévenue.
    assert Notification.objects.filter(
        user=offre_publiee.exploitation.owner, type="candidature").exists()


@pytest.mark.django_db
def test_une_candidature_sans_nom_ou_email_est_refusee(client, offre_publiee):
    from equipe.models import Candidature

    client.post(f"/emplois/{offre_publiee.slug}/candidater/", {"nom": "Anonyme"})
    client.post(f"/emplois/{offre_publiee.slug}/candidater/", {"email": "a@b.fr"})
    assert Candidature.objects.count() == 0


@pytest.mark.django_db
def test_le_cv_est_filtre_sur_le_format_et_la_taille(client, offre_publiee):
    """Seul endroit où un anonyme dépose un fichier : on le tient serré."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from equipe.emplois import TAILLE_MAX_CV
    from equipe.models import Candidature

    champs = {"nom": "Marie", "email": "marie@exemple.fr"}

    # Extension refusée.
    client.post(f"/emplois/{offre_publiee.slug}/candidater/", {
        **champs, "cv": SimpleUploadedFile("cv.exe", b"MZ", content_type="application/octet-stream")})
    assert Candidature.objects.count() == 0

    # Trop volumineux.
    client.post(f"/emplois/{offre_publiee.slug}/candidater/", {
        **champs, "cv": SimpleUploadedFile("cv.pdf", b"x" * (TAILLE_MAX_CV + 1),
                                           content_type="application/pdf")})
    assert Candidature.objects.count() == 0

    # Un PDF de taille raisonnable passe.
    client.post(f"/emplois/{offre_publiee.slug}/candidater/", {
        **champs, "cv": SimpleUploadedFile("cv.pdf", b"%PDF-1.4 ...", content_type="application/pdf")})
    assert Candidature.objects.count() == 1


@pytest.mark.django_db
def test_publier_pose_la_date_une_seule_fois(client, ferme_rh):
    from equipe.models import OffreEmploi

    patron, exploitation, _membre = ferme_rh
    offre = OffreEmploi.objects.create(exploitation=exploitation, titre="Poste", description="…")
    client.force_login(patron)

    client.post(f"/offres-emploi/{offre.pk}/statut/", {"statut": "publiee"})
    offre.refresh_from_db()
    premiere = offre.publiee_le
    assert premiere is not None

    # On dépublie puis on republie : la date de première mise en ligne tient.
    client.post(f"/offres-emploi/{offre.pk}/statut/", {"statut": "close"})
    client.post(f"/offres-emploi/{offre.pk}/statut/", {"statut": "publiee"})
    offre.refresh_from_db()
    assert offre.publiee_le == premiere


@pytest.mark.django_db
def test_les_candidatures_du_voisin_sont_hors_de_portee(client, ferme_rh, offre_publiee):
    from equipe.models import Candidature
    from exploitations.models import Exploitation

    patron, _exploitation, _membre = ferme_rh
    candidature = Candidature.objects.create(
        offre=offre_publiee, nom="Marie", email="marie@exemple.fr")

    voisin = User.objects.create_user(email="voisin-offres@ex.com", password="pwd12345")
    Exploitation.objects.create(owner=voisin, name="Ferme voisine")
    client.force_login(voisin)
    assert client.post(f"/candidatures/{candidature.pk}/statut/",
                       {"statut": "retenue"}).status_code == 404
    candidature.refresh_from_db()
    assert candidature.statut == Candidature.Statut.RECUE


@pytest.mark.django_db
def test_la_vitrine_reste_une_vitrine_meme_connecte(client, ferme_rh, offre_publiee):
    """Un agriculteur connecté voit la même page qu'un candidat, sans son tableau de bord."""
    patron, _exploitation, _membre = ferme_rh
    client.force_login(patron)

    for url in ("/emplois/", f"/emplois/{offre_publiee.slug}/"):
        corps = client.get(url).content.decode()
        assert 'class="lp-header"' in corps            # le bandeau de vitrine…
        assert "Tableau de bord" not in corps          # …et pas la barre latérale
        assert "Contrats de travail" not in corps

    # Une offre retirée aussi.
    offre_publiee.statut = "close"
    offre_publiee.save()
    ferme = client.get(f"/emplois/{offre_publiee.slug}/")
    assert ferme.status_code == 410
    assert "Tableau de bord" not in ferme.content.decode()


@pytest.mark.django_db
def test_les_champs_redactionnels_offrent_la_reformulation_ia(client, ferme_rh):
    """Intitulé, description et profil : les trois champs qu'on rédige."""
    patron, _exploitation, _membre = ferme_rh
    client.force_login(patron)
    html = client.get("/offres-emploi/").content.decode()

    for cible in ("o-titre", "o-desc", "o-profil"):
        assert f"hsRewrite(this, '{cible}'" in html
    assert html.count("/assistant/reformuler/") == 3
    # Le bouton reste inactif tant que le champ est vide.
    assert ":disabled=\"!(offre.titre || '').trim()\"" in html


@pytest.mark.django_db
def test_le_lieu_se_choisit_parmi_les_communes_des_parcelles(client, ferme_rh):
    from parcelles.models import Parcelle

    patron, exploitation, _membre = ferme_rh
    Parcelle.objects.create(exploitation=exploitation, name="Le Clos", commune="Carpentras")
    Parcelle.objects.create(exploitation=exploitation, name="Les Hauts", commune="Mazan")
    Parcelle.objects.create(exploitation=exploitation, name="Le Bas", commune="Carpentras")
    Parcelle.objects.create(exploitation=exploitation, name="Sans commune", commune="")

    client.force_login(patron)
    resp = client.get("/offres-emploi/")
    # Sans doublon, sans vide, et dans l'ordre.
    assert resp.context["communes"] == ["Carpentras", "Mazan"]

    html = resp.content.decode()
    assert '<select id="o-lieu" name="lieu"' in html
    assert "<option value=\"Carpentras\">Carpentras</option>" in html
    assert "{#" not in html and "{{" not in html


@pytest.mark.django_db
def test_sans_commune_renseignee_on_renvoie_vers_les_parcelles(client, ferme_rh):
    patron, _exploitation, _membre = ferme_rh
    client.force_login(patron)
    resp = client.get("/offres-emploi/")
    assert resp.context["communes"] == []
    assert "Aucune commune renseignée sur vos" in resp.content.decode()


@pytest.mark.django_db
def test_les_communes_du_voisin_ne_sont_pas_proposees(client, ferme_rh):
    from exploitations.models import Exploitation
    from parcelles.models import Parcelle

    patron, exploitation, _membre = ferme_rh
    Parcelle.objects.create(exploitation=exploitation, name="Chez moi", commune="Carpentras")
    voisin = User.objects.create_user(email="voisin-communes@ex.com", password="pwd12345")
    ferme_voisine = Exploitation.objects.create(owner=voisin, name="Ferme voisine")
    Parcelle.objects.create(exploitation=ferme_voisine, name="Chez lui", commune="Avignon")

    client.force_login(patron)
    assert client.get("/offres-emploi/").context["communes"] == ["Carpentras"]


# ── /taches/ et la modale du planning créent la même tâche ───────────


@pytest.mark.django_db
def test_le_formulaire_taches_propose_parcelles_et_sous_taches(client, ferme_rh):
    from parcelles.models import Parcelle

    patron, exploitation, _membre = ferme_rh
    Parcelle.objects.create(exploitation=exploitation, name="Le Clos", commune="Carpentras")
    client.force_login(patron)
    html = client.get("/taches/").content.decode()

    # Les deux blocs partagés avec la modale du planning.
    assert "Parcelles" in html and "Le Clos" in html
    assert "Sous-tâches" in html and 'name="subtasks"' in html
    assert "{#" not in html and "{{" not in html


@pytest.mark.django_db
def test_une_tache_creee_depuis_taches_porte_parcelles_et_filles(client, ferme_rh):
    import json

    from equipe.models import Task, TeamMember
    from parcelles.models import Parcelle

    patron, exploitation, _membre = ferme_rh
    membre = TeamMember.objects.create(exploitation=exploitation, name="Paul")
    clos = Parcelle.objects.create(exploitation=exploitation, name="Le Clos")
    hauts = Parcelle.objects.create(exploitation=exploitation, name="Les Hauts")

    client.force_login(patron)
    resp = client.post("/taches/", {
        "title": "Taille d'hiver", "assigned_to": str(membre.pk),
        "priority": "normale", "status": "todo",
        "parcelles": [str(clos.pk), str(hauts.pk)],
        "subtasks": json.dumps([
            {"id": None, "title": "Affûter le sécateur", "done": False, "member": "", "date": ""},
            {"id": None, "title": "Sortir la remorque", "done": True, "member": "", "date": ""},
        ]),
    })
    assert resp.status_code == 302

    tache = Task.objects.get(title="Taille d'hiver")
    assert set(tache.parcelles.all()) == {clos, hauts}
    assert tache.parcelle == clos              # la première, pour l'API
    filles = list(tache.subtasks.order_by("title"))
    assert [f.title for f in filles] == ["Affûter le sécateur", "Sortir la remorque"]
    assert filles[1].is_done
    # Une fille hérite de l'assigné de sa mère quand on n'en désigne pas d'autre.
    assert filles[0].assigned_to == membre


@pytest.mark.django_db
def test_les_deux_ecrans_partagent_la_meme_logique(client, ferme_rh):
    """Le planning et /taches/ appellent les mêmes services : pas de dérive."""
    import inspect

    from equipe import services
    from planning import views as planning_views

    source = inspect.getsource(planning_views._save_task)
    assert "enregistrer_parcelles" in source and "enregistrer_sous_taches" in source
    assert hasattr(services, "enregistrer_parcelles")
    assert hasattr(services, "enregistrer_sous_taches")


# ── Fiches de paie ───────────────────────────────────────────────────


def _fiche_valide(salarie, **surcharges):
    import json

    champs = {
        "membre": str(salarie.pk),
        "periode_debut": "2026-06-01", "periode_fin": "2026-06-30",
        "heures_travaillees": "151,67",
        "salaire_brut": "1 850,50", "cotisations_salariales": "425,60",
        "cotisations_patronales": "610,00", "net_imposable": "1 500,00",
        "net_a_payer": "1 424,90", "statut": "emise",
        "lignes": json.dumps([
            {"libelle": "Maladie", "base": "1850.50", "taux": "0.75", "part_salariale": "13.88", "part_patronale": "240.00"},
            {"libelle": "Retraite", "base": "1850.50", "taux": "6.90", "part_salariale": "411.72", "part_patronale": "370.00"},
        ]),
    }
    champs.update(surcharges)
    return champs


@pytest.mark.django_db
def test_ajouter_une_fiche_de_paie(client, ferme_rh):
    from equipe.models import FichePaie

    patron, exploitation, membre = ferme_rh
    client.force_login(patron)
    assert client.post("/paie/enregistrer/", _fiche_valide(membre)).status_code == 302

    fiche = FichePaie.objects.get(membre=membre)
    assert fiche.exploitation == exploitation
    assert fiche.salaire_brut == 1850.50 and fiche.net_a_payer == 1424.90
    assert fiche.heures_travaillees == 151.67
    assert [l.libelle for l in fiche.lignes.all()] == ["Maladie", "Retraite"]
    # Le coût employeur, c'est de l'addition : brut + charges patronales.
    assert fiche.cout_employeur == 2460.50


@pytest.mark.django_db
def test_l_addition_des_rubriques_est_verifiee(client, ferme_rh):
    """Isidor ne calcule pas la paie, mais il sait additionner."""
    from equipe.models import FichePaie

    patron, _exploitation, membre = ferme_rh
    client.force_login(patron)

    # 13,88 + 411,72 = 425,60 : cohérent.
    client.post("/paie/enregistrer/", _fiche_valide(membre))
    assert FichePaie.objects.get(membre=membre).addition_coherente

    # On annonce un total qui ne correspond plus aux rubriques.
    fiche = FichePaie.objects.get(membre=membre)
    resp = client.post(f"/paie/{fiche.pk}/enregistrer/",
                       _fiche_valide(membre, cotisations_salariales="999,00"), follow=True)
    fiche.refresh_from_db()
    assert not fiche.addition_coherente
    assert any("ne totalisent pas" in str(m) for m in resp.context["messages"])


@pytest.mark.django_db
def test_une_fiche_sans_rubrique_reste_coherente(client, ferme_rh):
    """On ne reproche rien à une fiche saisie en totaux seuls."""
    from equipe.models import FichePaie

    patron, _exploitation, membre = ferme_rh
    client.force_login(patron)
    client.post("/paie/enregistrer/", _fiche_valide(membre, lignes="[]"))
    fiche = FichePaie.objects.get(membre=membre)
    assert fiche.lignes.count() == 0 and fiche.addition_coherente


@pytest.mark.django_db
def test_pas_deux_fiches_pour_la_meme_periode(client, ferme_rh):
    from equipe.models import FichePaie

    patron, _exploitation, membre = ferme_rh
    client.force_login(patron)
    client.post("/paie/enregistrer/", _fiche_valide(membre))
    resp = client.post("/paie/enregistrer/", _fiche_valide(membre), follow=True)
    assert FichePaie.objects.count() == 1
    assert any("déjà une fiche" in str(m) for m in resp.context["messages"])


@pytest.mark.django_db
def test_une_fiche_sans_salarie_ou_sans_periode_est_refusee(client, ferme_rh):
    from equipe.models import FichePaie

    patron, _exploitation, membre = ferme_rh
    client.force_login(patron)
    client.post("/paie/enregistrer/", _fiche_valide(membre, membre=""))
    client.post("/paie/enregistrer/", _fiche_valide(membre, periode_debut=""))
    assert FichePaie.objects.count() == 0


@pytest.mark.django_db
def test_le_bulletin_sort_en_pdf(client, ferme_rh):
    from equipe.models import FichePaie

    patron, _exploitation, membre = ferme_rh
    client.force_login(patron)
    client.post("/paie/enregistrer/", _fiche_valide(membre))
    fiche = FichePaie.objects.get(membre=membre)

    resp = client.get(f"/paie/{fiche.pk}/pdf/")
    assert resp.status_code == 200
    if resp["Content-Type"] == "application/pdf":
        assert resp.content[:5] == b"%PDF-"
    else:  # WeasyPrint absent : on rend la page imprimable
        corps = resp.content.decode()
        assert "Bulletin de paie" in corps and "Net à payer" in corps


@pytest.mark.django_db
def test_la_fiche_du_voisin_est_hors_de_portee(client, ferme_rh):
    from equipe.models import FichePaie, TeamMember
    from exploitations.models import Exploitation

    patron, _exploitation, _membre = ferme_rh
    voisin = User.objects.create_user(email="voisin-paie@ex.com", password="pwd12345")
    ferme_voisine = Exploitation.objects.create(owner=voisin, name="Ferme voisine")
    son_salarie = TeamMember.objects.create(exploitation=ferme_voisine, name="Luc")
    sa_fiche = FichePaie.objects.create(
        exploitation=ferme_voisine, membre=son_salarie,
        periode_debut="2026-06-01", periode_fin="2026-06-30", salaire_brut=2000)

    client.force_login(patron)
    assert client.get(f"/paie/{sa_fiche.pk}/pdf/").status_code == 404
    assert client.post(f"/paie/{sa_fiche.pk}/supprimer/").status_code == 404
    # Et on ne lui fabrique pas une fiche non plus.
    client.post("/paie/enregistrer/", _fiche_valide(son_salarie))
    assert FichePaie.objects.count() == 1


@pytest.mark.django_db
def test_la_page_dit_qu_elle_ne_calcule_pas(client, ferme_rh):
    patron, _exploitation, _membre = ferme_rh
    client.force_login(patron)
    html = client.get("/paie/").content.decode()
    assert "il ne calcule pas la paie" in html
    assert "Ajouter une fiche de paie" in html
    assert "{#" not in html and "{{" not in html


@pytest.mark.django_db
def test_le_bandeau_ne_change_pas_entre_l_accueil_et_les_emplois(client, offre_publiee):
    """Passer aux offres ne doit pas donner l'impression de quitter le site.

    Les deux pages partagent le même partiel : même marque, même navigation,
    mêmes boutons d'accès. Seule l'entrée courante est signalée.
    """
    import re

    def bandeau(url):
        corps = client.get(url).content.decode()
        m = re.search(r'<header class="lp-header".*?</header>', corps, re.S)
        assert m, f"pas de bandeau de vitrine sur {url}"
        return m.group(0)

    accueil, emplois = bandeau("/"), bandeau("/emplois/")
    for entree in ("Domaines", "Tarif", "FAQ", "Offres d'emploi", "Connexion", "S'inscrire"):
        assert entree in accueil and entree in emplois, entree
    # Les ancres sont absolues : elles fonctionnent depuis les deux pages.
    for ancre in ("/#domaines", "/#tarif", "/#faq"):
        assert ancre in accueil and ancre in emplois, ancre
    # Seule différence attendue : l'entrée courante.
    assert 'aria-current="page"' in emplois and 'aria-current="page"' not in accueil
    assert emplois.replace(' aria-current="page"', "") == accueil


@pytest.mark.django_db
def test_un_modele_s_imprime_vierge(client, setup):
    """Une feuille à remplir à la main : les jetons deviennent des pointillés."""
    from equipe.models import ModeleContrat

    user, exploitation, _membre = setup
    modele = ModeleContrat.objects.create(
        exploitation=exploitation, nom="CDI saisonnier", type_contrat="cdi",
        corps="Entre {{ exploitation }} et {{ salarie }}, poste de {{ poste }}.")
    client.force_login(user)

    reponse = client.get(f"/contrats-travail/modeles/{modele.pk}/pdf/")
    assert reponse.status_code == 200

    if reponse["Content-Type"] == "application/pdf":
        assert reponse["Content-Disposition"].startswith("inline;")
    else:  # WeasyPrint absent : on rend la page imprimable
        corps = reponse.content.decode()
        assert "……………………" in corps, "les jetons devraient devenir des pointillés"
        assert "{{ salarie }}" not in corps
        assert "Modèle vierge" in corps
        # La signature de l'employeur ne s'appose pas sur un modèle vierge.
        assert "signature" not in corps.lower() or "img" not in corps


@pytest.mark.django_db
def test_le_modele_du_voisin_ne_s_imprime_pas(client, setup, django_user_model):
    from equipe.models import ModeleContrat
    from exploitations.models import Exploitation

    user, _exploitation, _membre = setup
    voisin = django_user_model.objects.create_user(email="voisin-modele@ex.com",
                                                   password="pwd12345")
    chez_lui = Exploitation.objects.create(owner=voisin, name="Ferme Voisine")
    modele = ModeleContrat.objects.create(exploitation=chez_lui, nom="Secret",
                                          type_contrat="cdi", corps="…")

    client.force_login(user)
    reponse = client.get(f"/contrats-travail/modeles/{modele.pk}/pdf/", follow=True)
    assert "Secret" not in reponse.content.decode()


@pytest.mark.django_db
def test_le_modele_s_ouvre_en_document_a_remplir(client, ferme_rh):
    """Chaque blanc devient un champ, déjà porteur de ce que la ferme sait."""
    from equipe.models import ModeleContrat

    patron, exploitation, membre = ferme_rh
    modele = ModeleContrat.objects.create(
        exploitation=exploitation, nom="CDI maison", type_contrat="cdi",
        corps="{{ salarie }} est engagé par {{ exploitation }} au poste de "
              "{{ poste }} à {{ lieu }} le {{ date_debut }}.")

    client.force_login(patron)
    page = client.get(f"/contrats-travail/modeles/{modele.pk}/etablir/").content.decode()

    # Le texte du modèle demeure, les jetons ont laissé place à des champs.
    assert "est engagé par" in page and "{{" not in page
    for cle in ("salarie", "exploitation", "poste", "lieu", "date_debut"):
        assert f'name="{cle}"' in page
    # Le nom de la ferme est déjà là ; le poste propose les rôles de l'équipe.
    assert "Ferme des Coteaux" in page
    assert "<option value=\"Ouvrier\">" in page
    # Le salarié se choisit en tête, ce qui remplira son identité.
    assert "Paul Martin" in page


@pytest.mark.django_db
def test_les_valeurs_choisies_priment_sur_les_deduites(client, ferme_rh):
    """Ce que l'exploitant corrige dans un blanc ne doit pas être réécrit."""
    from equipe.models import ContratTravail, ModeleContrat

    patron, exploitation, membre = ferme_rh
    modele = ModeleContrat.objects.create(
        exploitation=exploitation, nom="CDI maison", type_contrat="cdi",
        corps="Employeur {{ employeur }}, siège {{ exploitation_adresse }}, "
              "salarié {{ salarie }}.")

    client.force_login(patron)
    client.post("/contrats-travail/etablir/", {
        "membre": str(membre.pk), "modele": str(modele.pk),
        "employeur": "Marie Dupont", "exploitation_adresse": "3 route des Vignes",
    })

    corps = ContratTravail.objects.get(membre=membre).corps
    assert "Employeur Marie Dupont" in corps
    assert "siège 3 route des Vignes" in corps
    # Un blanc non soumis reste déduit de l'exploitation.
    assert "salarié Paul Martin" in corps


@pytest.mark.django_db
def test_le_modele_du_voisin_ne_s_etablit_pas(client, ferme_rh):
    from equipe.models import ModeleContrat
    from exploitations.models import Exploitation

    patron, _exploitation, _membre = ferme_rh
    voisin = User.objects.create_user(email="voisin@ex.com", password="pwd12345")
    ailleurs = Exploitation.objects.create(owner=voisin, name="Ferme d'à côté")
    modele = ModeleContrat.objects.create(exploitation=ailleurs, nom="Son CDI",
                                          type_contrat="cdi", corps="{{ salarie }}")

    client.force_login(patron)
    reponse = client.get(f"/contrats-travail/modeles/{modele.pk}/etablir/")
    assert reponse.status_code == 302 and reponse.url == "/contrats-travail/"


@pytest.mark.django_db
def test_une_date_saisie_s_ecrit_en_toutes_lettres(client, ferme_rh):
    """Le calendrier rend « 2026-09-15 » ; un contrat ne s'écrit pas ainsi."""
    from equipe.models import ContratTravail, ModeleContrat

    patron, exploitation, membre = ferme_rh
    modele = ModeleContrat.objects.create(
        exploitation=exploitation, nom="CDD maison", type_contrat="cdd",
        corps="Le contrat prend effet le {{ date_debut }}.")

    client.force_login(patron)
    client.post("/contrats-travail/etablir/", {
        "membre": str(membre.pk), "modele": str(modele.pk), "date_debut": "2026-09-15"})

    corps = ContratTravail.objects.get(membre=membre).corps
    assert "2026-09-15" not in corps
    assert "15 septembre 2026" in corps


def _signature(exploitation, par_defaut=False, nom="signature.png"):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from identite.models import Piece

    return Piece.objects.create(
        exploitation=exploitation, type_piece=Piece.Type.SIGNATURE, par_defaut=par_defaut,
        fichier=SimpleUploadedFile(nom, b"\x89PNG signature", content_type="image/png"))


@pytest.mark.django_db
def test_la_signature_se_choisit_a_l_endroit_ou_l_employeur_signe(client, ferme_rh):
    """Deux signatures portent le même nom : on choisit sur l'image."""
    from equipe.models import ModeleContrat

    patron, exploitation, membre = ferme_rh
    courante = _signature(exploitation, par_defaut=True)
    autre = _signature(exploitation, nom="autre.png")
    modele = ModeleContrat.objects.create(
        exploitation=exploitation, nom="CDI maison", type_contrat="cdi",
        corps="Représentée par {{ employeur }}.\n\nL'employeur\n{{ employeur }}")

    client.force_login(patron)
    brut = client.get(f"/contrats-travail/modeles/{modele.pk}/etablir/").content.decode()
    page = html.unescape(brut)  # l'état de départ voyage dans un attribut

    # Les deux signatures sont offertes, la courante déjà retenue.
    assert brut.count('name="signature"') == 3  # les deux pièces, plus « à la main »
    assert courante.fichier.url in page and autre.fichier.url in page
    assert f'"signature": "{courante.pk}"' in page
    # Le choix se pose à la dernière mention de l'employeur, pas à la première.
    assert page.index('class="ct-signature"') > page.index("Représentée par")


@pytest.mark.django_db
def test_la_signature_choisie_se_fige_sur_le_contrat(client, ferme_rh, monkeypatch):
    from equipe.models import ContratTravail, ModeleContrat

    patron, exploitation, membre = ferme_rh
    _signature(exploitation, par_defaut=True)
    choisie = _signature(exploitation, nom="celle-ci.png")
    modele = ModeleContrat.objects.create(exploitation=exploitation, nom="CDI",
                                          type_contrat="cdi", corps="{{ employeur }}")

    client.force_login(patron)
    client.post("/contrats-travail/etablir/", {
        "membre": str(membre.pk), "modele": str(modele.pk), "signature": str(choisie.pk)})

    contrat = ContratTravail.objects.get(membre=membre)
    assert contrat.signature == choisie
    # Et c'est bien elle qui s'appose sur le document remis. Sans WeasyPrint,
    # la vue rend la page imprimable : c'est là qu'on peut la lire.
    monkeypatch.setitem(sys.modules, "weasyprint", None)
    page = client.get(f"/contrats-travail/{contrat.pk}/pdf/").content.decode()
    assert choisie.fichier.url in page


@pytest.mark.django_db
def test_signer_a_la_main_laisse_le_contrat_vierge(client, ferme_rh):
    """Un choix vide est un choix : il ne doit pas rappeler la signature par défaut."""
    from equipe.models import ContratTravail, ModeleContrat

    patron, exploitation, membre = ferme_rh
    _signature(exploitation, par_defaut=True)
    modele = ModeleContrat.objects.create(exploitation=exploitation, nom="CDI",
                                          type_contrat="cdi", corps="{{ employeur }}")

    client.force_login(patron)
    client.post("/contrats-travail/etablir/", {
        "membre": str(membre.pk), "modele": str(modele.pk), "signature": ""})

    assert ContratTravail.objects.get(membre=membre).signature is None


@pytest.mark.django_db
def test_la_signature_du_voisin_ne_s_appose_pas(client, ferme_rh):
    from equipe.models import ContratTravail, ModeleContrat
    from exploitations.models import Exploitation

    patron, exploitation, membre = ferme_rh
    voisin = User.objects.create_user(email="voisin2@ex.com", password="pwd12345")
    la_sienne = _signature(Exploitation.objects.create(owner=voisin, name="Ferme d'à côté"))
    modele = ModeleContrat.objects.create(exploitation=exploitation, nom="CDI",
                                          type_contrat="cdi", corps="{{ employeur }}")

    client.force_login(patron)
    client.post("/contrats-travail/etablir/", {
        "membre": str(membre.pk), "modele": str(modele.pk), "signature": str(la_sienne.pk)})

    assert ContratTravail.objects.get(membre=membre).signature is None


def _fiche_membre(membre, **surcharges):
    donnees = {"first_name": "Paul", "last_name": "Martin", "email": membre.email,
               "phone": membre.phone, "role": "ouvrier"}
    donnees.update(surcharges)
    return donnees


@pytest.mark.django_db
def test_la_fiche_du_membre_porte_ce_que_le_contrat_demande(client, ferme_rh):
    patron, _exploitation, membre = ferme_rh
    client.force_login(patron)

    reponse = client.post(f"/equipe/{membre.pk}/modifier/", _fiche_membre(
        membre, civilite="m", date_naissance="1985-05-12", lieu_naissance="Bordeaux (33)",
        nationalite="française", numero_securite_sociale="1 85 05 33 063 042 26",
        adresse="12 chemin des Vignes", code_postal="33000", ville="Bordeaux",
        poste="Tractoriste", qualification="Palier 3"))

    assert reponse.status_code == 302
    membre.refresh_from_db()
    assert membre.date_naissance.isoformat() == "1985-05-12"
    # Le numéro s'enregistre sans ses espaces.
    assert membre.numero_securite_sociale == "185053306304226"
    assert membre.adresse_complete == "12 chemin des Vignes 33000 Bordeaux"
    assert membre.intitule_poste == "Tractoriste"


@pytest.mark.django_db
@pytest.mark.parametrize("nir", ["185053306304227", "18505330630", "1850533O6304226"])
def test_un_numero_de_securite_sociale_faux_est_refuse(client, ferme_rh, nir):
    patron, _exploitation, membre = ferme_rh
    client.force_login(patron)

    reponse = client.post(f"/equipe/{membre.pk}/modifier/",
                          _fiche_membre(membre, numero_securite_sociale=nir))

    assert reponse.status_code == 200
    assert "numero_securite_sociale" in reponse.context["form"].errors
    membre.refresh_from_db()
    assert membre.numero_securite_sociale == ""


def test_un_numero_corse_se_verifie_avec_son_departement_en_lettres():
    from equipe.forms import TeamMemberEditForm

    form = TeamMemberEditForm(data={"first_name": "A", "last_name": "B", "email": "a@b.fr",
                                    "role": "ouvrier", "numero_securite_sociale": "185052A06304216"})
    assert form.is_valid(), form.errors


@pytest.mark.django_db
def test_le_contrat_reprend_l_etat_civil_de_la_fiche(client, ferme_rh):
    """Choisir le salarié suffit : son état civil remplit les blancs."""
    import datetime

    from equipe.models import ContratTravail, ModeleContrat

    patron, exploitation, membre = ferme_rh
    TeamMember.objects.filter(pk=membre.pk).update(
        civilite="m", date_naissance=datetime.date(1985, 5, 12), lieu_naissance="Bordeaux",
        nationalite="française", numero_securite_sociale="185053306304226",
        adresse="12 chemin des Vignes", code_postal="33000", ville="Bordeaux",
        poste="Tractoriste", qualification="Palier 3")
    modele = ModeleContrat.objects.create(
        exploitation=exploitation, nom="CDI maison", type_contrat="cdi",
        corps="{{ salarie_civilite }} {{ salarie }}, né le {{ salarie_date_naissance }} à "
              "{{ salarie_lieu_naissance }}, {{ salarie_nationalite }}, demeurant "
              "{{ salarie_adresse }}, n° {{ salarie_securite_sociale }}, "
              "{{ poste }} ({{ qualification }}).")

    client.force_login(patron)
    client.post("/contrats-travail/etablir/", {"membre": str(membre.pk), "modele": str(modele.pk)})

    corps = ContratTravail.objects.get(membre=membre).corps
    assert corps == ("Monsieur Paul Martin, né le 12 mai 1985 à Bordeaux, française, "
                     "demeurant 12 chemin des Vignes 33000 Bordeaux, n° 185053306304226, "
                     "Tractoriste (Palier 3).")


@pytest.mark.django_db
def test_la_fiche_attend_qu_on_choisisse_le_salarie(client, ferme_rh):
    """Les blancs de la fiche ne se remplissent pas d'avance avec le premier membre."""
    from equipe.models import ModeleContrat

    patron, exploitation, membre = ferme_rh
    TeamMember.objects.filter(pk=membre.pk).update(qualification="Palier 3",
                                                   lieu_naissance="Bordeaux")
    modele = ModeleContrat.objects.create(
        exploitation=exploitation, nom="CDI maison", type_contrat="cdi",
        corps="Né à {{ salarie_lieu_naissance }}, qualification {{ qualification }}.")

    client.force_login(patron)
    reponse = client.get(f"/contrats-travail/modeles/{modele.pk}/etablir/")

    etat = json.loads(reponse.context["etat"])
    assert etat["valeurs"] == {"salarie_lieu_naissance": "", "qualification": ""}
    # C'est la fiche du salarié choisi qui les apportera.
    fiche = json.loads(reponse.context["fiches_membres"])[str(membre.pk)]
    assert fiche["qualification"] == "Palier 3" and fiche["salarie_lieu_naissance"] == "Bordeaux"


# ── Banque des postes ──

@pytest.mark.django_db
def test_la_banque_passe_avant_les_postes_courants(ferme_rh):
    """Un intitulé de la ferme masque son homonyme courant, casse ignorée."""
    from equipe import postes
    from equipe.models import Poste

    _patron, exploitation, _membre = ferme_rh
    Poste.objects.create(exploitation=exploitation, intitule="TRACTORISTE",
                         qualification="Palier 4")

    proposes = postes.suggestions(exploitation)
    assert proposes[0] == {"intitule": "TRACTORISTE", "qualification": "Palier 4",
                           "missions": "", "profil": "", "source": "banque"}
    intitules = [p["intitule"].casefold() for p in proposes]
    assert intitules.count("tractoriste") == 1
    assert "ouvrier viticole" in intitules  # les courants restent proposés


@pytest.mark.django_db
def test_importer_les_postes_courants_ne_cree_pas_de_doublon(client, ferme_rh):
    from equipe.models import Poste
    from equipe.postes import POSTES_COURANTS

    patron, exploitation, _membre = ferme_rh
    Poste.objects.create(exploitation=exploitation, intitule="ouvrier viticole")
    client.force_login(patron)

    client.post("/postes/importer/")
    client.post("/postes/importer/")

    assert Poste.objects.filter(exploitation=exploitation).count() == len(POSTES_COURANTS)
    # Le poste que la ferme avait déjà n'a pas été remplacé.
    assert Poste.objects.filter(exploitation=exploitation, intitule="ouvrier viticole").exists()


@pytest.mark.django_db
def test_un_poste_s_enregistre_une_seule_fois(client, ferme_rh):
    from equipe.models import Poste

    patron, exploitation, _membre = ferme_rh
    client.force_login(patron)

    client.post("/postes/enregistrer/", {"intitule": "Chef de culture", "qualification": "Palier 8",
                                          "missions": "Organiser les cultures."})
    reponse = client.post("/postes/enregistrer/", {"intitule": "chef de culture"}, follow=True)

    assert Poste.objects.filter(exploitation=exploitation).count() == 1
    assert "déjà dans la banque" in reponse.content.decode()

    poste = Poste.objects.get(exploitation=exploitation)
    client.post(f"/postes/{poste.pk}/enregistrer/", {"intitule": "Chef de culture",
                                                      "qualification": "Palier 9"})
    poste.refresh_from_db()
    assert poste.qualification == "Palier 9" and poste.missions == ""


@pytest.mark.django_db
def test_le_poste_du_voisin_ne_se_touche_pas(client, ferme_rh):
    from equipe.models import Poste

    patron, _exploitation, _membre = ferme_rh
    voisin = User.objects.create_user(email="voisin-poste@ex.com", password="pwd12345")
    ailleurs = Exploitation.objects.create(owner=voisin, name="Ferme d'en face")
    le_sien = Poste.objects.create(exploitation=ailleurs, intitule="Berger")
    client.force_login(patron)

    assert client.post(f"/postes/{le_sien.pk}/supprimer/").status_code == 404
    assert client.post(f"/postes/{le_sien.pk}/enregistrer/", {"intitule": "Pirate"}).status_code == 404
    assert "Berger" not in client.get("/postes/").content.decode()
    le_sien.refresh_from_db()
    assert le_sien.intitule == "Berger"


@pytest.mark.django_db
def test_la_banque_compte_les_fiches_et_offres_qui_portent_le_poste(client, ferme_rh):
    from equipe.models import OffreEmploi, Poste

    patron, exploitation, membre = ferme_rh
    Poste.objects.create(exploitation=exploitation, intitule="Tractoriste")
    TeamMember.objects.filter(pk=membre.pk).update(poste="tractoriste")
    OffreEmploi.objects.create(exploitation=exploitation, titre="Tractoriste", description="…")
    client.force_login(patron)

    reponse = client.get("/postes/")
    assert reponse.context["postes"][0].usages == 2


@pytest.mark.django_db
def test_la_banque_se_propose_la_ou_l_on_ecrit_un_intitule(client, ferme_rh):
    from equipe.models import ModeleContrat, Poste

    patron, exploitation, membre = ferme_rh
    Poste.objects.create(exploitation=exploitation, intitule="Caviste maison",
                         qualification="Palier 5", missions="Tenir le chai.")
    modele = ModeleContrat.objects.create(exploitation=exploitation, nom="CDI", type_contrat="cdi",
                                          corps="Au poste de {{ poste }}.")
    client.force_login(patron)

    # La fiche et l'offre portent la liste déroulante et, en données, la
    # banque de la ferme suivie des postes courants.
    for url in (f"/equipe/{membre.pk}/modifier/", "/offres-emploi/"):
        page = client.get(url)
        assert 'role="combobox"' in page.content.decode()
        proposes = page.context["banque_postes"]
        assert proposes[0]["intitule"] == "Caviste maison"
        assert proposes[0]["missions"] == "Tenir le chai."
        assert "Ouvrier viticole" in [p["intitule"] for p in proposes]

    contrat = client.get(f"/contrats-travail/modeles/{modele.pk}/etablir/").content.decode()
    assert '<option value="Caviste maison">' in contrat


@pytest.mark.django_db
def test_l_offre_publique_montre_la_photo_de_la_ferme(client, ferme_rh):
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    from equipe.models import OffreEmploi

    _patron, exploitation, _membre = ferme_rh
    tampon = io.BytesIO()
    Image.new("RGB", (16, 9), (40, 120, 60)).save(tampon, format="PNG")
    exploitation.photo = SimpleUploadedFile("ferme.png", tampon.getvalue(), content_type="image/png")
    exploitation.save()
    offre = OffreEmploi.objects.create(exploitation=exploitation, titre="Tractoriste", description="…",
                                       statut="publiee", publiee_le=timezone.now())

    assert exploitation.photo.url in client.get("/emplois/").content.decode()
    assert exploitation.photo.url in client.get(f"/emplois/{offre.slug}/").content.decode()


@pytest.mark.django_db
def test_l_offre_n_a_plus_d_email_de_contact(client, ferme_rh):
    patron, _exploitation, _membre = ferme_rh
    client.force_login(patron)
    assert 'name="contact_email"' not in client.get("/offres-emploi/").content.decode()
