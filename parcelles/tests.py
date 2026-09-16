"""Tests parcelles : CRUD web + API + isolation multi-tenant."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from exploitations.models import Exploitation
from parcelles.models import Parcelle

User = get_user_model()


@pytest.fixture
def user_exploitation(db):
    user = User.objects.create_user(email="a@ex.com", password="pwd12345")
    exploitation = Exploitation.objects.create(owner=user, name="Ferme A", total_area=10)
    return user, exploitation


@pytest.mark.django_db
def test_create_parcelle_web(client, user_exploitation):
    user, exploitation = user_exploitation
    client.force_login(user)
    resp = client.post(
        reverse("parcelles:create"),
        {"name": "Nord", "area": 2.5, "kc_value": 1.0, "status": "active"},
    )
    assert resp.status_code == 302
    parcelle = Parcelle.objects.get(name="Nord")
    assert parcelle.exploitation == exploitation


@pytest.mark.django_db
def test_create_requires_onboarding(client):
    user = User.objects.create_user(email="b@ex.com", password="pwd12345")
    client.force_login(user)
    resp = client.get(reverse("parcelles:create"))
    assert resp.status_code == 302
    assert reverse("exploitations:settings") in resp.url


@pytest.mark.django_db
def test_api_list_is_tenant_scoped(client, user_exploitation):
    user, exploitation = user_exploitation
    Parcelle.objects.create(exploitation=exploitation, name="P1")
    # Autre utilisateur / autre exploitation
    other = User.objects.create_user(email="c@ex.com", password="pwd12345")
    other_exp = Exploitation.objects.create(owner=other, name="Ferme C")
    Parcelle.objects.create(exploitation=other_exp, name="P2")

    client.force_login(user)
    resp = client.get("/api/parcelles/")
    assert resp.status_code == 200
    names = {p["name"] for p in resp.json()}
    assert names == {"P1"}  # P2 (autre tenant) absente


@pytest.mark.django_db
def test_api_create_attaches_exploitation(client, user_exploitation):
    user, exploitation = user_exploitation
    client.force_login(user)
    resp = client.post("/api/parcelles/", {"name": "Sud", "kc_value": 1.0}, content_type="application/json")
    assert resp.status_code == 201
    assert Parcelle.objects.get(name="Sud").exploitation == exploitation


@pytest.mark.django_db
def test_detail_404_cross_tenant(client, user_exploitation):
    user, exploitation = user_exploitation
    other = User.objects.create_user(email="d@ex.com", password="pwd12345")
    other_exp = Exploitation.objects.create(owner=other, name="Ferme D")
    p = Parcelle.objects.create(exploitation=other_exp, name="Secrète")
    client.force_login(user)
    resp = client.get(reverse("parcelles:detail", args=[p.pk]))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_campagnes_page_lists_and_filters(client, user_exploitation):
    """La page Campagnes agrège toutes les parcelles et filtre par libellé."""
    from parcelles.models import ParcelleCampagne

    user, exploitation = user_exploitation
    nord = Parcelle.objects.create(exploitation=exploitation, name="Nord", area=3)
    sud = Parcelle.objects.create(exploitation=exploitation, name="Sud", area=2)
    ParcelleCampagne.objects.create(parcelle=nord, libelle="2025/2026", culture="Vigne")
    ParcelleCampagne.objects.create(parcelle=sud, libelle="2025/2026", culture="Olivier")
    ParcelleCampagne.objects.create(parcelle=nord, libelle="2024/2025", culture="Blé")

    client.force_login(user)

    toutes = client.get(reverse("parcelles:campagnes"), {"campagne": ""})
    assert toutes.status_code == 200 and toutes.context["kpi_count"] == 3
    assert toutes.context["libelles"] == ["2025/2026", "2024/2025"]

    filtre = client.get(reverse("parcelles:campagnes"), {"campagne": "2025/2026"})
    assert filtre.context["kpi_count"] == 2 and filtre.context["kpi_parcelles"] == 2
    assert filtre.context["kpi_surface"] == 5 and filtre.context["kpi_cultures"] == 2
    assert "Blé" not in filtre.content.decode()


@pytest.mark.django_db
def test_campagnes_page_is_tenant_scoped(client, user_exploitation):
    from parcelles.models import ParcelleCampagne

    user, exploitation = user_exploitation
    autre = User.objects.create_user(email="autre-camp@ex.com", password="pwd12345")
    autre_exploitation = Exploitation.objects.create(owner=autre, name="Ferme B", total_area=5)
    intruse = Parcelle.objects.create(exploitation=autre_exploitation, name="Intruse", area=1)
    ParcelleCampagne.objects.create(parcelle=intruse, libelle="2025/2026", culture="Colza")

    client.force_login(user)
    resp = client.get(reverse("parcelles:campagnes"))
    assert resp.context["kpi_count"] == 0 and "Intruse" not in resp.content.decode()


@pytest.mark.django_db
def test_campagne_new_creates_from_campagnes_page(client, user_exploitation):
    """Création d'une campagne hors fiche parcelle : la parcelle est choisie au formulaire."""
    from parcelles.models import ParcelleCampagne

    user, exploitation = user_exploitation
    parcelle = Parcelle.objects.create(exploitation=exploitation, name="Nord", area=3)
    client.force_login(user)

    page = client.get(reverse("parcelles:campagne_new"))
    assert page.status_code == 200 and "Nord" in page.content.decode()

    resp = client.post(reverse("parcelles:campagne_new"), {
        "parcelles": [parcelle.pk], "libelle": "2025/2026", "culture": "Vigne", "kc_value": 1.0,
    })
    campagne = ParcelleCampagne.objects.get(libelle="2025/2026")
    assert resp.status_code == 302 and campagne.parcelle == parcelle
    assert resp["Location"].endswith("/campagnes/?campagne=2025/2026")


@pytest.mark.django_db
def test_campagne_new_rejects_missing_or_foreign_parcelle(client, user_exploitation):
    """Sans parcelle valide, rien n'est créé — y compris pour une parcelle d'autrui."""
    from parcelles.models import ParcelleCampagne

    user, exploitation = user_exploitation
    Parcelle.objects.create(exploitation=exploitation, name="Nord", area=3)
    autre = User.objects.create_user(email="autre-new@ex.com", password="pwd12345")
    autre_exploitation = Exploitation.objects.create(owner=autre, name="Ferme B", total_area=5)
    intruse = Parcelle.objects.create(exploitation=autre_exploitation, name="Intruse", area=1)

    client.force_login(user)
    donnees = {"libelle": "2025/2026", "culture": "Vigne", "kc_value": 1.0}

    sans = client.post(reverse("parcelles:campagne_new"), donnees)
    assert sans.status_code == 200 and "Choisissez au moins une parcelle" in sans.content.decode()

    volee = client.post(reverse("parcelles:campagne_new"), {**donnees, "parcelles": [intruse.pk]})
    assert volee.status_code == 200
    assert not ParcelleCampagne.objects.exists()


@pytest.mark.django_db
def test_campagne_new_redirects_when_no_parcelle(client, user_exploitation):
    """Sans aucune parcelle, la page renvoie vers la création de parcelle."""
    user, _exploitation = user_exploitation
    client.force_login(user)

    resp = client.get(reverse("parcelles:campagne_new"))
    assert resp.status_code == 302 and resp["Location"] == reverse("parcelles:list")


@pytest.mark.django_db
def test_campagne_type_culture_deduit_du_referentiel(client, user_exploitation):
    """Type laissé vide → repli sur la catégorie de la culture (agronomie)."""
    from agronomie.models import CultureKc
    from parcelles.models import ParcelleCampagne

    user, exploitation = user_exploitation
    CultureKc.objects.create(nom="Vigne", categorie=CultureKc.Categorie.VIGNE)
    parcelle = Parcelle.objects.create(exploitation=exploitation, name="Nord", area=3)
    campagne = ParcelleCampagne.objects.create(parcelle=parcelle, libelle="2025/2026")
    client.force_login(user)

    client.post(reverse("parcelles:campagne_edit", args=[campagne.pk]), {
        "libelle": "2025/2026", "culture": "Vigne", "type_culture": "", "kc_value": 1.0,
    })
    campagne.refresh_from_db()
    assert campagne.type_culture == "vigne" and campagne.type_culture_label == "Vigne"

    # Un type explicite prime sur la déduction.
    client.post(reverse("parcelles:campagne_edit", args=[campagne.pk]), {
        "libelle": "2025/2026", "culture": "Vigne", "type_culture": "fruits", "kc_value": 1.0,
    })
    campagne.refresh_from_db()
    assert campagne.type_culture == "fruits"


@pytest.mark.django_db
def test_type_culture_visible_sur_la_fiche_parcelle(client, user_exploitation):
    """Ce qui est saisi sur la campagne remonte sur /parcelles/<pk>/."""
    from parcelles.models import ParcelleCampagne

    user, exploitation = user_exploitation
    parcelle = Parcelle.objects.create(exploitation=exploitation, name="Nord", area=3)
    ParcelleCampagne.objects.create(
        parcelle=parcelle, libelle="2025/2026", culture="Merlot", type_culture="vigne",
    )

    client.force_login(user)
    body = client.get(reverse("parcelles:detail", args=[parcelle.pk])).content.decode()
    assert "Type de culture" in body and "Vigne" in body


@pytest.mark.django_db
def test_campagne_delete_revient_sur_la_liste_des_campagnes(client, user_exploitation):
    """Suppression lancée depuis la page Campagnes → retour sur cette page."""
    from parcelles.models import ParcelleCampagne

    user, exploitation = user_exploitation
    parcelle = Parcelle.objects.create(exploitation=exploitation, name="Nord", area=3)
    campagne = ParcelleCampagne.objects.create(parcelle=parcelle, libelle="2025/2026")
    url = reverse("parcelles:campagne_delete", args=[campagne.pk])
    client.force_login(user)

    confirmation = client.get(url, {"next": "campagnes"})
    assert confirmation.status_code == 200
    assert confirmation.context["retour_url"] == reverse("parcelles:campagnes")

    resp = client.post(url, {"next": "campagnes"})
    assert resp["Location"] == reverse("parcelles:campagnes")
    assert not ParcelleCampagne.objects.filter(pk=campagne.pk).exists()


@pytest.mark.django_db
def test_campagne_delete_depuis_la_parcelle_inchange(client, user_exploitation):
    """Sans ?next, la suppression continue de renvoyer sur la fiche parcelle."""
    from parcelles.models import ParcelleCampagne

    user, exploitation = user_exploitation
    parcelle = Parcelle.objects.create(exploitation=exploitation, name="Nord", area=3)
    campagne = ParcelleCampagne.objects.create(parcelle=parcelle, libelle="2025/2026")

    client.force_login(user)
    resp = client.post(reverse("parcelles:campagne_delete", args=[campagne.pk]))
    assert resp["Location"] == parcelle.get_absolute_url()


@pytest.mark.parametrize("jour,attendu", [
    ("2026-08-31", "2025/2026"),  # août : encore la campagne précédente
    ("2026-09-01", "2026/2027"),  # 1ᵉʳ septembre : bascule
    ("2026-12-15", "2026/2027"),
    ("2027-07-01", "2026/2027"),
])
def test_libelle_courant_bascule_en_septembre(jour, attendu):
    from datetime import date

    from parcelles.models import ParcelleCampagne

    annee, mois, num = (int(x) for x in jour.split("-"))
    assert ParcelleCampagne.libelle_courant(date(annee, mois, num)) == attendu


@pytest.mark.django_db
def test_type_agriculture_partage_entre_campagne_et_parcelle(client, user_exploitation):
    """Le type d'agriculture édité depuis la campagne est bien celui de la parcelle."""
    from parcelles.models import ParcelleCampagne

    user, exploitation = user_exploitation
    parcelle = Parcelle.objects.create(exploitation=exploitation, name="Nord", area=3, type_agriculture="bio")
    campagne = ParcelleCampagne.objects.create(parcelle=parcelle, libelle="2025/2026")
    client.force_login(user)

    # La valeur de la parcelle est pré-remplie sur l'écran campagne.
    page = client.get(reverse("parcelles:campagne_edit", args=[campagne.pk]))
    assert page.status_code == 200
    assert page.context["parcelle_form"]["type_agriculture"].value() == "bio"

    # La modifier depuis la campagne met à jour la parcelle.
    client.post(reverse("parcelles:campagne_edit", args=[campagne.pk]), {
        "libelle": "2025/2026", "culture": "Vigne", "kc_value": 1.0, "type_agriculture": "hve",
    })
    parcelle.refresh_from_db()
    assert parcelle.type_agriculture == "hve"

    # Et l'écran parcelle affiche la même valeur.
    edition = client.get(reverse("parcelles:edit", args=[parcelle.pk]))
    assert edition.context["form"]["type_agriculture"].value() == "hve"


@pytest.mark.django_db
def test_type_agriculture_depuis_nouvelle_campagne(client, user_exploitation):
    user, exploitation = user_exploitation
    parcelle = Parcelle.objects.create(exploitation=exploitation, name="Sud", area=2)
    client.force_login(user)

    client.post(reverse("parcelles:campagne_new"), {
        "parcelles": [parcelle.pk], "libelle": "2025/2026", "kc_value": 1.0,
        "type_agriculture": "conversion",
    })
    parcelle.refresh_from_db()
    assert parcelle.type_agriculture == "conversion"


# ── Redessin du contour ─────────────────────────────────────────────────────
def _carre(lon=4.80, lat=43.95, cote=0.0025):
    """Anneau fermé d'environ 200 m de côté (~4 ha à cette latitude)."""
    return [[lon, lat], [lon + cote, lat], [lon + cote, lat + cote * 0.72],
            [lon, lat + cote * 0.72], [lon, lat]]


def _post_contour(client, parcelle, anneau):
    return client.post(
        reverse("parcelles:contour", args=[parcelle.pk]),
        data={"geometry": {"type": "Polygon", "coordinates": [anneau]}},
        content_type="application/json",
    )


@pytest.mark.django_db
def test_contour_recalcule_la_surface_sans_toucher_au_cadastre(client, user_exploitation):
    user, exploitation = user_exploitation
    parcelle = Parcelle.objects.create(
        exploitation=exploitation, name="Nord", area=1.0, official_area_ha=1.0)
    client.force_login(user)

    resp = _post_contour(client, parcelle, _carre())
    assert resp.status_code == 200
    parcelle.refresh_from_db()
    assert parcelle.area == pytest.approx(4.0, abs=0.1)
    assert parcelle.boundaries["type"] == "Polygon"
    # La surface cadastrale reste la référence administrative.
    assert parcelle.official_area_ha == 1.0


@pytest.mark.django_db
def test_contour_refuse_un_trace_degenere(client, user_exploitation):
    user, exploitation = user_exploitation
    parcelle = Parcelle.objects.create(exploitation=exploitation, name="Nord", area=1.0)
    client.force_login(user)

    resp = _post_contour(client, parcelle, [[4.8, 43.9], [4.81, 43.9], [4.8, 43.9]])
    assert resp.status_code == 400
    parcelle.refresh_from_db()
    assert parcelle.area == 1.0
    assert parcelle.boundaries is None


@pytest.mark.django_db
def test_contour_est_isole_par_exploitation(client, user_exploitation):
    user, _exploitation = user_exploitation
    autre = User.objects.create_user(email="d@ex.com", password="pwd12345")
    autre_exp = Exploitation.objects.create(owner=autre, name="Ferme D")
    parcelle = Parcelle.objects.create(exploitation=autre_exp, name="Voisine", area=3.0)

    client.force_login(user)
    assert _post_contour(client, parcelle, _carre()).status_code == 404
    parcelle.refresh_from_db()
    assert parcelle.area == 3.0


# ── Sens des rangs ──────────────────────────────────────────────────────────
def _post_orientation(client, parcelle, deg):
    return client.post(
        reverse("parcelles:orientation", args=[parcelle.pk]),
        data={"deg": deg},
        content_type="application/json",
    )


@pytest.mark.django_db
def test_orientation_enregistre_un_azimut_normalise(client, user_exploitation):
    user, exploitation = user_exploitation
    parcelle = Parcelle.objects.create(exploitation=exploitation, name="Nord", area=2)
    client.force_login(user)

    assert _post_orientation(client, parcelle, 135).json()["orientation"] == 135
    # Au-delà d'un tour complet, l'azimut revient dans [0, 360[.
    assert _post_orientation(client, parcelle, 400).json()["orientation"] == 40
    parcelle.refresh_from_db()
    assert parcelle.orientation_rangs_deg == 40


@pytest.mark.django_db
def test_orientation_effacable_et_refuse_les_valeurs_absurdes(client, user_exploitation):
    user, exploitation = user_exploitation
    parcelle = Parcelle.objects.create(
        exploitation=exploitation, name="Nord", area=2, orientation_rangs_deg=90)
    client.force_login(user)

    assert _post_orientation(client, parcelle, "nord-est").status_code == 400
    parcelle.refresh_from_db()
    assert parcelle.orientation_rangs_deg == 90

    assert _post_orientation(client, parcelle, None).json()["orientation"] is None
    parcelle.refresh_from_db()
    assert parcelle.orientation_rangs_deg is None


@pytest.mark.django_db
def test_orientation_est_isolee_par_exploitation(client, user_exploitation):
    user, _exploitation = user_exploitation
    autre = User.objects.create_user(email="e@ex.com", password="pwd12345")
    autre_exp = Exploitation.objects.create(owner=autre, name="Ferme E")
    parcelle = Parcelle.objects.create(exploitation=autre_exp, name="Voisine", area=3)

    client.force_login(user)
    assert _post_orientation(client, parcelle, 90).status_code == 404
    parcelle.refresh_from_db()
    assert parcelle.orientation_rangs_deg is None


@pytest.mark.django_db
def test_orientation_exposee_dans_le_geojson_de_la_carte(client, user_exploitation):
    user, exploitation = user_exploitation
    Parcelle.objects.create(
        exploitation=exploitation, name="Nord", area=2, orientation_rangs_deg=200,
        boundaries={"type": "Polygon", "coordinates": [_carre()]})
    client.force_login(user)

    resp = client.get(reverse("parcelles:list"))
    feature = resp.context["parcelles_geojson"]["features"][0]
    assert feature["properties"]["orientation"] == 200


@pytest.mark.django_db
def test_la_campagne_se_choisit_sur_la_carte(client, user_exploitation):
    """Des parcelles se reconnaissent à leur forme : la carte accompagne les pastilles."""
    user, exploitation = user_exploitation
    contour = {"type": "Polygon", "coordinates": [[[6.2, 44.1], [6.3, 44.1],
                                                   [6.3, 44.2], [6.2, 44.2], [6.2, 44.1]]]}
    tracee = Parcelle.objects.create(exploitation=exploitation, name="Les Coteaux",
                                     boundaries=contour)
    Parcelle.objects.create(exploitation=exploitation, name="Sans contour")

    client.force_login(user)
    page = client.get(reverse("parcelles:campagne_new")).content.decode()

    # La carte est là, avec le contour de la parcelle tracée.
    assert 'id="hs-parcelles-map"' in page
    assert '"name": "Les Coteaux"' in page or "Les Coteaux" in page
    # Les deux parcelles restent choisissables en pastille, tracées ou non.
    assert "Sans contour" in page
    assert f"toggleParcelle('{tracee.pk}')" in page
    # Et l'on prévient que la seconde ne s'y verra pas.
    assert "apparaissent pas sur la carte" in page
    # Le choix est multiple : le formulaire envoie une liste de parcelles.
    assert 'name="parcelles"' in page


@pytest.mark.django_db
def test_une_campagne_se_seme_sur_plusieurs_parcelles(client, user_exploitation):
    """La même culture, sur toutes les parcelles choisies d'un coup."""
    from parcelles.models import ParcelleCampagne

    user, exploitation = user_exploitation
    nord = Parcelle.objects.create(exploitation=exploitation, name="Nord", area=3)
    sud = Parcelle.objects.create(exploitation=exploitation, name="Sud", area=2)
    Parcelle.objects.create(exploitation=exploitation, name="Est", area=1)

    client.force_login(user)
    reponse = client.post(reverse("parcelles:campagne_new"), {
        "parcelles": [nord.pk, sud.pk], "libelle": "2025/2026",
        "culture": "Blé tendre", "kc_value": 1.0, "type_agriculture": "conversion",
    })

    assert reponse.status_code == 302
    semees = ParcelleCampagne.objects.filter(libelle="2025/2026")
    assert {c.parcelle.name for c in semees} == {"Nord", "Sud"}
    assert {c.culture for c in semees} == {"Blé tendre"}
    # Le type d'agriculture appartient à la parcelle : chacune le reçoit.
    nord.refresh_from_db(); sud.refresh_from_db()
    assert nord.type_agriculture == sud.type_agriculture == "conversion"


@pytest.mark.django_db
def test_une_parcelle_deja_semee_est_passee_sans_bloquer_les_autres(client, user_exploitation):
    """Semer sur ce qui est libre vaut mieux que tout refuser."""
    from parcelles.models import ParcelleCampagne

    user, exploitation = user_exploitation
    deja = Parcelle.objects.create(exploitation=exploitation, name="Nord", area=3)
    libre = Parcelle.objects.create(exploitation=exploitation, name="Sud", area=2)
    ParcelleCampagne.objects.create(parcelle=deja, libelle="2025/2026", culture="Vigne")

    client.force_login(user)
    reponse = client.post(reverse("parcelles:campagne_new"), {
        "parcelles": [deja.pk, libre.pk], "libelle": "2025/2026",
        "culture": "Blé tendre", "kc_value": 1.0,
    }, follow=True)

    # La libre est semée, l'autre gardée telle quelle, et on le dit.
    assert ParcelleCampagne.objects.get(parcelle=libre).culture == "Blé tendre"
    assert ParcelleCampagne.objects.get(parcelle=deja).culture == "Vigne"
    assert "Nord" in reponse.content.decode()


@pytest.mark.django_db
def test_toutes_deja_semees_ne_cree_rien(client, user_exploitation):
    from parcelles.models import ParcelleCampagne

    user, exploitation = user_exploitation
    parcelle = Parcelle.objects.create(exploitation=exploitation, name="Nord", area=3)
    ParcelleCampagne.objects.create(parcelle=parcelle, libelle="2025/2026", culture="Vigne")

    client.force_login(user)
    reponse = client.post(reverse("parcelles:campagne_new"), {
        "parcelles": [parcelle.pk], "libelle": "2025/2026", "culture": "Blé", "kc_value": 1.0,
    })

    assert reponse.status_code == 200  # on reste au formulaire
    assert ParcelleCampagne.objects.count() == 1
    assert ParcelleCampagne.objects.get().culture == "Vigne"


# --- Rendements et heures sur la fiche parcelle ------------------------------------------------

@pytest.fixture
def parcelle_travaillee(user_exploitation):
    """Une parcelle de 2 ha, deux campagnes de récolte et quelques heures de travail."""
    from datetime import datetime, timedelta

    from django.utils import timezone

    from equipe.models import TeamMember
    from finances.models import Recolte
    from interventions.models import Intervention
    from operations.models import AffectationEngin, Machine

    user, exploitation = user_exploitation
    parcelle = Parcelle.objects.create(exploitation=exploitation, name="Coteau", area=2.0)
    aware = lambda *a: timezone.make_aware(datetime(*a))  # noqa: E731

    # Campagne 2025/2026 (septembre → septembre) : 3 000 kg, dont 1 000 kg en extra.
    Recolte.objects.create(exploitation=exploitation, parcelle=parcelle, date=aware(2025, 10, 5),
                           quantite_kg=2000, qualite=Recolte.Qualite.CAT1, prix_unitaire=1.5)
    Recolte.objects.create(exploitation=exploitation, parcelle=parcelle, date=aware(2026, 6, 20),
                           quantite_kg=1000, qualite=Recolte.Qualite.EXTRA, prix_unitaire=2.0)
    # Campagne précédente : 2 000 kg.
    Recolte.objects.create(exploitation=exploitation, parcelle=parcelle, date=aware(2025, 5, 12),
                           quantite_kg=2000, qualite=Recolte.Qualite.CAT1, prix_unitaire=1.0)

    marie = TeamMember.objects.create(exploitation=exploitation, name="Marie")
    for heures, type_, statut in ((6, "taille", "terminee"), (4, "recolte", "terminee"), (3, "recolte", "en_cours")):
        Intervention.objects.create(exploitation=exploitation, parcelle=parcelle, assigned_to=marie,
                                    intervention_type=type_, status=statut, start_time=aware(2026, 3, 2),
                                    duration_hours=heures, cost=heures * 20)
    # Annulée et sans durée : ni l'une ni l'autre ne doit gonfler le total.
    Intervention.objects.create(exploitation=exploitation, parcelle=parcelle, intervention_type="taille",
                                status="annulee", start_time=aware(2026, 3, 3), duration_hours=99)
    Intervention.objects.create(exploitation=exploitation, parcelle=parcelle, intervention_type="observation",
                                status="terminee", start_time=aware(2026, 3, 4))

    tracteur = Machine.objects.create(exploitation=exploitation, name="Tracteur")
    AffectationEngin.objects.create(exploitation=exploitation, machine=tracteur, parcelle=parcelle,
                                    operation="labour", date_debut=aware(2026, 2, 1) + timedelta(hours=1),
                                    heures_utilisees=5)
    return user, parcelle


@pytest.mark.django_db
def test_rendements_par_campagne(parcelle_travaillee):
    from finances.services import rendements_par_parcelle

    _, parcelle = parcelle_travaillee
    resultat = rendements_par_parcelle(parcelle)
    courante, precedente = resultat["campagnes"][0], resultat["campagnes"][1]
    assert courante["libelle"] == "2025/2026"
    assert courante["kg"] == 3000
    assert courante["rendement"] == 1500  # 3 000 kg sur 2 ha
    assert courante["valorisation"] == 5000  # 2 000 × 1,5 + 1 000 × 2
    assert precedente["libelle"] == "2024/2025"
    assert resultat["variation"] == 50  # 3 000 kg contre 2 000 la campagne d'avant
    assert {q["libelle"]: q["part"] for q in courante["qualites"]} == {"Catégorie 1": 67, "Extra": 33}


@pytest.mark.django_db
def test_rendement_vide_sans_surface(user_exploitation):
    from datetime import datetime

    from django.utils import timezone

    from finances.models import Recolte
    from finances.services import rendements_par_parcelle

    user, exploitation = user_exploitation
    parcelle = Parcelle.objects.create(exploitation=exploitation, name="Sans surface", area=None)
    Recolte.objects.create(exploitation=exploitation, parcelle=parcelle,
                           date=timezone.make_aware(datetime(2026, 5, 1)), quantite_kg=500)
    resultat = rendements_par_parcelle(parcelle)
    assert resultat["campagnes"][0]["kg"] == 500
    assert resultat["campagnes"][0]["rendement"] is None  # pas de kg/ha inventé


@pytest.mark.django_db
def test_heures_effectuees(parcelle_travaillee):
    from interventions.services import heures_par_parcelle
    from operations.services import heures_engins_par_parcelle

    _, parcelle = parcelle_travaillee
    heures = heures_par_parcelle(parcelle)
    assert heures["total"] == 13  # 6 + 4 + 3 ; l'annulée et celle sans durée sont écartées
    assert heures["par_hectare"] == 6.5
    assert heures["sans_duree"] == 1
    assert heures["cout"] == 260
    assert [t["libelle"] for t in heures["par_type"]] == ["Récolte", "Taille"]  # du plus long au plus court
    assert heures["par_personne"][0] == {"libelle": "Marie", "heures": 13.0, "nombre": 3}
    assert heures_engins_par_parcelle(parcelle)["total"] == 5


@pytest.mark.django_db
def test_fiche_parcelle_affiche_rendements_et_heures(client, parcelle_travaillee):
    user, parcelle = parcelle_travaillee
    client.force_login(user)
    resp = client.get(reverse("parcelles:edit", args=[parcelle.pk]))
    assert resp.status_code == 200
    assert resp.context["rendements"]["courante"]["rendement"] == 1500
    assert resp.context["heures"]["total"] == 13
    assert resp.context["fruit"]["kg_par_heure"] == 384.6  # 5 000 kg récoltés pour 13 heures
    contenu = resp.content.decode()
    for attendu in ("Rendements", "Heures effectuées", "Fruit du travail", "1500", "13", "Marie"):
        assert attendu in contenu


@pytest.mark.django_db
def test_fiche_parcelle_sans_donnees(client, user_exploitation):
    user, exploitation = user_exploitation
    parcelle = Parcelle.objects.create(exploitation=exploitation, name="Neuve", area=1)
    client.force_login(user)
    resp = client.get(reverse("parcelles:edit", args=[parcelle.pk]))
    assert resp.status_code == 200
    assert resp.context["rendements"]["campagnes"] == []
    assert resp.context["heures"]["total"] == 0
    assert "Aucune récolte enregistrée" in resp.content.decode()
