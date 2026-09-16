"""Vues web agronomie : référentiels cultures Kc / types de sol, saisons."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from exploitations.models import Exploitation
from parcelles.models import Parcelle

from .models import CultureKc, Engrais, Fertigation, TypeSol, Variete


def _to_float(value, default):
    """Parse un nombre tolérant à la virgule décimale (ex. '0,30')."""
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return default


_MOIS_ABBR = ["", "janv.", "févr.", "mars", "avr.", "mai", "juin",
              "juil.", "août", "sept.", "oct.", "nov.", "déc."]
_MOIS_FULL = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
              "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
_SEASONS = [("printemps", {3, 4, 5}), ("ete", {6, 7, 8}),
            ("automne", {9, 10, 11}), ("hiver", {12, 1, 2})]


def _to_month(value):
    try:
        m = int(value)
        return m if 1 <= m <= 12 else None
    except (TypeError, ValueError):
        return None


def _mois_label(debut, fin):
    if not debut or not fin:
        return ""
    return _MOIS_ABBR[debut] if debut == fin else f"{_MOIS_ABBR[debut]} → {_MOIS_ABBR[fin]}"


def _seasons_for(months):
    ms = set(months)
    return [key for key, group in _SEASONS if ms & group]


def _variete_dict(v):
    """Sérialise une variété (fiche détaillée)."""
    return {
        "id": v.id, "cultureId": v.culture_id, "nom": v.nom, "sci": v.nom_scientifique,
        "photo": v.photo.url if v.photo else "",
        "description": v.description, "note": v.note, "nbAvis": v.nb_avis,
        "sd": v.semis_debut, "sf": v.semis_fin, "rd": v.recolte_debut, "rf": v.recolte_fin,
        "semis": _mois_label(v.semis_debut, v.semis_fin) or _mois_label(v.culture.semis_debut, v.culture.semis_fin),
        "recolte": _mois_label(v.recolte_debut, v.recolte_fin) or _mois_label(v.culture.recolte_debut, v.culture.recolte_fin),
        "seasons": _seasons_for(v.semis_mois),
        "exposition": v.exposition, "expositionLabel": v.get_exposition_display() if v.exposition else "",
        "arrosage": v.arrosage, "arrosageLabel": v.get_arrosage_display() if v.arrosage else "",
        "natureSol": v.nature_sol, "solDetail": v.sol_detail, "modeCulture": v.mode_culture,
        "conseilSemis": v.conseil_semis, "conseilCulture": v.conseil_culture,
        "poids": v.poids, "contenance": v.contenance_sachet, "forme": v.forme,
        "textureFruit": v.texture_fruit, "typeCroissance": v.type_croissance,
        "couleur": v.couleur, "feuillage": v.feuillage, "typeSemis": v.type_semis,
        "origine": v.origine, "origineTexte": v.origine_texte,
    }


def _culture_dict(c):
    """Sérialise une culture (espèce) + ses variétés."""
    varietes = [_variete_dict(v) for v in c.varietes.all()]
    return {
        "id": c.id, "nom": c.nom, "sci": c.nom_scientifique,
        "cat": c.categorie, "catLabel": str(c.get_categorie_display()),
        "ki": c.kc_initial, "km": c.kc_mid, "ke": c.kc_end,
        "source": c.source, "notes": c.notes,
        "sd": c.semis_debut, "sf": c.semis_fin, "rd": c.recolte_debut, "rf": c.recolte_fin,
        "semis": _mois_label(c.semis_debut, c.semis_fin),
        "recolte": _mois_label(c.recolte_debut, c.recolte_fin),
        "semisMonths": c.semis_mois, "recolteMonths": c.recolte_mois,
        "seasons": _seasons_for(c.semis_mois),
        "varietes": varietes, "nbVar": len(varietes),
    }


@login_required
def cultures(request):
    data = [_culture_dict(c) for c in CultureKc.objects.prefetch_related("varietes")]
    return render(
        request,
        "agronomie/cultures.html",
        {
            "cultures_json": data,
            "categories": CultureKc.Categorie.choices,
            "expositions": Variete.Exposition.choices,
            "arrosages": Variete.Arrosage.choices,
            "mois_choices": list(enumerate(_MOIS_FULL, 1)),
            "page_title": _("Cultures"),
        },
    )


def _culture_fields_from_post(request):
    """Champs d'une culture (espèce) depuis le POST."""
    g = request.POST.get
    return {
        "nom_scientifique": (g("nom_scientifique") or "").strip(),
        "categorie": g("categorie") or CultureKc.Categorie.AUTRE,
        "kc_initial": _to_float(g("kc_initial"), 0.30),
        "kc_mid": _to_float(g("kc_mid"), 1.00),
        "kc_end": _to_float(g("kc_end"), 0.60),
        "semis_debut": _to_month(g("semis_debut")),
        "semis_fin": _to_month(g("semis_fin")),
        "recolte_debut": _to_month(g("recolte_debut")),
        "recolte_fin": _to_month(g("recolte_fin")),
        "source": (g("source") or "").strip() or "FAO-56",
        "notes": (g("notes") or "").strip(),
    }


@login_required
@require_POST
def culture_create(request):
    """Crée une culture (espèce)."""
    nom = (request.POST.get("nom") or "").strip()
    if nom:
        CultureKc.objects.create(nom=nom, **_culture_fields_from_post(request))
    return redirect("agronomie:cultures")


@login_required
@require_POST
def culture_edit(request, pk):
    """Met à jour une culture (espèce)."""
    culture = CultureKc.objects.filter(pk=pk).first()
    nom = (request.POST.get("nom") or "").strip()
    if culture and nom:
        for field, value in {"nom": nom, **_culture_fields_from_post(request)}.items():
            setattr(culture, field, value)
        culture.save()
    return redirect("agronomie:cultures")


@login_required
@require_POST
def culture_delete(request, pk):
    """Supprime une culture (et ses variétés en cascade)."""
    CultureKc.objects.filter(pk=pk).delete()
    return redirect("agronomie:cultures")


# ── Variétés (fiches détaillées rattachées à une culture) ───────────

def _variete_fields_from_post(request):
    g = request.POST.get
    return {
        "nom_scientifique": (g("nom_scientifique") or "").strip(),
        "note": _to_float(g("note"), None),
        "nb_avis": int(g("nb_avis") or 0) if (g("nb_avis") or "").strip().isdigit() else 0,
        "description": (g("description") or "").strip(),
        "semis_debut": _to_month(g("semis_debut")),
        "semis_fin": _to_month(g("semis_fin")),
        "recolte_debut": _to_month(g("recolte_debut")),
        "recolte_fin": _to_month(g("recolte_fin")),
        "exposition": g("exposition") or "",
        "arrosage": g("arrosage") or "",
        "nature_sol": (g("nature_sol") or "").strip(),
        "sol_detail": (g("sol_detail") or "").strip(),
        "mode_culture": (g("mode_culture") or "").strip(),
        "conseil_semis": (g("conseil_semis") or "").strip(),
        "conseil_culture": (g("conseil_culture") or "").strip(),
        "poids": (g("poids") or "").strip(),
        "contenance_sachet": (g("contenance_sachet") or "").strip(),
        "forme": (g("forme") or "").strip(),
        "texture_fruit": (g("texture_fruit") or "").strip(),
        "type_croissance": (g("type_croissance") or "").strip(),
        "couleur": (g("couleur") or "").strip(),
        "feuillage": (g("feuillage") or "").strip(),
        "type_semis": (g("type_semis") or "").strip(),
        "origine": (g("origine") or "").strip(),
        "origine_texte": (g("origine_texte") or "").strip(),
        "source": (g("source") or "").strip(),
    }


@login_required
@require_POST
def variete_create(request):
    """Crée une variété rattachée à une culture."""
    culture = CultureKc.objects.filter(pk=request.POST.get("culture")).first()
    nom = (request.POST.get("nom") or "").strip()
    if culture and nom:
        Variete.objects.create(culture=culture, nom=nom, photo=request.FILES.get("photo"),
                               **_variete_fields_from_post(request))
    return redirect("agronomie:cultures")


@login_required
@require_POST
def variete_edit(request, pk):
    """Met à jour une variété."""
    variete = Variete.objects.filter(pk=pk).first()
    nom = (request.POST.get("nom") or "").strip()
    if variete and nom:
        for field, value in {"nom": nom, **_variete_fields_from_post(request)}.items():
            setattr(variete, field, value)
        if request.FILES.get("photo"):
            variete.photo = request.FILES["photo"]
        variete.save()
    return redirect("agronomie:cultures")


@login_required
@require_POST
def variete_delete(request, pk):
    """Supprime une variété."""
    Variete.objects.filter(pk=pk).delete()
    return redirect("agronomie:cultures")


@login_required
def types_sol(request):
    data = [
        {
            "id": t.id,
            "nom": t.nom,
            "texture": t.texture,
            "textureLabel": str(t.get_texture_display()),
            "retention": t.capacite_retention_mm,
            "ph": t.ph_typique,
            "conductivite": t.conductivite_hydraulique,
            "densite": t.densite_apparente,
        }
        for t in TypeSol.objects.all()
    ]
    return render(
        request,
        "agronomie/types_sol.html",
        {
            "types_json": data,
            "textures": TypeSol.Texture.choices,
            "page_title": _("Types de sol"),
        },
    )


@login_required
@require_POST
def type_sol_create(request):
    nom = (request.POST.get("nom") or "").strip()
    if nom:
        TypeSol.objects.create(
            nom=nom,
            texture=request.POST.get("texture") or TypeSol.Texture.LIMONEUX,
            capacite_retention_mm=_to_float(request.POST.get("retention"), 100),
            ph_typique=_to_float(request.POST.get("ph"), 7.0),
            conductivite_hydraulique=_to_float(request.POST.get("conductivite"), None),
            densite_apparente=_to_float(request.POST.get("densite"), None),
            notes=(request.POST.get("notes") or "").strip(),
        )
    return redirect("agronomie:types_sol")


@login_required
@require_POST
def type_sol_delete(request, pk):
    TypeSol.objects.filter(pk=pk).delete()
    return redirect("agronomie:types_sol")


@login_required
def fertigation(request):
    """Catalogue d'engrais, suivi Directive Nitrates, historique des apports."""
    exploitation = Exploitation.objects.filter(owner=request.user).first()
    fertigations = (
        Fertigation.objects.filter(exploitation=exploitation).select_related("parcelle")
        if exploitation
        else Fertigation.objects.none()
    )
    engrais = Engrais.objects.all()
    engrais_json = [
        {"nom": e.nom, "type": e.type_engrais, "n": e.n_pct, "p": e.p_pct, "k": e.k_pct}
        for e in engrais
    ]
    total_n = round(sum(f.azote_n or 0 for f in fertigations), 1)
    parcelles = Parcelle.objects.filter(exploitation=exploitation) if exploitation else Parcelle.objects.none()
    return render(request, "agronomie/fertigation.html", {
        "engrais": engrais,
        "engrais_json": engrais_json,
        "fertigations": fertigations,
        "parcelles": parcelles,
        "total_n": total_n,
        "page_title": _("Fertigation"),
    })


@login_required
@require_POST
def fertigation_create(request):
    exploitation = Exploitation.objects.filter(owner=request.user).first()
    parcelle = Parcelle.objects.filter(pk=request.POST.get("parcelle"), exploitation=exploitation).first()
    if exploitation and parcelle:
        dt = parse_datetime(request.POST.get("date") or "") or timezone.now()
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        Fertigation.objects.create(
            exploitation=exploitation,
            parcelle=parcelle,
            date=dt,
            produit=(request.POST.get("produit") or "").strip(),
            azote_n=_to_float(request.POST.get("azote_n"), 0),
            phosphore_p=_to_float(request.POST.get("phosphore_p"), 0),
            potassium_k=_to_float(request.POST.get("potassium_k"), 0),
            volume_l=_to_float(request.POST.get("volume_l"), 0),
        )
    return redirect("agronomie:fertigation")
