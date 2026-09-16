from django.urls import path

from . import views

app_name = "parcelles"

urlpatterns = [
    path("parcelles/", views.parcelle_list, name="list"),
    path("parcelles/<int:pk>/teledetection/", views.parcelle_teledetection, name="teledetection"),
    path("parcelles/cadastre/", views.parcelle_cadastre, name="cadastre"),
    path("parcelles/cadastre/enregistrer/", views.parcelle_cadastre_save, name="cadastre_save"),
    path("parcelles/nouvelle/", views.parcelle_create, name="create"),
    path("parcelles/<int:pk>/", views.parcelle_detail, name="detail"),
    path("parcelles/<int:pk>/contour/", views.parcelle_contour, name="contour"),
    path("parcelles/<int:pk>/orientation/", views.parcelle_orientation, name="orientation"),
    path("parcelles/<int:pk>/modifier/", views.parcelle_edit, name="edit"),
    path("parcelles/<int:pk>/supprimer/", views.parcelle_delete, name="delete"),
    # Récoltes et heures : saisies depuis la parcelle, écrites dans finances et interventions.
    path("parcelles/<int:parcelle_pk>/recolte/", views.recolte_save, name="recolte_create"),
    path("parcelles/<int:parcelle_pk>/recolte/<int:pk>/", views.recolte_save, name="recolte_edit"),
    path("parcelles/<int:parcelle_pk>/recolte/<int:pk>/supprimer/", views.recolte_delete, name="recolte_delete"),
    path("parcelles/<int:parcelle_pk>/heures/", views.heures_save, name="heures_create"),
    path("parcelles/<int:parcelle_pk>/heures/<int:pk>/", views.heures_save, name="heures_edit"),
    path("parcelles/<int:parcelle_pk>/heures/<int:pk>/supprimer/", views.heures_delete, name="heures_delete"),
    path("campagnes/", views.campagne_list, name="campagnes"),
    path("campagnes/nouvelle/", views.campagne_new, name="campagne_new"),
    path("parcelles/<int:parcelle_pk>/campagnes/nouvelle/", views.campagne_create, name="campagne_create"),
    path("campagnes/<int:pk>/modifier/", views.campagne_edit, name="campagne_edit"),
    path("campagnes/<int:pk>/supprimer/", views.campagne_delete, name="campagne_delete"),
]
