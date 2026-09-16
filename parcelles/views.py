"""Vues web parcelles : carte + liste, création, détail, édition, suppression, cadastre IGN."""

import json
import urllib.parse
import urllib.request

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from exploitations.models import Exploitation
from irrigation import satellite, teledetection

from . import analyse, carte, geometrie
from .forms import ParcelleCampagneForm, ParcelleForm, ParcelleTypeAgricultureForm
from .models import Parcelle, ParcelleCampagne


def _exploitation_or_redirect(request):
    return Exploitation.objects.filter(owner=request.user).first()


@login_required
@ensure_csrf_cookie
def parcelle_list(request):
    exploitation = _exploitation_or_redirect(request)
    parcelles = (
        Parcelle.objects.filter(exploitation=exploitation)
        .prefetch_related("analyses_sol", "ndvi_data")
        if exploitation else Parcelle.objects.none()
    )
    features, total_area = [], 0.0
    for p in parcelles:
        total_area += p.area or 0
        if p.boundaries:
            features.append({
                "type": "Feature",
                "geometry": p.boundaries,
                "properties": {"id": p.pk, "name": p.name, "area": p.area,
                               "ref": p.cadastral_ref,
                               "orientation": p.orientation_rangs_deg},
            })
    return render(request, "parcelles/list.html", {
        "parcelles": parcelles,
        "parcelles_geojson": {"type": "FeatureCollection", "features": features},
        # Onglet Analyses : indices satellite NDVI / NDWI par parcelle.
        "teledetection": teledetection.lignes_par_parcelle(parcelles),
        "satellite_configure": satellite.is_configured(),
        "kpi_actives": parcelles.filter(status=Parcelle.Status.ACTIVE).count() if exploitation else 0,
        "kpi_total_area": round(total_area, 2),
        "needs_onboarding": exploitation is None,
        "page_title": _("Mes Parcelles"),
    })


@login_required
def parcelle_cadastre(request):
    """Proxy vers l'API Carto Cadastre (IGN) — parcelle(s) au point cliqué."""
    try:
        lat, lon = float(request.GET["lat"]), float(request.GET["lon"])
    except (KeyError, TypeError, ValueError):
        return JsonResponse({"error": "lat/lon requis"}, status=400)
    geom = json.dumps({"type": "Point", "coordinates": [lon, lat]})
    url = "https://apicarto.ign.fr/api/cadastre/parcelle?geom=" + urllib.parse.quote(geom) + "&source_ign=PCI"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Isidor/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return HttpResponse(resp.read(), content_type="application/json")
    except Exception as exc:  # noqa: BLE001 — toute erreur réseau/API renvoyée au front
        return JsonResponse({"error": str(exc)}, status=502)


def _create_parcelle_from_feature(exploitation, feature, name=None, lat=None, lon=None):
    """Crée une Parcelle depuis une feature cadastrale (API Carto IGN)."""
    geom = (feature or {}).get("geometry")
    if not geom:
        return None
    props = feature.get("properties", {})
    contenance = props.get("contenance")
    area = round(contenance / 10000, 4) if contenance else None
    ref = f"{props.get('section', '')} {props.get('numero', '')}".strip()
    commune = props.get("nom_com") or str(props.get("code_insee", "")) or str(props.get("code_com", ""))
    name = (name or "").strip() or f"Parcelle {ref}".strip() or "Parcelle"
    return Parcelle.objects.create(
        exploitation=exploitation,
        name=name[:255],
        boundaries=geom,
        cadastral_ref=ref[:50],
        commune=str(commune)[:100],
        area=area,
        official_area_ha=area,
        latitude=lat,
        longitude=lon,
        cadastre_data=props,          # toutes les données cadastre disponibles (brut IGN)
        acquired_at=timezone.now(),   # date d'acquisition
    )


@login_required
@require_POST
def parcelle_cadastre_save(request):
    """Enregistre une ou plusieurs parcelles sélectionnées depuis le cadastre IGN."""
    exploitation = _exploitation_or_redirect(request)
    if exploitation is None:
        # Crée une exploitation par défaut pour ne pas bloquer l'ajout de parcelles.
        exploitation = Exploitation.objects.create(
            owner=request.user,
            name=(getattr(request.user, "display_name", "") or "Mon exploitation")[:255],
        )
    try:
        body = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"error": "JSON invalide"}, status=400)

    items = body.get("parcelles")
    if not items:  # rétrocompat : une seule parcelle
        items = [{"feature": body.get("feature"), "name": body.get("name"),
                  "lat": body.get("lat"), "lon": body.get("lon")}]

    created = 0
    for it in items:
        parcelle = _create_parcelle_from_feature(
            exploitation, it.get("feature"), it.get("name"), it.get("lat"), it.get("lon")
        )
        if parcelle:
            created += 1
    if not created:
        return JsonResponse({"error": "Aucune parcelle valide."}, status=400)
    return JsonResponse({"ok": True, "created": created})


@login_required
@require_POST
def parcelle_contour(request, pk):
    """Redessine le contour d'une parcelle — POST JSON {geometry: Polygon}.

    Le cadastre décrit la propriété, pas ce qui est cultivé : le contour tracé
    sur la carte fait foi pour la surface. `official_area_ha` (surface
    cadastrale) reste intacte, elle sert de référence administrative.
    """
    exploitation = _exploitation_or_redirect(request)
    parcelle = get_object_or_404(Parcelle, pk=pk, exploitation=exploitation)
    try:
        body = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"error": "JSON invalide"}, status=400)

    geometry = body.get("geometry") or {}
    anneau = geometrie.anneau_exterieur(geometry) if geometry.get("type") == "Polygon" else None
    # Un anneau fermé = au moins 3 sommets distincts + le retour au premier.
    if not anneau or len(anneau) < 4:
        return JsonResponse({"error": "Tracez au moins 3 points."}, status=400)
    surface = geometrie.surface_ha(anneau)
    if not surface:
        return JsonResponse({"error": "Contour trop petit ou aplati."}, status=400)

    parcelle.boundaries = geometry
    parcelle.area = surface
    parcelle.save(update_fields=["boundaries", "area", "updated_at"])
    return JsonResponse({"ok": True, "id": parcelle.pk, "area": surface})


@login_required
@require_POST
def parcelle_orientation(request, pk):
    """Sens des rangs d'une parcelle — POST JSON {deg} (null pour effacer).

    L'azimut se prend au clic sur la carte : 0 = Nord, sens horaire.
    """
    exploitation = _exploitation_or_redirect(request)
    parcelle = get_object_or_404(Parcelle, pk=pk, exploitation=exploitation)
    try:
        body = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"error": "JSON invalide"}, status=400)

    deg = body.get("deg")
    if deg is None or deg == "":
        parcelle.orientation_rangs_deg = None
    else:
        try:
            parcelle.orientation_rangs_deg = int(round(float(deg))) % 360
        except (ValueError, TypeError):
            return JsonResponse({"error": "Angle invalide"}, status=400)
    parcelle.save(update_fields=["orientation_rangs_deg", "updated_at"])
    return JsonResponse({"ok": True, "orientation": parcelle.orientation_rangs_deg})


@login_required
def parcelle_create(request):
    exploitation = _exploitation_or_redirect(request)
    if exploitation is None:
        messages.info(request, _("Configurez d'abord votre exploitation."))
        return redirect("exploitations:settings")

    if request.method == "POST":
        form = ParcelleForm(request.POST)
        campagne_form = ParcelleCampagneForm(request.POST)
        if form.is_valid() and campagne_form.is_valid():
            with transaction.atomic():
                parcelle = form.save(commit=False)
                parcelle.exploitation = exploitation
                parcelle.save()
                campagne = campagne_form.save(commit=False)
                campagne.parcelle = parcelle
                campagne.save()
            messages.success(request, _("Parcelle créée."))
            return redirect("parcelles:detail", pk=parcelle.pk)
    else:
        form = ParcelleForm()
        campagne_form = ParcelleCampagneForm()

    return render(
        request,
        "parcelles/form.html",
        {"form": form, "campagne_form": campagne_form,
         "page_title": _("Nouvelle parcelle"), "is_create": True},
    )


@login_required
def parcelle_detail(request, pk):
    exploitation = _exploitation_or_redirect(request)
    parcelle = get_object_or_404(Parcelle, pk=pk, exploitation=exploitation)

    center = None
    if parcelle.latitude is not None and parcelle.longitude is not None:
        center = [parcelle.latitude, parcelle.longitude]
    elif parcelle.boundaries:
        c = _bounds_center(parcelle.boundaries)
        if c:
            center = [c[0], c[1]]

    geojson = None
    if parcelle.boundaries:
        geojson = {
            "type": "Feature",
            "geometry": parcelle.boundaries,
            "properties": {"name": parcelle.name},
        }

    # Campagne affichée : celle demandée (?campagne=<id>), sinon la plus récente.
    campagnes = parcelle.campagnes.all()
    campagne = None
    sel_id = request.GET.get("campagne")
    if sel_id:
        campagne = campagnes.filter(pk=sel_id).first()
    if campagne is None:
        campagne = parcelle.campagne_courante

    return render(
        request,
        "parcelles/detail.html",
        {
            "parcelle": parcelle,
            "campagnes": campagnes,
            "campagne": campagne,
            "crop_stages": campagne.crop_stages.all() if campagne else [],
            "analyses_sol": parcelle.analyses_sol.all(),
            "geojson": geojson,
            "map_center": center,
            "page_title": parcelle.name,
        },
    )


@login_required
def campagne_list(request):
    """Toutes les campagnes de l'exploitation, filtrables par libellé."""
    exploitation = _exploitation_or_redirect(request)
    base = (
        ParcelleCampagne.objects.filter(parcelle__exploitation=exploitation).select_related("parcelle")
        if exploitation else ParcelleCampagne.objects.none()
    )

    libelles = list(base.order_by("-libelle").values_list("libelle", flat=True).distinct())
    courante = ParcelleCampagne.libelle_courant()
    # Campagne affichée : ?campagne=… (vide = toutes), sinon celle en cours.
    if "campagne" in request.GET:
        selection = request.GET["campagne"].strip()
    else:
        selection = courante if courante in libelles else ""
    campagnes = base.filter(libelle=selection) if selection else base

    surface = sum(c.parcelle.area or 0 for c in campagnes)
    cultures = {c.culture for c in campagnes if c.culture}

    return render(request, "parcelles/campagnes.html", {
        "campagnes": campagnes.order_by("-libelle", "parcelle__name"),
        "libelles": libelles,
        "selection": selection,
        "courante": courante,
        "kpi_count": campagnes.count(),
        "kpi_parcelles": campagnes.values("parcelle").distinct().count(),
        "kpi_surface": round(surface, 2),
        "kpi_cultures": len(cultures),
        "parcelles": Parcelle.objects.filter(exploitation=exploitation) if exploitation else [],
        "page_title": _("Campagnes"),
    })


def _semer_la_campagne(request, parcelles, donnees):
    """Crée la même campagne sur chaque parcelle. Rend son libellé, ou "".

    Une parcelle qui porte déjà cette campagne est passée plutôt que de faire
    échouer les autres : on sème sur ce qui est libre, et l'on dit lesquelles
    ont été laissées de côté.
    """
    libelle = donnees["libelle"]
    creees, deja = [], []
    with transaction.atomic():
        for parcelle in parcelles:
            if ParcelleCampagne.objects.filter(parcelle=parcelle, libelle=libelle).exists():
                deja.append(parcelle.name)
                continue
            ParcelleCampagne.objects.create(parcelle=parcelle, **donnees)
            # Le type d'agriculture appartient à la parcelle, pas à la campagne.
            ParcelleTypeAgricultureForm(request.POST, instance=parcelle).save()
            creees.append(parcelle.name)

    if deja:
        messages.info(request, _("Campagne déjà présente sur : %(noms)s.")
                      % {"noms": ", ".join(deja)})
    if not creees:
        return ""
    messages.success(request, _("Campagne ajoutée sur %(n)s parcelle(s).")
                     % {"n": len(creees)})
    return libelle


@login_required
def campagne_new(request):
    """Nouvelle campagne depuis la page Campagnes : la parcelle est à choisir."""
    exploitation = _exploitation_or_redirect(request)
    parcelles = Parcelle.objects.filter(exploitation=exploitation) if exploitation else Parcelle.objects.none()
    if not parcelles.exists():
        # Une campagne se rattache à une parcelle : sans parcelle, rien à saisir.
        messages.info(request, _("Créez d'abord une parcelle pour lui ajouter une campagne."))
        return redirect("parcelles:list")

    choisies, erreur_parcelle = [], ""
    if request.method == "POST":
        # Une même campagne se sème rarement sur une seule parcelle : on en
        # accepte plusieurs, et l'on crée la même culture sur chacune.
        choisies = list(parcelles.filter(pk__in=request.POST.getlist("parcelles")))
        form = ParcelleCampagneForm(request.POST)
        parcelle_form = ParcelleTypeAgricultureForm(request.POST)
        if not choisies:
            erreur_parcelle = _("Choisissez au moins une parcelle.")
            form.is_valid()  # peuple form.errors pour l'affichage
        elif form.is_valid() and parcelle_form.is_valid():
            campagne = _semer_la_campagne(request, choisies, form.cleaned_data)
            if campagne:
                return redirect(f"{reverse('parcelles:campagnes')}?campagne={campagne}")
            erreur_parcelle = _("Cette campagne existe déjà sur toutes les parcelles choisies.")
    else:
        form = ParcelleCampagneForm()
        parcelle_form = ParcelleTypeAgricultureForm()

    return render(request, "parcelles/campagne_form.html", {
        "form": form,
        "parcelle_form": parcelle_form,
        # La carte et les pastilles, comme au formulaire de tâche : une
        # parcelle se reconnaît à sa forme.
        **carte.contexte(parcelles),
        "noms_parcelles": json.dumps({str(p.pk): p.name for p in parcelles}),
        "parcelles_choisies": json.dumps([str(p.pk) for p in choisies]),
        "erreur_parcelle": erreur_parcelle,
        "retour_url": reverse("parcelles:campagnes"),
        "page_title": _("Nouvelle campagne"),
    })


@login_required
def campagne_create(request, parcelle_pk):
    exploitation = _exploitation_or_redirect(request)
    parcelle = get_object_or_404(Parcelle, pk=parcelle_pk, exploitation=exploitation)
    parcelle_form = ParcelleTypeAgricultureForm(request.POST or None, instance=parcelle)
    if request.method == "POST":
        form = ParcelleCampagneForm(request.POST)
        form.instance.parcelle = parcelle
        if form.is_valid() and parcelle_form.is_valid():
            campagne = form.save()
            parcelle_form.save()
            messages.success(request, _("Campagne ajoutée."))
            return redirect(f"{parcelle.get_absolute_url()}?campagne={campagne.pk}")
    else:
        form = ParcelleCampagneForm()
    return render(request, "parcelles/campagne_form.html", {
        "form": form, "parcelle_form": parcelle_form,
        "parcelle": parcelle, "retour_url": parcelle.get_absolute_url(),
        "page_title": _("Nouvelle campagne"),
    })


@login_required
def campagne_edit(request, pk):
    exploitation = _exploitation_or_redirect(request)
    campagne = get_object_or_404(ParcelleCampagne, pk=pk, parcelle__exploitation=exploitation)
    parcelle = campagne.parcelle
    parcelle_form = ParcelleTypeAgricultureForm(request.POST or None, instance=parcelle)
    if request.method == "POST":
        form = ParcelleCampagneForm(request.POST, instance=campagne)
        if form.is_valid() and parcelle_form.is_valid():
            form.save()
            parcelle_form.save()
            messages.success(request, _("Campagne mise à jour."))
            return redirect(f"{parcelle.get_absolute_url()}?campagne={campagne.pk}")
    else:
        form = ParcelleCampagneForm(instance=campagne)
    return render(request, "parcelles/campagne_form.html", {
        "form": form, "parcelle_form": parcelle_form,
        "parcelle": parcelle, "campagne": campagne,
        "retour_url": parcelle.get_absolute_url(),
        "page_title": _("Modifier la campagne %(l)s") % {"l": campagne.libelle},
    })


@login_required
def campagne_delete(request, pk):
    exploitation = _exploitation_or_redirect(request)
    campagne = get_object_or_404(ParcelleCampagne, pk=pk, parcelle__exploitation=exploitation)
    parcelle = campagne.parcelle

    # Retour à l'écran d'origine : la liste des campagnes ou la fiche parcelle.
    depuis_campagnes = "campagnes" in (request.POST.get("next"), request.GET.get("next"))
    retour_url = reverse("parcelles:campagnes") if depuis_campagnes else parcelle.get_absolute_url()

    if request.method == "POST":
        campagne.delete()
        messages.success(request, _("Campagne supprimée."))
        return redirect(retour_url)
    return render(request, "parcelles/campagne_confirm_delete.html", {
        "campagne": campagne, "parcelle": parcelle,
        "retour_url": retour_url, "depuis_campagnes": depuis_campagnes,
    })


def _bounds_center(geom):
    """Centre (lat, lng) de la bounding-box d'une géométrie GeoJSON, ou None."""
    if not isinstance(geom, dict):
        return None
    lats, lngs = [], []
    stack = [geom.get("coordinates")]
    while stack:
        item = stack.pop()
        if isinstance(item, (list, tuple)):
            if len(item) >= 2 and all(isinstance(x, (int, float)) for x in item[:2]):
                lngs.append(item[0])
                lats.append(item[1])
            else:
                stack.extend(item)
    if not lats:
        return None
    return round((min(lats) + max(lats)) / 2, 6), round((min(lngs) + max(lngs)) / 2, 6)


@login_required
def parcelle_edit(request, pk):
    exploitation = _exploitation_or_redirect(request)
    parcelle = get_object_or_404(Parcelle, pk=pk, exploitation=exploitation)
    campagne = parcelle.campagne_courante  # éditée en même temps que la parcelle
    if request.method == "POST":
        form = ParcelleForm(request.POST, instance=parcelle)
        campagne_form = ParcelleCampagneForm(request.POST, instance=campagne)
        campagne_form.instance.parcelle = parcelle
        if form.is_valid() and campagne_form.is_valid():
            with transaction.atomic():
                form.save()
                campagne_form.save()
            messages.success(request, _("Parcelle mise à jour."))
            return redirect("parcelles:detail", pk=parcelle.pk)
    else:
        # Récupère lat/lng depuis le polygone cadastre si elles manquent.
        if parcelle.latitude is None and parcelle.longitude is None and parcelle.boundaries:
            center = _bounds_center(parcelle.boundaries)
            if center:
                parcelle.latitude, parcelle.longitude = center
                parcelle.save(update_fields=["latitude", "longitude"])
        form = ParcelleForm(instance=parcelle)
        campagne_form = ParcelleCampagneForm(instance=campagne)
    return render(
        request,
        "parcelles/form.html",
        {"form": form, "campagne_form": campagne_form, "parcelle": parcelle,
         "page_title": _("Modifier %(n)s") % {"n": parcelle.name},
         # Ce que la parcelle a produit et le temps qu'elle a demandé : on modifie une fiche
         # en ayant sous les yeux ce qu'elle a donné.
         **analyse.synthese(parcelle)},
    )


@login_required
def parcelle_delete(request, pk):
    exploitation = _exploitation_or_redirect(request)
    parcelle = get_object_or_404(Parcelle, pk=pk, exploitation=exploitation)
    if request.method == "POST":
        parcelle.delete()
        messages.success(request, _("Parcelle supprimée."))
        return redirect("parcelles:list")
    return render(request, "parcelles/confirm_delete.html", {"parcelle": parcelle})


@login_required
@require_POST
def parcelle_teledetection(request, pk):
    """Analyse satellite d'une parcelle (Sentinel-2) depuis l'onglet Analyses."""
    exploitation = _exploitation_or_redirect(request)
    parcelle = get_object_or_404(Parcelle, pk=pk, exploitation=exploitation)
    try:
        mesure = satellite.analyser(parcelle)
    except satellite.SatelliteError as erreur:
        messages.error(request, str(erreur))
    else:
        messages.success(request, _("Analyse satellite du %(date)s enregistrée pour %(nom)s.") % {
            "date": mesure.acquisition_date.strftime("%d/%m/%Y"), "nom": parcelle.name,
        })
    return redirect(f"{reverse('parcelles:list')}?tab=analyses")
