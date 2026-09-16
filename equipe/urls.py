from django.urls import path

from . import emplois as emplois_public, views

app_name = "equipe"

urlpatterns = [
    path("equipe/", views.equipe, name="equipe"),
    path("equipe/<int:pk>/modifier/", views.membre_edit, name="membre_edit"),
    path("equipe/<int:pk>/supprimer/", views.membre_delete, name="membre_delete"),
    path("equipe/<int:pk>/inviter/", views.membre_inviter, name="membre_inviter"),
    path("equipe/invitation/<str:token>/", views.invitation, name="invitation"),
    path("taches/", views.taches, name="taches"),
    path("taches/<int:pk>/modifier/", views.taches_edit, name="taches_edit"),
    path("taches/<int:pk>/supprimer/", views.taches_delete, name="taches_delete"),
    path("contrats-travail/", views.contrats, name="contrats"),
    path("contrats-travail/modeles/importer/", views.modeles_importer, name="modeles_importer"),
    path("contrats-travail/modeles/enregistrer/", views.modele_save, name="modele_create"),
    path("contrats-travail/modeles/<int:pk>/enregistrer/", views.modele_save, name="modele_edit"),
    path("contrats-travail/modeles/<int:pk>/supprimer/", views.modele_delete, name="modele_delete"),
    path("contrats-travail/modeles/<int:pk>/pdf/", views.modele_pdf, name="modele_pdf"),
    path("contrats-travail/etablir/", views.contrat_create, name="contrat_create"),
    path("contrats-travail/modeles/<int:pk>/etablir/", views.contrat_etablir, name="contrat_etablir"),
    path("contrats-travail/<int:pk>/enregistrer/", views.contrat_edit, name="contrat_edit"),
    path("contrats-travail/<int:pk>/supprimer/", views.contrat_delete, name="contrat_delete"),
    path("contrats-travail/<int:pk>/pdf/", views.contrat_pdf, name="contrat_pdf"),
    path("postes/", views.postes, name="postes"),
    path("postes/importer/", views.postes_importer, name="postes_importer"),
    path("postes/enregistrer/", views.poste_save, name="poste_create"),
    path("postes/<int:pk>/enregistrer/", views.poste_save, name="poste_edit"),
    path("postes/<int:pk>/supprimer/", views.poste_delete, name="poste_delete"),
    path("paie/", views.paie, name="paie"),
    path("paie/enregistrer/", views.fiche_save, name="fiche_create"),
    path("paie/<int:pk>/enregistrer/", views.fiche_save, name="fiche_edit"),
    path("paie/<int:pk>/statut/", views.fiche_statut, name="fiche_statut"),
    path("paie/<int:pk>/supprimer/", views.fiche_delete, name="fiche_delete"),
    path("paie/<int:pk>/pdf/", views.fiche_pdf, name="fiche_pdf"),
    path("localisation/<str:token>/", views.location_share, name="location_share"),

    # ── Offres d'emploi : back-office ──
    path("offres-emploi/", views.offres, name="offres"),
    path("offres-emploi/enregistrer/", views.offre_save, name="offre_create"),
    path("offres-emploi/<int:pk>/enregistrer/", views.offre_save, name="offre_edit"),
    path("offres-emploi/<int:pk>/statut/", views.offre_statut, name="offre_statut"),
    path("offres-emploi/<int:pk>/supprimer/", views.offre_delete, name="offre_delete"),
    path("candidatures/<int:pk>/statut/", views.candidature_statut, name="candidature_statut"),

    # ── Espace public : aucun compte requis ──
    path("emplois/", emplois_public.emplois, name="emplois"),
    path("emplois/<slug:slug>/", emplois_public.emploi_detail, name="emploi_detail"),
    path("emplois/<slug:slug>/candidater/", emplois_public.candidater, name="candidater"),
]
