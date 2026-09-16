"""Banque des postes : les postes courants d'une exploitation agricole.

Deux sources se proposent quand on écrit un intitulé : les postes que la ferme
a enregistrés dans sa banque, puis les postes courants ci-dessous. Ces
derniers servent tant que la banque est vide ou incomplète ; les importer en
fait des postes de la ferme, qu'elle retouche sans que la mise à jour de
l'application les écrase.

Aucune qualification n'est proposée d'office : la grille dépend de la
convention collective applicable, et une valeur par défaut serait fausse pour
une partie des exploitations.
"""

from django.utils.translation import gettext_lazy as _

#: Intitulé, missions, profil. L'ordre est celui de la liste proposée.
POSTES_COURANTS = (
    (_("Ouvrier agricole polyvalent"),
     _("Travaux des cultures selon la saison : préparation des sols, semis, entretien, récolte. "
       "Entretien courant du matériel et des bâtiments."),
     _("Goût du travail en extérieur et bonne condition physique. Permis B apprécié.")),
    (_("Ouvrier viticole"),
     _("Travaux de la vigne tout au long de l'année : taille, tirage des bois, palissage, "
       "ébourgeonnage, relevage, vendanges."),
     _("Première expérience en vigne appréciée. Travail en extérieur par tous les temps.")),
    (_("Tailleur de vigne"),
     _("Taille de la vigne selon le mode de conduite de l'exploitation, tirage des bois, "
       "entretien des outils de taille."),
     _("Maîtrise de la taille ou volonté d'apprendre. Soin et régularité.")),
    (_("Ouvrier arboricole"),
     _("Taille et éclaircissage des arbres, entretien des vergers, récolte et tri des fruits."),
     _("Aisance sur escabeau ou plateforme. Rigueur sur la qualité des fruits.")),
    (_("Ouvrier maraîcher"),
     _("Plantation et entretien des cultures sous abri et de plein champ, récolte, lavage et "
       "conditionnement des légumes."),
     _("Goût du travail manuel et du végétal. Rapidité et soin.")),
    (_("Saisonnier de récolte"),
     _("Récolte manuelle des fruits ou légumes, tri et mise en caisse selon les consignes de "
       "qualité."),
     _("Débutant accepté. Endurance et respect des consignes.")),
    (_("Tractoriste"),
     _("Conduite du tracteur et des outils attelés pour les travaux du sol, les semis, les "
       "traitements et le transport. Entretien courant du matériel."),
     _("Expérience de la conduite d'engins agricoles. Permis B, Certiphyto apprécié.")),
    (_("Conducteur d'engins de récolte"),
     _("Conduite de moissonneuse-batteuse, d'ensileuse ou de machine à vendanger ; réglages et "
       "entretien courant."),
     _("Expérience des machines de récolte. Disponibilité pendant les chantiers.")),
    (_("Agent d'élevage bovin"),
     _("Alimentation et soins aux animaux, surveillance sanitaire, paillage et curage, suivi des "
       "vêlages. Participation aux cultures fourragères."),
     _("Aisance avec les animaux. Disponibilité certains week-ends.")),
    (_("Agent de traite"),
     _("Traite du matin et du soir, nettoyage de la salle de traite et du matériel, soins aux "
       "animaux, suivi de la qualité du lait."),
     _("Ponctualité et rigueur d'hygiène. Expérience de la traite appréciée.")),
    (_("Agent d'élevage porcin"),
     _("Alimentation et surveillance des animaux, conduite des bandes, soins et hygiène des "
       "bâtiments, respect de la biosécurité."),
     _("Rigueur sur l'hygiène et les protocoles sanitaires.")),
    (_("Agent d'élevage avicole"),
     _("Surveillance des lots, alimentation et abreuvement, ramassage des œufs, nettoyage et "
       "désinfection des bâtiments."),
     _("Rigueur sur l'hygiène et la biosécurité. Observation des animaux.")),
    (_("Berger"),
     _("Conduite et surveillance du troupeau, soins aux animaux, gestion du pâturage, "
       "participation à l'agnelage."),
     _("Autonomie, goût du plein air, aisance avec les chiens de troupeau.")),
    (_("Agent piscicole"),
     _("Alimentation des poissons, surveillance de la qualité de l'eau, tri et pêche des bassins, "
       "entretien des installations."),
     _("Goût du travail en milieu humide. Rigueur dans les relevés.")),
    (_("Chef de culture"),
     _("Organisation et suivi des cultures, planification des travaux, encadrement de l'équipe, "
       "suivi des traitements et de la fertilisation, enregistrements réglementaires."),
     _("Formation agricole et expérience d'encadrement. Certiphyto décideur.")),
    (_("Chef d'équipe"),
     _("Organisation du travail d'une équipe, répartition des tâches, contrôle de la qualité et de "
       "la sécurité, lien avec le chef d'exploitation."),
     _("Expérience du terrain et de l'encadrement. Sens de l'organisation.")),
    (_("Mécanicien agricole"),
     _("Entretien, diagnostic et réparation du matériel agricole et des installations de "
       "l'exploitation."),
     _("Formation en mécanique ou machinisme agricole. Autonomie.")),
    (_("Ouvrier de chai"),
     _("Réception de la vendange, travaux de vinification, soutirages, entretien du chai et du "
       "matériel, mise en bouteille."),
     _("Rigueur d'hygiène. Expérience en cave appréciée.")),
    (_("Agent de conditionnement"),
     _("Tri, calibrage, emballage et étiquetage des produits ; préparation des commandes ; respect "
       "des règles d'hygiène."),
     _("Soin, rapidité et respect des consignes de qualité.")),
    (_("Vendeur à la ferme"),
     _("Accueil et conseil des clients, tenue de la boutique ou du stand de marché, encaissement, "
       "mise en rayon."),
     _("Sens du contact et connaissance des produits de la ferme.")),
)


def courants():
    """Les postes courants, sous la forme des postes de la banque."""
    return [{"intitule": str(intitule), "qualification": "", "missions": str(missions),
             "profil": str(profil), "source": "courant"}
            for intitule, missions, profil in POSTES_COURANTS]


def suggestions(exploitation):
    """La banque de la ferme d'abord, puis les postes courants qu'elle n'a pas.

    Un intitulé déjà dans la banque masque son homonyme courant, casse
    ignorée : c'est la version de la ferme qui fait foi.
    """
    from .models import Poste

    banque = [{"intitule": p.intitule, "qualification": p.qualification,
               "missions": p.missions, "profil": p.profil, "source": "banque"}
              for p in Poste.objects.filter(exploitation=exploitation)] if exploitation else []
    connus = {p["intitule"].casefold() for p in banque}
    return banque + [p for p in courants() if p["intitule"].casefold() not in connus]
