"""Variables injectées dans tous les templates (navigation, branding)."""

import os
import unicodedata

from django.conf import settings
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _

from . import espaces as espaces_service


def _sort_key(label):
    """Clé de tri alphabétique insensible aux accents/casse, dans la langue active."""
    text = unicodedata.normalize("NFKD", str(label))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.casefold()


def _css_version():
    """En dev, mtime de app.css → cache-buster pour éviter le hard-refresh.
    En prod (DEBUG=False), on renvoie "" : le manifest hashé gère déjà le cache."""
    if not settings.DEBUG:
        return ""
    for base in settings.STATICFILES_DIRS or []:
        path = os.path.join(base, "css", "app.css")
        try:
            return str(int(os.path.getmtime(path)))
        except OSError:
            continue
    return ""


def layout(request):
    """Navigation de la sidebar (sections) + accès rapides mobiles + branding."""
    nav_primary = [
        {"label": _("Accueil"), "url_name": "core:dashboard"},
        {"label": _("Parcelles"), "url_name": "parcelles:list"},
        {"label": _("Irrigation"), "url_name": "irrigation:irrigation"},
        {"label": _("Planning"), "url_name": "planning:planning"},
        {"label": _("Agent IA"), "url_name": "ia:assistant"},
    ]

    nav_sections = [
        {
            "label": _("Accueil"), "key": "accueil",
            "items": [
                {"label": _("Tableau de bord"), "url_name": "core:dashboard", "icon": "dashboard"},
                # Réservée à l'espace client : elle n'a pas de sens ailleurs.
                {"label": _("Mes documents"), "url_name": "client:espace", "icon": "description",
                 "espaces": (espaces_service.CLIENT,)},
                {"label": _("Mes commandes"), "url_name": "vente:mes_commandes", "icon": "shopping_basket",
                 "espaces": (espaces_service.CLIENT,)},
            ],
        },
        {
            "label": _("Communication"), "key": "communication",
            "items": [
                {"label": _("Notifications"), "url_name": "notifications:center", "icon": "notifications"},
                {"label": _("Messagerie"), "url_name": "messagerie:inbox", "icon": "chat"},
                {"label": _("Mail"), "url_name": "mail:outbox", "icon": "mail"},
                {"label": _("Réseau"), "url_name": "reseaux:reseaux", "icon": "hub"},
                {"label": _("Pétition"), "url_name": "petition:liste", "icon": "draw"},
            ],
        },
        {
            # Ce qui pousse : la terre, ce qu'on y met, ce qu'on y fait.
            "label": _("Cultures"), "key": "cultures",
            "items": [
                {"label": _("Mes Parcelles"), "url_name": "parcelles:list", "icon": "map"},
                {"label": _("Campagnes"), "url_name": "parcelles:campagnes", "icon": "event_repeat"},
                {"label": _("Cultures"), "url_name": "agronomie:cultures", "icon": "grass"},
                {"label": _("Types de sol"), "url_name": "agronomie:types_sol", "icon": "terrain"},
                {"label": _("Analyses de sol"), "url_name": "analyse_sol:analyses_sol", "icon": "biotech"},
                {"label": _("Fertigation"), "url_name": "agronomie:fertigation", "icon": "opacity"},
                {"label": _("Interventions"), "url_name": "interventions:interventions", "icon": "build"},
                {"label": _("Météo"), "url_name": "meteo:index", "icon": "wb_sunny"},
            ],
        },
        {
            "label": _("Élevage"), "key": "elevage",
            "items": [
                {"label": _("Élevage"), "url_name": "elevage:elevage", "icon": "pets"},
            ],
        },
        {
            # L'eau et son pilotage, d'un bout à l'autre : ce qu'on distribue,
            # ce qui protège la culture, et ce qui mesure. Bassinage et anti-gel
            # sont des usages de l'irrigation, ils n'avaient pas à vivre ailleurs.
            "label": _("Irrigation"), "key": "irrigation",
            "items": [
                {"label": _("Irrigation"), "url_name": "irrigation:irrigation", "icon": "water_drop"},
                {"label": _("Bassinage"), "url_name": "irrigation:bassinage", "icon": "shower"},
                {"label": _("Anti-gel"), "url_name": "irrigation:antigel", "icon": "ac_unit"},
                {"label": _("DTI"), "url_name": "irrigation:dti", "icon": "electric_bolt"},
                {"label": _("Régie SCADA"), "url_name": "iot:regie", "icon": "tune"},
                {"label": _("Capteurs"), "url_name": "iot:capteurs", "icon": "sensors"},
            ],
        },
        {
            "label": _("Finance"), "key": "economie",
            "items": [
                {"label": _("Charges"), "url_name": "finances:charges", "icon": "payments"},
                {"label": _("Fermage"), "url_name": "finances:fermage", "icon": "agriculture"},
                {"label": _("Bilan économique"), "url_name": "finances:bilan_economique", "icon": "insights"},
                {"label": _("Facture"), "url_name": "finances:facturation", "icon": "receipt_long"},
                {"label": _("Devis"), "url_name": "finances:devis", "icon": "request_quote"},
                {"label": _("Logos"), "url_name": "finances:logos", "icon": "branding_watermark"},
                {"label": _("PAC"), "url_name": "pac:pac", "icon": "account_balance"},
            ],
        },
        {
            "label": _("Environnement"), "key": "environnement",
            "items": [
                {"label": _("Biodiversité"), "url_name": "environnement:biodiversite", "icon": "eco"},
                {"label": _("Bilan azoté"), "url_name": "environnement:bilan_azote", "icon": "science"},
                {"label": _("Empreinte carbone"), "url_name": "environnement:empreinte_carbone", "icon": "cloud"},
                {"label": _("Rapport environnemental"), "url_name": "environnement:rapport", "icon": "assessment"},
                {"label": _("Santé végétale"), "url_name": "environnement:sante_vegetale", "icon": "local_florist"},
                {"label": _("Taxonomie EU"), "url_name": "environnement:taxonomie", "icon": "fact_check"},
            ],
        },
        {
            "label": _("RH"), "key": "rh",
            "items": [
                {"label": _("Planning"), "url_name": "planning:planning", "icon": "event"},
                {"label": _("Équipe"), "url_name": "equipe:equipe", "icon": "groups"},
                {"label": _("Tâches"), "url_name": "equipe:taches", "icon": "checklist"},
                {"label": _("Contrats de travail"), "url_name": "equipe:contrats", "icon": "assignment_ind"},
                {"label": _("Banque des postes"), "url_name": "equipe:postes", "icon": "work_history"},
                {"label": _("Offres d'emploi"), "url_name": "equipe:offres", "icon": "work"},
                {"label": _("Paie"), "url_name": "equipe:paie", "icon": "payments"},
            ],
        },
        {
            # Les pièces personnelles : elles ne relèvent ni de l'exploitation
            # ni d'un contrat, et on les cherche toujours dans l'urgence.
            "label": _("Identité"), "key": "identite",
            "url_name": "identite:pieces",
            "items": [
                {"label": _("Carte d'identité"), "url_name": "identite:pieces_type",
                 "args": ["carte"], "icon": "badge"},
                {"label": _("Passeport"), "url_name": "identite:pieces_type",
                 "args": ["passeport"], "icon": "book"},
                {"label": _("Signature"), "url_name": "identite:pieces_type",
                 "args": ["signature"], "icon": "draw"},
            ],
        },
        {
            "label": _("Contrat"), "key": "contrat",
            "items": [
                {"label": _("Contrats"), "url_name": "contrat:contrats", "icon": "description"},
                {"label": _("Drive"), "url_name": "contrat:drive", "icon": "folder"},
                {"label": _("Baux"), "url_name": "contrat:baux", "icon": "agriculture"},
                {"label": _("Patrimoine"), "url_name": "contrat:actes", "icon": "history_edu"},
                {"label": _("Assurance"), "url_name": "contrat:assurances", "icon": "shield"},
                {"label": _("Mutualité Sociale Agricole"), "url_name": "contrat:msa", "icon": "health_and_safety"},
            ],
        },
        {
            "label": _("Vente directe"), "key": "vente",
            "items": [
                {"label": _("Commandes"), "url_name": "vente:commandes", "icon": "receipt_long"},
                {"label": _("Mes produits"), "url_name": "vente:produits", "icon": "local_offer"},
                {"label": _("Ma boutique"), "url_name": "vente:boutique", "icon": "storefront"},
                {"label": _("Le marché"), "url_name": "vente:marche", "icon": "travel_explore"},
            ],
        },
        {
            "label": _("Stock"), "key": "stock",
            "items": [
                {"label": _("Articles"), "url_name": "stock:articles", "icon": "inventory_2"},
                {"label": _("Récoltes"), "url_name": "stock:recoltes", "icon": "agriculture"},
                {"label": _("Mouvements"), "url_name": "stock:mouvements", "icon": "swap_vert"},
                {"label": _("Dépôts"), "url_name": "stock:depots", "icon": "warehouse"},
            ],
        },
        {
            "label": _("Aquaculture"), "key": "aquaculture",
            "items": [
                {"label": _("Bassins"), "url_name": "aquaculture:bassins", "icon": "set_meal"},
                {"label": _("Espèces aquacoles"), "url_name": "aquaculture:especes", "icon": "phishing"},
            ],
        },
        {
            "label": _("Relations"), "key": "relations",
            "items": [
                {"label": _("Clients"), "url_name": "client:clients", "icon": "handshake"},
                {"label": _("Bailleur"), "url_name": "client:partenaires", "args": ["bailleur"], "icon": "real_estate_agent"},
                {"label": _("Comptable"), "url_name": "client:partenaires", "args": ["comptable"], "icon": "calculate"},
                {"label": _("Avocat"), "url_name": "client:partenaires", "args": ["avocat"], "icon": "balance"},
                {"label": _("CUMA"), "url_name": "client:partenaires", "args": ["cuma"], "icon": "agriculture"},
            ],
        },
        {
            "label": _("Mon exploitation"), "key": "compte",
            # La section elle-même mène à la vue d'ensemble.
            "url_name": "exploitations:settings",
            "items": [
                {"label": _("Identités"), "url_name": "exploitations:section_identite", "icon": "badge"},
                {"label": _("Photo de la ferme"), "url_name": "exploitations:photo", "icon": "photo_camera"},
                {"label": _("Juridique"), "url_name": "exploitations:section_juridique", "icon": "gavel"},
                {"label": _("Contact"), "url_name": "exploitations:contact", "icon": "call"},
                {"label": _("Localisation"), "url_name": "exploitations:section_localisation", "icon": "place"},
                {"label": _("Caractéristiques agricoles"), "url_name": "exploitations:section_caracteristiques", "icon": "agriculture"},
                {"label": _("Eau"), "url_name": "exploitations:section_eau", "icon": "water_drop"},
                {"label": _("Certificats et labels"), "url_name": "exploitations:section_certifications", "icon": "verified"},
                {"label": _("Sociétés liées"), "url_name": "exploitations:societes", "icon": "domain"},
                {"label": _("RH et CA"), "url_name": "exploitations:section_economique", "icon": "payments"},
                {"label": _("Parc matériel"), "url_name": "operations:parc_materiel", "icon": "agriculture"},
            ],
        },
    ]

    # ── Filtrage par espace ──────────────────────────────────────────────
    # Un employé ou un bailleur ne voit qu'une part de la nav. Le filtre est
    # appliqué ici, avant la résolution des URL : inutile de reverse() des
    # entrées qui ne seront pas affichées. Une section vidée disparaît.
    espace_actif = getattr(request, "espace", None)

    # Sans rattachement, on se règle sur le profil déclaré : quelqu'un qui
    # vient de se dire employé voit la forme de son futur espace, pas toute
    # l'application dont chaque lien le mènerait à un refus.
    espace_affiche = espace_actif or getattr(getattr(request, "user", None), "profil_souhaite", "")

    # Une entrée peut se réserver à des espaces précis (clé `espaces`) : elle
    # disparaît partout ailleurs, y compris chez l'exploitant qui voit tout.
    def _reservee(entree):
        reserve = entree.get("espaces")
        return reserve is not None and espace_affiche not in reserve

    nav_primary = [e for e in nav_primary if not _reservee(e)]
    for section in nav_sections:
        section["items"] = [i for i in section["items"] if not _reservee(i)]

    autorisees = espaces_service.nav_autorisee(espace_affiche)
    if autorisees is not None:
        nav_primary = [e for e in nav_primary if e["url_name"] in autorisees]
        for section in nav_sections:
            section["items"] = [i for i in section["items"] if i["url_name"] in autorisees]
    nav_sections = [s for s in nav_sections if s["items"]]

    # Icône de section (présentation ; navigation de la sidebar)
    section_icons = {
        "accueil": "home", "communication": "forum", "cultures": "eco",
        "elevage": "pets", "irrigation": "water_drop",
        "economie": "payments", "environnement": "park",
        "rh": "groups", "contrat": "gavel", "relations": "handshake",
        "stock": "inventory_2", "vente": "storefront",
        "aquaculture": "waves", "compte": "person",
    }
    for section in nav_sections:
        section["icon"] = section_icons.get(section["key"], "folder")

    # Sections triées par ordre alphabétique (selon le libellé dans la langue active)
    nav_sections.sort(key=lambda section: _sort_key(section["label"]))
    # Items de chaque sous-section triés par ordre alphabétique
    for section in nav_sections:
        section["items"].sort(key=lambda item: _sort_key(item["label"]))

    # Lien de chaque entrée, résolu ici : c'est le seul endroit qui sait passer
    # des arguments d'URL (ex. le type d'un partenaire) au reverse.
    for section in nav_sections:
        for item in section["items"]:
            try:
                href = reverse(item["url_name"], args=item.get("args") or [])
            except NoReverseMatch:
                href = ""
            item["href"] = f"{href}#{item['anchor']}" if href and item.get("anchor") else href
        if section.get("url_name"):
            try:
                section["href"] = reverse(section["url_name"])
            except NoReverseMatch:
                section["href"] = ""

    # Section + item contenant la page courante (le panneau volant reste ouvert dessus)
    current = getattr(getattr(request, "resolver_match", None), "view_name", None)
    current_ns = current.split(":")[0] if current else ""

    all_items = [(section, item) for section in nav_sections for item in section["items"]]
    ns_counts = {}
    for section, item in all_items:
        ns = item["url_name"].split(":")[0]
        ns_counts[ns] = ns_counts.get(ns, 0) + 1

    active_section, active_url_name = "", ""
    # 1) correspondance exacte du nom de vue
    for section, item in all_items:
        if item["url_name"] == current:
            active_section, active_url_name = section["key"], item["url_name"]
            break
    # 1 bis) page portée par la section elle-même (son libellé est un lien) :
    # aucune sous-entrée ne correspond, mais la section doit rester ouverte.
    if not active_section:
        for section in nav_sections:
            if section.get("url_name") == current:
                active_section = section["key"]
                break
    # 2) repli par namespace (sous-pages : detail, create…) si le namespace est unique dans la nav
    if not active_url_name and current_ns and ns_counts.get(current_ns) == 1:
        for section, item in all_items:
            if item["url_name"].split(":")[0] == current_ns:
                active_section, active_url_name = section["key"], item["url_name"]
                break

    # Compteur de notifications non lues (badge sur la cloche du header)
    unread_notifications = 0
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        unread_notifications = user.notifications.filter(read=False).count()

    # Section ouverte par défaut dans le panneau latéral (jamais vide → toujours visible)
    default_section = active_section or (nav_sections[0]["key"] if nav_sections else "")

    # Sélecteur d'espace : les trois sont toujours affichés, ceux sans
    # rattachement étant rendus inertes. Les masquer ferait disparaître le
    # sélecteur entier chez la plupart des comptes, qui n'ont qu'un espace.
    # Un espace indisponible n'est pas un cul-de-sac : quand une action existe
    # pour l'ouvrir (« définir les membres de l'équipe » → espace employé) et que
    # l'espace courant y donne droit, l'icône devient un lien vers cet écran.
    disponibles = espaces_service.espaces_disponibles(request) if hasattr(request, "session") else []
    courant = getattr(request, "espace", None)
    espaces = []
    for e in espaces_service.ESPACES:
        entree = dict(e, disponible=e["cle"] in disponibles)
        action = e.get("action")
        if not entree["disponible"] and action and action["depuis"] == courant:
            try:
                entree["action_url"] = reverse(action["vue"])
                entree["action_libelle"] = action["libelle"]
            except NoReverseMatch:
                pass
        espaces.append(entree)

    return {
        "APP_NAME": getattr(settings, "APP_NAME", "Isidor"),
        "espaces": espaces,
        # Le sélecteur marque l'espace où l'on se trouve. Sans rattachement,
        # c'est le profil déclaré qui fait foi : on est bien sur son tableau de
        # bord, même si l'espace n'est pas encore ouvert.
        "espace_courant": espace_affiche,
        "nav_primary": nav_primary,
        "nav_sections": nav_sections,
        "active_section": active_section,
        "active_url_name": active_url_name,
        "default_section": default_section,
        # Espace externe (client) : l'ossature masque ce qu'il ne peut pas ouvrir.
        "espace_ferme": espaces_service.est_ferme(espace_actif),
        "unread_notifications": unread_notifications,
        "css_version": _css_version(),
    }
