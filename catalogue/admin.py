"""Administration du référentiel.

Le catalogue est un reflet du Drive : rien ne s'y saisit à la main. L'admin
sert à le consulter et à comprendre ce qu'une synchronisation a fait — d'où
des champs en lecture seule sur tout ce qui vient de la source.
"""

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Categorie, DocumentProduit, Fournisseur, Produit, SynchroDrive


@admin.register(Fournisseur)
class FournisseurAdmin(admin.ModelAdmin):
    list_display = ("nom", "nombre_produits", "dossier_drive")
    search_fields = ("nom",)
    prepopulated_fields = {"slug": ("nom",)}

    @admin.display(description=_("produits"))
    def nombre_produits(self, obj):
        return obj.produits.count()


@admin.register(Categorie)
class CategorieAdmin(admin.ModelAdmin):
    list_display = ("nom", "nombre_produits")
    search_fields = ("nom",)
    prepopulated_fields = {"slug": ("nom",)}

    @admin.display(description=_("produits"))
    def nombre_produits(self, obj):
        return obj.produits.count()


@admin.register(Produit)
class ProduitAdmin(admin.ModelAdmin):
    list_display = ("reference", "nom", "fournisseur", "categorie", "prix_ht",
                    "statut", "documente", "vu_le")
    list_filter = ("statut", "fournisseur", "categorie", "stocke_plateforme")
    search_fields = ("reference", "nom")
    list_select_related = ("fournisseur", "categorie")
    autocomplete_fields = ()
    readonly_fields = ("vu_le", "caracteristiques", "caracteristiques_source")

    @admin.display(boolean=True, description=_("documenté"))
    def documente(self, obj):
        return obj.documente


@admin.register(DocumentProduit)
class DocumentProduitAdmin(admin.ModelAdmin):
    list_display = ("nom_fichier", "nature", "fournisseur", "chemin_drive",
                    "extrait_le")
    list_filter = ("nature", "fournisseur")
    search_fields = ("nom_fichier", "chemin_drive", "drive_file_id")
    filter_horizontal = ("produits",)
    readonly_fields = ("drive_file_id", "md5_drive", "octets", "modifie_le",
                       "extraction", "extrait_le")


@admin.register(SynchroDrive)
class SynchroDriveAdmin(admin.ModelAdmin):
    list_display = ("demarree_le", "statut", "crees", "modifies", "inchanges",
                    "retires", "documents_vus")
    list_filter = ("statut",)
    date_hierarchy = "demarree_le"
    # Une synchronisation est un constat : la retoucher effacerait la seule
    # trace de ce que le référentiel a réellement subi.
    readonly_fields = tuple(f.name for f in SynchroDrive._meta.fields)
