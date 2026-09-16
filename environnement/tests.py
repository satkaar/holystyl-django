"""Tests Environnement : bilan azoté, empreinte carbone, rapport et santé végétale."""

import datetime

import pytest
from django.contrib.auth import get_user_model

from agronomie.models import Fertigation
from environnement import azote as azote_service
from environnement.models import ApportAzote
from exploitations.models import Exploitation
from parcelles.models import Parcelle

User = get_user_model()


@pytest.fixture
def ferme(db):
    user = User.objects.create_user(email="azote@ex.com", password="pwd12345")
    exploitation = Exploitation.objects.create(owner=user, name="Ferme des Prés")
    grande = Parcelle.objects.create(exploitation=exploitation, name="Grand Champ", area=10)
    petite = Parcelle.objects.create(exploitation=exploitation, name="Le Clos", area=5)
    return user, exploitation, grande, petite


def _apport(exploitation, parcelle, **surcharges):
    donnees = {
        "date": datetime.date(2026, 3, 10),
        "nature": ApportAzote.Nature.MINERAL,
        "dose_kg_ha": 200,
        "teneur_n_pct": 33.5,
    }
    donnees.update(surcharges)
    return ApportAzote.objects.create(exploitation=exploitation, parcelle=parcelle, **donnees)


@pytest.mark.django_db
def test_l_azote_se_calcule_de_la_dose_et_de_la_teneur(ferme):
    """200 kg/ha d'un engrais à 33,5 % font 67 kg N/ha, et 670 kg sur 10 ha."""
    _user, exploitation, grande, _petite = ferme

    apport = _apport(exploitation, grande)

    assert apport.n_kg_ha == 67.0
    assert apport.surface_ha == 10  # reprise de la parcelle
    assert apport.n_total == 670.0
    # Mars 2026 relève de la campagne ouverte en septembre 2025.
    assert apport.campagne == "2025/2026"


@pytest.mark.django_db
def test_le_plafond_ne_compte_que_les_effluents_d_elevage(ferme):
    """Le minéral pèse dans le bilan, jamais dans le plafond des 170 kg."""
    _user, exploitation, grande, petite = ferme
    _apport(exploitation, grande, dose_kg_ha=1000, teneur_n_pct=50)  # 500 kg N/ha minéral
    _apport(exploitation, petite, nature=ApportAzote.Nature.ORGANIQUE_ELEVAGE,
            dose_kg_ha=20000, teneur_n_pct=0.5)  # 100 kg N/ha sur 5 ha = 500 kg N

    bilan = azote_service.bilan(exploitation, "2025/2026")

    assert bilan["n_total"] == 5500.0
    assert bilan["n_effluents"] == 500.0
    assert bilan["sau"] == 15
    # 500 kg d'effluents sur 15 ha de SAU : très en dessous des 170.
    assert bilan["effluents_ha"] == 33.3
    assert bilan["depasse"] is False


@pytest.mark.django_db
def test_un_depassement_du_plafond_se_voit(ferme):
    _user, exploitation, grande, petite = ferme
    for parcelle in (grande, petite):
        _apport(exploitation, parcelle, nature=ApportAzote.Nature.ORGANIQUE_ELEVAGE,
                dose_kg_ha=40000, teneur_n_pct=0.5)  # 200 kg N/ha

    bilan = azote_service.bilan(exploitation, "2025/2026")

    assert bilan["effluents_ha"] == 200.0
    assert bilan["depasse"] is True
    assert bilan["pct_plafond"] == 100  # la jauge sature, elle ne déborde pas
    assert all(p["depasse"] for p in bilan["par_parcelle"])


@pytest.mark.django_db
def test_les_fertigations_comptent_sans_etre_ressaisies(ferme):
    """Elles sont déjà enregistrées dans Agronomie : le bilan les reprend."""
    _user, exploitation, grande, _petite = ferme
    Fertigation.objects.create(
        exploitation=exploitation, parcelle=grande,
        date=datetime.datetime(2026, 4, 2, 8, 0, tzinfo=datetime.timezone.utc),
        produit="Solution azotée", azote_n=30)

    bilan = azote_service.bilan(exploitation, "2025/2026")

    ligne = bilan["lignes"][0]
    assert ligne["origine"] == "fertigation"
    assert ligne["n_kg_ha"] == 30 and ligne["n_total"] == 300.0
    assert bilan["n_mineral"] == 300.0
    # Une fertigation n'est pas un effluent : elle ne pousse pas le plafond.
    assert bilan["n_effluents"] == 0


@pytest.mark.django_db
def test_chaque_campagne_a_son_bilan(ferme):
    _user, exploitation, grande, _petite = ferme
    _apport(exploitation, grande, date=datetime.date(2025, 10, 1))  # campagne 2025/2026
    _apport(exploitation, grande, date=datetime.date(2026, 9, 15))  # campagne 2026/2027

    assert azote_service.bilan(exploitation, "2025/2026")["n_total"] == 670.0
    assert azote_service.bilan(exploitation, "2026/2027")["n_total"] == 670.0
    assert "2026/2027" in azote_service.campagnes(exploitation)


@pytest.mark.django_db
def test_la_page_enregistre_modifie_et_supprime_un_apport(client, ferme):
    user, exploitation, grande, _petite = ferme
    client.force_login(user)

    reponse = client.post("/environnement/bilan-azote/apport/", {
        "parcelle": str(grande.pk), "date": "2026-03-10", "nature": "organique_elevage",
        "produit": "Fumier de bovins", "dose_kg_ha": "20000", "teneur_n_pct": "0,5",
        "surface_ha": "8", "notes": "Épandu au fumier à fumier."})

    assert reponse.status_code == 302 and "campagne=2025/2026" in reponse["Location"]
    apport = ApportAzote.objects.get()
    assert apport.n_kg_ha == 100.0 and apport.surface_ha == 8 and apport.n_total == 800.0

    client.post(f"/environnement/bilan-azote/apport/{apport.pk}/", {
        "parcelle": str(grande.pk), "date": "2026-03-10", "nature": "mineral",
        "dose_kg_ha": "100", "teneur_n_pct": "27"})
    apport.refresh_from_db()
    assert apport.nature == "mineral" and apport.n_kg_ha == 27.0

    client.post(f"/environnement/bilan-azote/apport/{apport.pk}/supprimer/")
    assert not ApportAzote.objects.exists()


@pytest.mark.django_db
def test_l_apport_du_voisin_ne_se_touche_pas(client, ferme):
    user, _exploitation, _grande, _petite = ferme
    voisin = User.objects.create_user(email="voisin-azote@ex.com", password="pwd12345")
    ailleurs = Exploitation.objects.create(owner=voisin, name="Ferme d'en face")
    sa_parcelle = Parcelle.objects.create(exploitation=ailleurs, name="Sa parcelle", area=3)
    son_apport = _apport(ailleurs, sa_parcelle)

    client.force_login(user)
    assert client.post(f"/environnement/bilan-azote/apport/{son_apport.pk}/supprimer/").status_code == 404
    page = client.get("/environnement/bilan-azote/").content.decode()
    assert "Sa parcelle" not in page


@pytest.mark.django_db
def test_le_cahier_d_epandage_s_edite(client, ferme):
    user, exploitation, grande, _petite = ferme
    _apport(exploitation, grande, nature=ApportAzote.Nature.ORGANIQUE_ELEVAGE,
            produit="Fumier de bovins", dose_kg_ha=20000, teneur_n_pct=0.5)
    client.force_login(user)

    reponse = client.get("/environnement/bilan-azote/cahier-epandage/?campagne=2025/2026")

    assert reponse.status_code == 200
    if reponse["Content-Type"] == "application/pdf":
        assert reponse["Content-Disposition"].startswith("inline;")
    else:  # WeasyPrint absent : on rend la page imprimable
        corps = reponse.content.decode()
        assert "Cahier d'épandage" in corps
        assert "Fumier de bovins" in corps and "Ferme des Prés" in corps
        assert "{{" not in corps


# ── Empreinte carbone ──

@pytest.mark.django_db
def test_le_poste_multiplie_la_quantite_par_le_facteur(ferme):
    from environnement.models import PosteCarbone

    _user, exploitation, _grande, _petite = ferme

    poste = PosteCarbone.objects.create(
        exploitation=exploitation, campagne="2025/2026", poste=PosteCarbone.Poste.CHEPTEL,
        libelle="Vaches laitières", quantite=40, unite="tête", facteur=3200,
        source_facteur="Diagnostic CAP'2ER 2026")

    assert poste.emissions_kg == 128000.0
    assert poste.est_stockage is False


@pytest.mark.django_db
def test_le_stockage_se_retranche_des_emissions(ferme):
    from environnement.carbone import bilan
    from environnement.models import PosteCarbone

    _user, exploitation, _grande, _petite = ferme
    PosteCarbone.objects.create(exploitation=exploitation, campagne="2025/2026",
                                poste=PosteCarbone.Poste.CARBURANT, libelle="GNR",
                                quantite=1000, unite="L", facteur=3.17)
    PosteCarbone.objects.create(exploitation=exploitation, campagne="2025/2026",
                                poste=PosteCarbone.Poste.STOCKAGE, libelle="Haies",
                                quantite=2000, unite="ml", facteur=0.5)

    resultat = bilan(exploitation, "2025/2026")

    assert resultat["emissions"] == 3170.0
    assert resultat["stockage"] == 1000.0
    assert resultat["net"] == 2170.0
    # 3170 kg sur 15 ha de SAU.
    assert resultat["emissions_ha"] == 211.3


@pytest.mark.django_db
def test_l_electricite_le_carburant_et_l_azote_se_comptent_seuls(ferme):
    """Ce que l'application sait déjà n'a pas à être ressaisi."""
    import datetime as dt

    from irrigation.models import EnergyLog
    from stock.models import Article, Mouvement

    from environnement.carbone import bilan

    _user, exploitation, grande, _petite = ferme
    EnergyLog.objects.create(exploitation=exploitation, energy_kwh=1000,
                             log_date=dt.datetime(2026, 5, 1, 10, tzinfo=dt.timezone.utc))
    cuve = Article.objects.create(exploitation=exploitation, nom="GNR",
                                  categorie=Article.Categorie.CARBURANT, unite="l", quantite=5000)
    Mouvement.objects.create(exploitation=exploitation, article=cuve,
                             type_mouvement=Mouvement.Type.SORTIE, quantite=800,
                             date=dt.datetime(2026, 5, 2, 8, tzinfo=dt.timezone.utc))
    _apport(exploitation, grande, dose_kg_ha=100, teneur_n_pct=27)  # 27 kg N/ha sur 10 ha

    resultat = bilan(exploitation, "2025/2026")
    postes = {l["poste"]: l for l in resultat["lignes"]}

    assert postes["electricite"]["quantite"] == 1000 and postes["electricite"]["emissions_kg"] == 60.0
    assert postes["carburant"]["quantite"] == 800 and postes["carburant"]["emissions_kg"] == 2536.0
    assert postes["engrais"]["quantite"] == 270.0 and postes["engrais"]["emissions_kg"] == 2160.0
    assert all(l["origine"] != "saisi" for l in resultat["lignes"])


@pytest.mark.django_db
def test_un_poste_sans_facteur_est_signale(ferme):
    """Sans facteur, la ligne ne pèse rien : le bilan doit le dire."""
    from environnement.carbone import bilan
    from environnement.models import PosteCarbone

    _user, exploitation, _grande, _petite = ferme
    PosteCarbone.objects.create(exploitation=exploitation, campagne="2025/2026",
                                poste=PosteCarbone.Poste.CHEPTEL, libelle="Troupeau allaitant",
                                quantite=60, unite="tête", facteur=0)

    resultat = bilan(exploitation, "2025/2026")

    assert resultat["emissions"] == 0
    assert [l["libelle"] for l in resultat["sans_facteur"]] == ["Troupeau allaitant"]


@pytest.mark.django_db
def test_la_page_carbone_enregistre_et_supprime_un_poste(client, ferme):
    from environnement.models import PosteCarbone

    user, exploitation, _grande, _petite = ferme
    client.force_login(user)

    reponse = client.post("/environnement/empreinte-carbone/poste/", {
        "campagne": "2025/2026", "poste": "aliments", "libelle": "Tourteau de colza",
        "quantite": "12", "unite": "t", "facteur": "0,6", "source_facteur": "Diagnostic 2026"})

    assert reponse.status_code == 302
    poste = PosteCarbone.objects.get()
    assert poste.emissions_kg == 7.2 and poste.source_facteur == "Diagnostic 2026"

    client.post(f"/environnement/empreinte-carbone/poste/{poste.pk}/supprimer/")
    assert not PosteCarbone.objects.exists()


@pytest.mark.django_db
def test_le_poste_carbone_du_voisin_ne_se_touche_pas(client, ferme):
    from environnement.models import PosteCarbone

    user, _exploitation, _grande, _petite = ferme
    voisin = User.objects.create_user(email="voisin-carbone@ex.com", password="pwd12345")
    ailleurs = Exploitation.objects.create(owner=voisin, name="Ferme d'en face")
    le_sien = PosteCarbone.objects.create(exploitation=ailleurs, campagne="2025/2026",
                                          poste=PosteCarbone.Poste.CARBURANT,
                                          libelle="Son GNR", quantite=10, facteur=3)

    client.force_login(user)
    assert client.post(f"/environnement/empreinte-carbone/poste/{le_sien.pk}/supprimer/").status_code == 404
    assert "Son GNR" not in client.get("/environnement/empreinte-carbone/").content.decode()


# ── Rapport environnemental ──

@pytest.mark.django_db
def test_le_rapport_rassemble_les_rubriques_de_la_campagne(ferme):
    import datetime as dt

    from irrigation.models import IrrigationSession

    from environnement.models import Biodiversite, PosteCarbone
    from environnement.rapport import rubriques_manquantes, synthese

    _user, exploitation, grande, _petite = ferme
    IrrigationSession.objects.create(
        exploitation=exploitation, parcelle=grande, volume_delivered_m3=1500, energy_kwh=450,
        start_time=dt.datetime(2026, 6, 1, 6, tzinfo=dt.timezone.utc))
    _apport(exploitation, grande, nature=ApportAzote.Nature.ORGANIQUE_ELEVAGE,
            dose_kg_ha=20000, teneur_n_pct=0.5)
    PosteCarbone.objects.create(exploitation=exploitation, campagne="2025/2026",
                                poste=PosteCarbone.Poste.CARBURANT, libelle="GNR",
                                quantite=1000, unite="L", facteur=3.17)
    Biodiversite.objects.create(exploitation=exploitation, parcelle=grande,
                                date=dt.date(2026, 5, 20), score=64, haies_ml=800)

    synth = synthese(exploitation, "2025/2026")

    assert synth["sau"] == 15 and synth["parcelles"] == 2
    assert synth["eau"]["volume_m3"] == 1500 and synth["eau"]["sessions"] == 1
    assert synth["eau"]["kwh_par_m3"] == 0.3
    assert synth["azote"]["n_effluents"] == 1000.0
    # 1 000 L de GNR (3 170 kg) plus les 450 kWh de la session (27 kg).
    assert synth["carbone"]["emissions"] == 3197.0
    assert synth["biodiversite"]["score_moyen"] == 64
    # Seule la Taxonomie reste vide : le rapport le dit plutôt que d'afficher 0.
    assert rubriques_manquantes(synth) == ["Taxonomie EU"]


@pytest.mark.django_db
def test_une_rubrique_vide_se_signale(ferme):
    from environnement.rapport import rubriques_manquantes, synthese

    _user, exploitation, _grande, _petite = ferme

    synth = synthese(exploitation, "2025/2026")

    assert synth["eau"]["renseigne"] is False
    assert synth["azote"]["renseigne"] is False
    assert len(rubriques_manquantes(synth)) == 5


@pytest.mark.django_db
def test_le_rapport_s_edite_en_pdf(client, ferme):
    user, exploitation, grande, _petite = ferme
    _apport(exploitation, grande, produit="Ammonitrate")
    client.force_login(user)

    reponse = client.get("/environnement/rapport/pdf/?campagne=2025/2026")

    assert reponse.status_code == 200
    if reponse["Content-Type"] == "application/pdf":
        assert reponse["Content-Disposition"].startswith("inline;")
    else:  # WeasyPrint absent : on rend la page imprimable
        corps = reponse.content.decode()
        assert "Rapport environnemental" in corps and "Ferme des Prés" in corps
        assert "Non renseigné sur cette campagne." in corps  # les rubriques vides
        assert "{{" not in corps


@pytest.mark.django_db
def test_le_rapport_du_voisin_n_apparait_pas(client, ferme):
    import datetime as dt

    from environnement.models import Biodiversite

    user, _exploitation, _grande, _petite = ferme
    voisin = User.objects.create_user(email="voisin-rapport@ex.com", password="pwd12345")
    ailleurs = Exploitation.objects.create(owner=voisin, name="Ferme d'en face")
    sa_parcelle = Parcelle.objects.create(exploitation=ailleurs, name="Sa parcelle", area=40)
    Biodiversite.objects.create(exploitation=ailleurs, parcelle=sa_parcelle,
                                date=dt.date(2026, 5, 20), score=90)

    client.force_login(user)
    page = client.get("/environnement/rapport/?campagne=2025/2026")

    assert page.context["synthese"]["sau"] == 15  # la sienne, pas 55
    assert page.context["synthese"]["biodiversite"]["renseigne"] is False


@pytest.mark.django_db
def test_l_electricite_ne_se_compte_pas_deux_fois(ferme):
    """Compteur et session portent la même énergie : le compteur fait foi."""
    import datetime as dt

    from irrigation.models import EnergyLog, IrrigationSession

    from environnement.carbone import energie_kwh

    _user, exploitation, grande, _petite = ferme
    IrrigationSession.objects.create(
        exploitation=exploitation, parcelle=grande, volume_delivered_m3=1000, energy_kwh=400,
        start_time=dt.datetime(2026, 6, 1, 6, tzinfo=dt.timezone.utc))

    # Sans relevé de compteur, la session fait le compte.
    assert energie_kwh(exploitation, "2025/2026") == 400.0

    EnergyLog.objects.create(exploitation=exploitation, energy_kwh=520,
                             log_date=dt.datetime(2026, 6, 1, 7, tzinfo=dt.timezone.utc))

    # Dès qu'il y en a un, il fait foi : 520, et non 920.
    assert energie_kwh(exploitation, "2025/2026") == 520.0


# ── Santé végétale ──

def _traitement(exploitation, parcelle, **surcharges):
    from environnement.models import Traitement

    donnees = {
        "date": datetime.date(2026, 5, 12),
        "produit": "Bouillie bordelaise",
        "numero_amm": "2100024",
        "type_cible": "maladie",
        "cible": "Mildiou",
        "dose": 2.5,
        "unite_dose": "kg/ha",
        "delai_avant_recolte": 21,
    }
    donnees.update(surcharges)
    return Traitement.objects.create(exploitation=exploitation, parcelle=parcelle, **donnees)


@pytest.mark.django_db
def test_le_traitement_deduit_sa_campagne_et_sa_date_de_recolte(ferme):
    _user, exploitation, grande, _petite = ferme

    traitement = _traitement(exploitation, grande)

    assert traitement.campagne == "2025/2026"
    assert traitement.surface_ha == 10  # reprise de la parcelle
    assert traitement.recolte_possible_le == datetime.date(2026, 6, 2)


@pytest.mark.django_db
def test_une_ligne_de_registre_incomplete_est_signalee(ferme):
    """Sans AMM, sans cible ou sans dose, le registre n'est pas en règle."""
    from environnement.sante import bilan

    _user, exploitation, grande, petite = ferme
    _traitement(exploitation, grande)
    _traitement(exploitation, petite, numero_amm="", produit="Soufre")

    resultat = bilan(exploitation, "2025/2026")

    assert resultat["nb_traitements"] == 2
    assert [t["produit"] for t in resultat["incomplets"]] == ["Soufre"]
    assert resultat["surface_traitee"] == 15.0


@pytest.mark.django_db
def test_les_interventions_de_traitement_entrent_au_registre(ferme):
    """Elles y figurent, mais incomplètes : la page ne fait pas semblant."""
    from interventions.models import Intervention

    from environnement.sante import bilan

    _user, exploitation, grande, _petite = ferme
    Intervention.objects.create(
        exploitation=exploitation, parcelle=grande,
        intervention_type=Intervention.Type.TRAITEMENT, product="Soufre mouillable",
        start_time=datetime.datetime(2026, 5, 20, 7, tzinfo=datetime.timezone.utc))

    resultat = bilan(exploitation, "2025/2026")

    ligne = resultat["traitements"][0]
    assert ligne["origine"] == "intervention" and ligne["produit"] == "Soufre mouillable"
    assert ligne["complet"] is False
    assert resultat["incomplets"] == [ligne]


@pytest.mark.django_db
def test_une_observation_forte_remonte_en_alerte(ferme):
    from environnement.models import ObservationSanitaire
    from environnement.sante import bilan

    _user, exploitation, grande, petite = ferme
    ObservationSanitaire.objects.create(exploitation=exploitation, parcelle=grande,
                                        date=datetime.date(2026, 5, 2), nom="Oïdium",
                                        intensite=ObservationSanitaire.Intensite.FORTE)
    ObservationSanitaire.objects.create(exploitation=exploitation, parcelle=petite,
                                        date=datetime.date(2026, 5, 2), nom="Rien",
                                        intensite=ObservationSanitaire.Intensite.NULLE)

    resultat = bilan(exploitation, "2025/2026")

    assert resultat["nb_observations"] == 2
    assert [o.nom for o in resultat["alertes"]] == ["Oïdium"]


@pytest.mark.django_db
def test_la_page_sante_enregistre_et_supprime(client, ferme):
    from environnement.models import ObservationSanitaire, Traitement

    user, _exploitation, grande, _petite = ferme
    client.force_login(user)

    client.post("/environnement/sante-vegetale/traitement/", {
        "parcelle": str(grande.pk), "date": "2026-05-12", "produit": "Bouillie bordelaise",
        "numero_amm": "2100024", "type_cible": "maladie", "cible": "Mildiou",
        "dose": "2,5", "unite_dose": "kg/ha", "surface_ha": "9", "delai_avant_recolte": "21",
        "operateur": "Paul Martin"})
    traitement = Traitement.objects.get()
    assert traitement.dose == 2.5 and traitement.surface_ha == 9 and traitement.campagne == "2025/2026"

    client.post("/environnement/sante-vegetale/observation/", {
        "parcelle": str(grande.pk), "date": "2026-05-02", "type_cible": "ravageur",
        "nom": "Carpocapse", "intensite": "2"})
    observation = ObservationSanitaire.objects.get()
    assert observation.intensite == 2 and observation.campagne == "2025/2026"

    client.post(f"/environnement/sante-vegetale/traitement/{traitement.pk}/supprimer/")
    client.post(f"/environnement/sante-vegetale/observation/{observation.pk}/supprimer/")
    assert not Traitement.objects.exists() and not ObservationSanitaire.objects.exists()


@pytest.mark.django_db
def test_le_registre_du_voisin_ne_se_touche_pas(client, ferme):
    user, _exploitation, _grande, _petite = ferme
    voisin = User.objects.create_user(email="voisin-phyto@ex.com", password="pwd12345")
    ailleurs = Exploitation.objects.create(owner=voisin, name="Ferme d'en face")
    sa_parcelle = Parcelle.objects.create(exploitation=ailleurs, name="Sa parcelle", area=3)
    son_traitement = _traitement(ailleurs, sa_parcelle, produit="Son produit")

    client.force_login(user)
    assert client.post(
        f"/environnement/sante-vegetale/traitement/{son_traitement.pk}/supprimer/").status_code == 404
    assert "Son produit" not in client.get("/environnement/sante-vegetale/").content.decode()


@pytest.mark.django_db
def test_le_registre_phyto_s_edite(client, ferme):
    user, exploitation, grande, petite = ferme
    _traitement(exploitation, grande)
    _traitement(exploitation, petite, numero_amm="", produit="Soufre")
    client.force_login(user)

    reponse = client.get("/environnement/sante-vegetale/registre/?campagne=2025/2026")

    assert reponse.status_code == 200
    if reponse["Content-Type"] == "application/pdf":
        assert reponse["Content-Disposition"].startswith("inline;")
    else:  # WeasyPrint absent : on rend la page imprimable
        corps = reponse.content.decode()
        assert "Registre des traitements phytosanitaires" in corps
        assert "Bouillie bordelaise" in corps and "2100024" in corps
        assert "à compléter" in corps  # la ligne sans AMM
        assert "{{" not in corps
