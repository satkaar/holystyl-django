from django.urls import path

from . import views

app_name = "environnement"

urlpatterns = [
    path("environnement/biodiversite/", views.biodiversite, name="biodiversite"),
    path("environnement/biodiversite/nouvelle/", views.biodiversite_create, name="biodiversite_create"),
    path("environnement/biodiversite/<int:pk>/modifier/", views.biodiversite_edit, name="biodiversite_edit"),
    path("environnement/biodiversite/<int:pk>/supprimer/", views.biodiversite_delete, name="biodiversite_delete"),
    path("environnement/bilan-eau/", views.bilan_eau, name="bilan_eau"),
    path("environnement/bilan-eau/export/", views.bilan_eau_export, name="bilan_eau_export"),
    path("environnement/bilan-azote/", views.bilan_azote, name="bilan_azote"),
    path("environnement/bilan-azote/apport/", views.apport_azote_save, name="apport_azote_create"),
    path("environnement/bilan-azote/apport/<int:pk>/", views.apport_azote_save, name="apport_azote_edit"),
    path("environnement/bilan-azote/apport/<int:pk>/supprimer/", views.apport_azote_delete, name="apport_azote_delete"),
    path("environnement/bilan-azote/cahier-epandage/", views.cahier_epandage, name="cahier_epandage"),
    path("environnement/empreinte-carbone/", views.empreinte_carbone, name="empreinte_carbone"),
    path("environnement/empreinte-carbone/poste/", views.poste_carbone_save, name="poste_carbone_create"),
    path("environnement/empreinte-carbone/poste/<int:pk>/", views.poste_carbone_save, name="poste_carbone_edit"),
    path("environnement/empreinte-carbone/poste/<int:pk>/supprimer/", views.poste_carbone_delete, name="poste_carbone_delete"),
    path("environnement/rapport/", views.rapport_environnemental, name="rapport"),
    path("environnement/rapport/pdf/", views.rapport_environnemental_pdf, name="rapport_pdf"),
    path("environnement/sante-vegetale/", views.sante_vegetale, name="sante_vegetale"),
    path("environnement/sante-vegetale/registre/", views.registre_phyto, name="registre_phyto"),
    path("environnement/sante-vegetale/traitement/", views.traitement_save, name="traitement_create"),
    path("environnement/sante-vegetale/traitement/<int:pk>/", views.traitement_save, name="traitement_edit"),
    path("environnement/sante-vegetale/traitement/<int:pk>/supprimer/", views.traitement_delete, name="traitement_delete"),
    path("environnement/sante-vegetale/observation/", views.observation_save, name="observation_create"),
    path("environnement/sante-vegetale/observation/<int:pk>/", views.observation_save, name="observation_edit"),
    path("environnement/sante-vegetale/observation/<int:pk>/supprimer/", views.observation_delete, name="observation_delete"),
    path("environnement/taxonomie/", views.taxonomie, name="taxonomie"),
    path("environnement/taxonomie/ajouter/", views.taxonomie_create, name="taxonomie_create"),
    path("environnement/taxonomie/<int:pk>/supprimer/", views.taxonomie_delete, name="taxonomie_delete"),
]
