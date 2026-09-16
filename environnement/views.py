"""Vues web Environnement : bilan azoté, empreinte carbone, biodiversité, Taxonomie EU."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST



from exploitations.models import Exploitation
from parcelles.models import Parcelle

from django.db.models import Avg, Sum
from django.utils.dateparse import parse_date

from . import (azote as azote_service, carbone as carbone_service,
               rapport as rapport_service, sante as sante_service)
from .models import (ActiviteTaxonomie, ApportAzote, Biodiversite, ObservationSanitaire,
                     PosteCarbone, Traitement)



def _to_float(value, default=None):
    try:
        return float(str(value).replace(",", ".").replace("€", "").replace(" ", "").strip())
    except (TypeError, ValueError):
        return default


def _exploitation(request, create=False):
    exp = Exploitation.objects.filter(owner=request.user).first()
    if exp is None and create:
        exp = Exploitation.objects.create(
            owner=request.user,
            name=(getattr(request.user, "full_name", "") or "Mon exploitation")[:255],
        )
    return exp


def _page(request, title, icon, desc):
    return render(request, "environnement/placeholder.html", {
        "title": title, "icon": icon, "desc": desc, "page_title": title,
    })


def _int(value):
    try:
        return int(float(str(value).replace(",", ".").strip()))
    except (TypeError, ValueError):
        return None


@login_required
def biodiversite(request):
    exploitation = _exploitation(request)
    fiches = (
        Biodiversite.objects.filter(exploitation=exploitation).select_related("parcelle")
        if exploitation else Biodiversite.objects.none()
    )
    agg = fiches.aggregate(score=Avg("score"), haies=Sum("haies_ml"), jachere=Sum("jachere_ha"))
    return render(request, "environnement/biodiversite.html", {
        "fiches": fiches,
        "kpi_score": round(agg["score"]) if agg["score"] is not None else None,
        "kpi_haies": round(agg["haies"]) if agg["haies"] else 0,
        "kpi_jachere": round(agg["jachere"], 1) if agg["jachere"] else 0,
        "parcelles": Parcelle.objects.filter(exploitation=exploitation) if exploitation else Parcelle.objects.none(),
        "today": timezone.localdate().isoformat(),
        "page_title": _("Biodiversité"),
    })


def _save_biodiversite(fiche, request, exploitation):
    """Applique les champs POST à une fiche (création ou édition). False si parcelle invalide."""
    parcelle = Parcelle.objects.filter(pk=request.POST.get("parcelle"), exploitation=exploitation).first()
    if not parcelle:
        return False
    score = _int(request.POST.get("score"))
    fiche.parcelle = parcelle
    fiche.date = parse_date(request.POST.get("date") or "") or timezone.localdate()
    fiche.score = max(0, min(100, score)) if score is not None else None
    fiche.especes_vegetales = _int(request.POST.get("especes_vegetales"))
    fiche.especes_animales = _int(request.POST.get("especes_animales"))
    fiche.haies_ml = _to_float(request.POST.get("haies_ml"))
    fiche.jachere_ha = _to_float(request.POST.get("jachere_ha"))
    fiche.observations = (request.POST.get("observations") or "").strip()
    fiche.save()
    return True


@login_required
@require_POST
def biodiversite_create(request):
    """Enregistre une fiche biodiversité depuis la modale « Nouvelle fiche »."""
    exploitation = _exploitation(request, create=True)
    _save_biodiversite(Biodiversite(exploitation=exploitation), request, exploitation)
    return redirect("environnement:biodiversite")


@login_required
@require_POST
def biodiversite_edit(request, pk):
    """Met à jour une fiche existante (soumise depuis la modale d'édition)."""
    exploitation = _exploitation(request)
    fiche = get_object_or_404(Biodiversite, pk=pk, exploitation=exploitation)
    _save_biodiversite(fiche, request, exploitation)
    return redirect("environnement:biodiversite")


@login_required
@require_POST
def biodiversite_delete(request, pk):
    """Supprime une fiche biodiversité."""
    exploitation = _exploitation(request)
    get_object_or_404(Biodiversite, pk=pk, exploitation=exploitation).delete()
    return redirect("environnement:biodiversite")


# ── Bilan eau : la page a rejoint le DTI ────────────────────────────
#
# Elle répondait à la même question que le diagnostic — ce que l'installation
# fait de l'eau — depuis un autre menu. Les deux adresses subsistent en
# redirection permanente : un lien partagé ou un signet doit continuer de
# mener quelque part.


@login_required
def bilan_eau(request):
    return redirect("irrigation:dti", permanent=True)


@login_required
def bilan_eau_export(request):
    return redirect("irrigation:bilan_eau_export", permanent=True)


@login_required
def bilan_azote(request):
    """Les apports d'azote d'une campagne, et la pression sur le plafond 170."""
    exploitation = _exploitation(request)
    campagnes = azote_service.campagnes(exploitation)
    campagne = request.GET.get("campagne") or (campagnes[0] if campagnes else "")
    if campagne not in campagnes and campagnes:
        campagne = campagnes[0]

    return render(request, "environnement/bilan_azote.html", {
        "bilan": azote_service.bilan(exploitation, campagne),
        "campagne": campagne,
        "campagnes": campagnes,
        "natures": ApportAzote.Nature.choices,
        "parcelles": (Parcelle.objects.filter(exploitation=exploitation)
                      if exploitation else Parcelle.objects.none()),
        "today": timezone.localdate().isoformat(),
        "page_title": _("Bilan azoté"),
    })


def _apport_depuis(request, apport, exploitation):
    """Applique la saisie à un apport. False si la parcelle n'est pas la nôtre."""
    parcelle = Parcelle.objects.filter(pk=request.POST.get("parcelle"), exploitation=exploitation).first()
    if not parcelle:
        return False
    nature = request.POST.get("nature")
    apport.parcelle = parcelle
    apport.date = parse_date(request.POST.get("date") or "") or timezone.localdate()
    apport.nature = nature if nature in ApportAzote.Nature.values else ApportAzote.Nature.MINERAL
    apport.produit = (request.POST.get("produit") or "").strip()[:255]
    apport.dose_kg_ha = max(0, _to_float(request.POST.get("dose_kg_ha"), 0) or 0)
    apport.teneur_n_pct = min(100, max(0, _to_float(request.POST.get("teneur_n_pct"), 0) or 0))
    apport.surface_ha = _to_float(request.POST.get("surface_ha"))
    apport.notes = (request.POST.get("notes") or "").strip()
    # La campagne suit la date : corriger la date d'un apport le range ailleurs.
    apport.campagne = ""
    apport.save()
    return True


@login_required
@require_POST
def apport_azote_save(request, pk=None):
    exploitation = _exploitation(request, create=True)
    apport = (get_object_or_404(ApportAzote, pk=pk, exploitation=exploitation)
              if pk else ApportAzote(exploitation=exploitation))
    _apport_depuis(request, apport, exploitation)
    return redirect(f"{reverse('environnement:bilan_azote')}?campagne={apport.campagne}")


@login_required
@require_POST
def apport_azote_delete(request, pk):
    exploitation = _exploitation(request)
    apport = get_object_or_404(ApportAzote, pk=pk, exploitation=exploitation)
    campagne = apport.campagne
    apport.delete()
    return redirect(f"{reverse('environnement:bilan_azote')}?campagne={campagne}")


@login_required
def cahier_epandage(request):
    """Le cahier d'épandage de la campagne, en PDF — le document du contrôle."""
    exploitation = _exploitation(request)
    campagnes = azote_service.campagnes(exploitation)
    campagne = request.GET.get("campagne") or (campagnes[0] if campagnes else "")
    contexte = {
        "bilan": azote_service.bilan(exploitation, campagne),
        "campagne": campagne,
        "exploitation": exploitation,
        "edite_le": timezone.localdate(),
    }
    html = render(request, "environnement/cahier_epandage_pdf.html", contexte).content.decode()

    try:
        from weasyprint import HTML
    except Exception:  # noqa: BLE001 — libs système absentes : on rend la page
        return render(request, "environnement/cahier_epandage_pdf.html", contexte)

    from django.http import HttpResponse

    pdf = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
    reponse = HttpResponse(pdf, content_type="application/pdf")
    reponse["Content-Disposition"] = (
        'inline; filename="cahier-epandage-%s.pdf"' % (campagne or "").replace("/", "-"))
    return reponse


@login_required
def empreinte_carbone(request):
    """Les émissions de la campagne, poste par poste, et le carbone stocké."""
    exploitation = _exploitation(request)
    campagnes = carbone_service.campagnes(exploitation)
    campagne = request.GET.get("campagne") or (campagnes[0] if campagnes else "")
    if campagne not in campagnes and campagnes:
        campagne = campagnes[0]

    return render(request, "environnement/empreinte_carbone.html", {
        "bilan": carbone_service.bilan(exploitation, campagne),
        "campagne": campagne,
        "campagnes": campagnes,
        "postes": PosteCarbone.Poste.choices,
        # `json_script` sérialise lui-même : lui passer du JSON en ferait une
        # chaîne, et le gabarit attend une liste.
        "catalogue": carbone_service.catalogue(),
        "page_title": _("Empreinte carbone"),
    })


@login_required
@require_POST
def poste_carbone_save(request, pk=None):
    exploitation = _exploitation(request, create=True)
    poste = (get_object_or_404(PosteCarbone, pk=pk, exploitation=exploitation)
             if pk else PosteCarbone(exploitation=exploitation))
    campagne = (request.POST.get("campagne") or "").strip()[:20]
    libelle = (request.POST.get("libelle") or "").strip()[:255]
    if not (campagne and libelle):
        return redirect("environnement:empreinte_carbone")

    choix = request.POST.get("poste")
    poste.campagne = campagne
    poste.libelle = libelle
    poste.poste = choix if choix in PosteCarbone.Poste.values else PosteCarbone.Poste.AUTRE
    poste.quantite = _to_float(request.POST.get("quantite"), 0) or 0
    poste.unite = (request.POST.get("unite") or "").strip()[:30]
    poste.facteur = _to_float(request.POST.get("facteur"), 0) or 0
    poste.source_facteur = (request.POST.get("source_facteur") or "").strip()[:255]
    poste.notes = (request.POST.get("notes") or "").strip()
    poste.save()
    return redirect(f"{reverse('environnement:empreinte_carbone')}?campagne={poste.campagne}")


@login_required
@require_POST
def poste_carbone_delete(request, pk):
    exploitation = _exploitation(request)
    poste = get_object_or_404(PosteCarbone, pk=pk, exploitation=exploitation)
    campagne = poste.campagne
    poste.delete()
    return redirect(f"{reverse('environnement:empreinte_carbone')}?campagne={campagne}")


def _synthese(request):
    """La synthèse de la campagne demandée, et la liste des campagnes."""
    exploitation = _exploitation(request)
    campagnes = rapport_service.campagnes(exploitation)
    campagne = request.GET.get("campagne") or (campagnes[0] if campagnes else "")
    if campagne not in campagnes and campagnes:
        campagne = campagnes[0]
    return exploitation, campagnes, campagne, rapport_service.synthese(exploitation, campagne)


@login_required
def rapport_environnemental(request):
    """Eau, azote, carbone, biodiversité et Taxonomie, réunis pour une campagne."""
    _exp, campagnes, campagne, synthese = _synthese(request)
    return render(request, "environnement/rapport.html", {
        "synthese": synthese,
        "manquantes": rapport_service.rubriques_manquantes(synthese),
        "campagne": campagne,
        "campagnes": campagnes,
        "page_title": _("Rapport environnemental"),
    })


@login_required
def rapport_environnemental_pdf(request):
    """Le rapport en PDF : ce qu'on remet à une coopérative ou à une banque."""
    _exp, _campagnes, campagne, synthese = _synthese(request)
    contexte = {
        "synthese": synthese,
        "manquantes": rapport_service.rubriques_manquantes(synthese),
        "campagne": campagne,
        "edite_le": timezone.localdate(),
    }
    html = render(request, "environnement/rapport_pdf.html", contexte).content.decode()

    try:
        from weasyprint import HTML
    except Exception:  # noqa: BLE001 — libs système absentes : on rend la page
        return render(request, "environnement/rapport_pdf.html", contexte)

    from django.http import HttpResponse

    pdf = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
    reponse = HttpResponse(pdf, content_type="application/pdf")
    reponse["Content-Disposition"] = (
        'inline; filename="rapport-environnemental-%s.pdf"' % (campagne or "").replace("/", "-"))
    return reponse


@login_required
def sante_vegetale(request):
    """Le registre des traitements de la campagne, et les observations."""
    exploitation = _exploitation(request)
    campagnes = sante_service.campagnes(exploitation)
    campagne = request.GET.get("campagne") or (campagnes[0] if campagnes else "")
    if campagne not in campagnes and campagnes:
        campagne = campagnes[0]

    return render(request, "environnement/sante_vegetale.html", {
        "bilan": sante_service.bilan(exploitation, campagne),
        "campagne": campagne,
        "campagnes": campagnes,
        "cibles": Traitement.Cible.choices,
        "intensites": ObservationSanitaire.Intensite.choices,
        "parcelles": (Parcelle.objects.filter(exploitation=exploitation)
                      if exploitation else Parcelle.objects.none()),
        "today": timezone.localdate().isoformat(),
        "page_title": _("Santé végétale"),
    })


def _retour_sante(campagne):
    return redirect(f"{reverse('environnement:sante_vegetale')}?campagne={campagne}")


@login_required
@require_POST
def traitement_save(request, pk=None):
    exploitation = _exploitation(request, create=True)
    traitement = (get_object_or_404(Traitement, pk=pk, exploitation=exploitation)
                  if pk else Traitement(exploitation=exploitation))
    parcelle = Parcelle.objects.filter(pk=request.POST.get("parcelle"), exploitation=exploitation).first()
    produit = (request.POST.get("produit") or "").strip()[:255]
    if not (parcelle and produit):
        return _retour_sante(request.POST.get("campagne") or "")

    cible = request.POST.get("type_cible")
    traitement.parcelle = parcelle
    traitement.produit = produit
    traitement.date = parse_date(request.POST.get("date") or "") or timezone.localdate()
    traitement.campagne = ""  # la campagne suit la date
    traitement.culture = (request.POST.get("culture") or "").strip()[:100]
    traitement.numero_amm = (request.POST.get("numero_amm") or "").strip()[:20]
    traitement.type_cible = cible if cible in Traitement.Cible.values else Traitement.Cible.MALADIE
    traitement.cible = (request.POST.get("cible") or "").strip()[:255]
    traitement.dose = _to_float(request.POST.get("dose"))
    traitement.unite_dose = (request.POST.get("unite_dose") or "").strip()[:20]
    traitement.surface_ha = _to_float(request.POST.get("surface_ha"))
    delai = _to_float(request.POST.get("delai_avant_recolte"))
    traitement.delai_avant_recolte = int(delai) if delai is not None and delai >= 0 else None
    traitement.operateur = (request.POST.get("operateur") or "").strip()[:255]
    traitement.conditions = (request.POST.get("conditions") or "").strip()[:255]
    traitement.notes = (request.POST.get("notes") or "").strip()
    traitement.save()
    return _retour_sante(traitement.campagne)


@login_required
@require_POST
def traitement_delete(request, pk):
    exploitation = _exploitation(request)
    traitement = get_object_or_404(Traitement, pk=pk, exploitation=exploitation)
    campagne = traitement.campagne
    traitement.delete()
    return _retour_sante(campagne)


@login_required
@require_POST
def observation_save(request, pk=None):
    exploitation = _exploitation(request, create=True)
    observation = (get_object_or_404(ObservationSanitaire, pk=pk, exploitation=exploitation)
                   if pk else ObservationSanitaire(exploitation=exploitation))
    parcelle = Parcelle.objects.filter(pk=request.POST.get("parcelle"), exploitation=exploitation).first()
    nom = (request.POST.get("nom") or "").strip()[:255]
    if not (parcelle and nom):
        return _retour_sante(request.POST.get("campagne") or "")

    cible = request.POST.get("type_cible")
    intensite = _to_float(request.POST.get("intensite"), 1)
    observation.parcelle = parcelle
    observation.nom = nom
    observation.date = parse_date(request.POST.get("date") or "") or timezone.localdate()
    observation.campagne = ""
    observation.type_cible = cible if cible in Traitement.Cible.values else Traitement.Cible.MALADIE
    observation.intensite = int(intensite) if intensite in (0, 1, 2, 3) else 1
    observation.notes = (request.POST.get("notes") or "").strip()
    observation.save()
    return _retour_sante(observation.campagne)


@login_required
@require_POST
def observation_delete(request, pk):
    exploitation = _exploitation(request)
    observation = get_object_or_404(ObservationSanitaire, pk=pk, exploitation=exploitation)
    campagne = observation.campagne
    observation.delete()
    return _retour_sante(campagne)


@login_required
def registre_phyto(request):
    """Le registre des traitements en PDF — celui qu'on présente au contrôle."""
    exploitation = _exploitation(request)
    campagnes = sante_service.campagnes(exploitation)
    campagne = request.GET.get("campagne") or (campagnes[0] if campagnes else "")
    contexte = {
        "bilan": sante_service.bilan(exploitation, campagne),
        "campagne": campagne,
        "exploitation": exploitation,
        "edite_le": timezone.localdate(),
    }
    html = render(request, "environnement/registre_phyto_pdf.html", contexte).content.decode()

    try:
        from weasyprint import HTML
    except Exception:  # noqa: BLE001 — libs système absentes : on rend la page
        return render(request, "environnement/registre_phyto_pdf.html", contexte)

    from django.http import HttpResponse

    pdf = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
    reponse = HttpResponse(pdf, content_type="application/pdf")
    reponse["Content-Disposition"] = (
        'inline; filename="registre-phytosanitaire-%s.pdf"' % (campagne or "").replace("/", "-"))
    return reponse


# ── Taxonomie EU ────────────────────────────────────────────────────

@login_required
def taxonomie(request):
    exploitation = _exploitation(request)
    base = ActiviteTaxonomie.objects.filter(exploitation=exploitation) if exploitation else ActiviteTaxonomie.objects.none()

    annees = sorted(set(base.values_list("campagne", flat=True)), reverse=True)
    current = timezone.now().year
    if current not in annees:
        annees = [current] + annees
    try:
        campagne = int(request.GET.get("campagne") or annees[0])
    except (ValueError, IndexError):
        campagne = current

    fiches = list(base.filter(campagne=campagne))

    def _pct(attr):
        total = sum((getattr(f, attr) or 0) for f in fiches)
        aligned = sum((getattr(f, attr) or 0) for f in fiches if f.aligne)
        return round(aligned / total * 100) if total else 0

    return render(request, "environnement/taxonomie.html", {
        "fiches": fiches,
        "annees": annees,
        "campagne": campagne,
        "pct_ca": _pct("chiffre_affaires"),
        "pct_capex": _pct("capex"),
        "pct_opex": _pct("opex"),
        "nb_fiches": len(fiches),
        "nb_alignes": sum(1 for f in fiches if f.aligne),
        "objectifs": ActiviteTaxonomie.Objectif.choices,
        "page_title": _("Taxonomie EU"),
    })


@login_required
@require_POST
def taxonomie_create(request):
    exploitation = _exploitation(request, create=True)
    try:
        campagne = int(request.POST.get("campagne") or timezone.now().year)
    except (ValueError, TypeError):
        campagne = timezone.now().year
    dnsh = {value: (request.POST.get(f"dnsh_{value}") == "on") for value, _label in ActiviteTaxonomie.Objectif.choices}
    ActiviteTaxonomie.objects.create(
        exploitation=exploitation,
        campagne=campagne,
        libelle=(request.POST.get("libelle") or "").strip()[:255] or "Activité",
        code_nace=(request.POST.get("code_nace") or "").strip()[:20],
        objectif=request.POST.get("objectif") or ActiviteTaxonomie.Objectif.ATTENUATION,
        eligible=request.POST.get("eligible") == "on",
        contribution=request.POST.get("contribution") == "on",
        garanties=request.POST.get("garanties") == "on",
        dnsh=dnsh,
        chiffre_affaires=_to_float(request.POST.get("chiffre_affaires")),
        capex=_to_float(request.POST.get("capex")),
        opex=_to_float(request.POST.get("opex")),
        justification=(request.POST.get("justification") or "").strip(),
    )
    return redirect(f"{reverse('environnement:taxonomie')}?campagne={campagne}")


@login_required
@require_POST
def taxonomie_delete(request, pk):
    exploitation = _exploitation(request)
    obj = get_object_or_404(ActiviteTaxonomie, pk=pk, exploitation=exploitation)
    campagne = obj.campagne
    obj.delete()
    return redirect(f"{reverse('environnement:taxonomie')}?campagne={campagne}")
