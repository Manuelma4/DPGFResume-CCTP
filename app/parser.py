from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from .extractors import ExtractedDocument, TextBlock


NUMBERED_HEADING = re.compile(
    r"^\s*(?:ARTICLE\s+)?"
    r"(?P<code>[A-Z]?\d{1,3}(?:[.\-]\d{1,3}){0,6})[.)]?"
    r"(?:\s*[-–—:]\s*|\s+)(?P<title>\S.+?)\s*$",
    re.IGNORECASE,
)
LOT_PATTERN = re.compile(
    # Le séparateur entre le code et le titre est le plus souvent un tiret
    # ("LOT 05 — MENUISERIES") mais certains CCTP réels l'omettent complètement
    # ("CCTP LOT 6 CVC - DESENFUMAGE" : rien entre "6" et "CVC", le tiret
    # n'arrive que plus loin) — un simple espace doit donc aussi être accepté.
    r"\bLOT\s*(?:N[°O]\s*)?(?P<code>[A-Z]?\d{1,3}(?:[.\-]\d{1,3})?)"
    r"(?:\s*[-–—:]\s*|\s+)(?P<title>[^\n|]{3,100})",
    re.IGNORECASE,
)
TOC_PATTERN = re.compile(r"\.{3,}\s*\d+\s*$")
WORK_ANCHOR = re.compile(
    r"\b(?:description|descriptif)\s+(?:d[eu']\s+|des\s+)?"
    r"(?:ouvrages?|travaux|prestations?)\b",
    re.IGNORECASE,
)
OPTION_PATTERN = re.compile(
    r"\b(?:option|variante|pse|prestation\s+supplémentaire)\b", re.IGNORECASE
)
EXPLICIT_QUANTITY = re.compile(
    r"\b(?:quantité|qté|nombre)\s*(?:totale?)?\s*(?:[:=]\s*)?"
    r"(?P<value>\d+(?:[.,]\d+)?)\b",
    re.IGNORECASE,
)
EXPLICIT_UNIT = re.compile(
    r"\b(?:unité(?:\s+de\s+(?:règlement|mesure|prix))?|u\.o\.)\s*[:=]\s*"
    r"(?P<unit>m[²2]|m[³3]|ml|u(?:nité)?|ens(?:emble)?|forfait|kg|h(?:eure)?|mois)\b",
    re.IGNORECASE,
)

# Ce catalogue mots-clé → unité est validé sur des DPGF réellement produits par
# Moduo : minage de 2179 DPGF (projets 2022) puis, pour cette passe, 59
# projets 2023-2026 avec CCTP+DPGF appariés lot par lot (5825 lots), avec un
# clustering par signature de tokens normalisés (support ≥ 15 occurrences,
# pureté ≥ 90 % d'une unité dominante). Les entrées "unite" sont classées par
# ordre de priorité : la première expression qui correspond l'emporte — les
# exceptions ci-dessous passent donc avant les groupes génériques qu'elles
# contredisent (ex. "isolation acoustique" n'est pas un ouvrage au m²).
UNIT_RULES: list[tuple[str, float, re.Pattern[str]]] = [
    (
        "Ens",
        0.85,
        re.compile(r"\bisolation acoustique\b", re.IGNORECASE),
    ),
    (
        "U",
        0.87,
        re.compile(
            r"\b(?:unites?|porte|fenetre|chassis|trappe|regard|caniveau|appareil|"
            r"equipement|robinet|lavabo|vasque|wc|urinoir|evier|radiateur|"
            r"ventilo\w*|ventilateurs?|"
            r"extracteur|escalier|echelle|store|luminaire|"
            r"tableau|coffret|prise|interrupteur|detecteur|extincteur|"
            r"pompe|siphons?|douche|miroir|seche[\s-]mains?|"
            r"barre d'appui|borne|sprinklers?|gaches?|serrures?|"
            r"telecommandes?|compteurs?|manometres?|bouches?|vannes?|filtres?|"
            r"thermometres?|convecteurs?|inters?|baes|disconnecteurs?|descentes?|"
            r"boutons?[\s-]?poussoirs?|clapets?|purgeurs?|sondes?|"
            r"extraction des locaux humides)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ml",
        0.88,
        re.compile(
            r"\bisolation\b.*\b(?:canalisations?|evacuations?|tuyaux?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ml",
        0.86,
        re.compile(
            # "canalisations", "cablage/cables" et "reseau" seuls ont été
            # retirés d'ici : sur le corpus réel ils penchent en fait vers Ens
            # (49-61 % de leurs occurrences), pas assez purs pour être une
            # règle fiable. "descente" penche à 81 % vers U, déplacée ci-dessous.
            r"\b(?:tuyauteries?|fourreaux?|plinthes?|"
            r"bordure|gouttiere|profile|main[\s-]courante|"
            r"garde-corps|releves? d'etancheite|couronnements? d'acrotere|"
            r"bavettes?|linteaux?|grilles? anti-rongeurs?|encadrements? de baies?|"
            r"habillages?|baguettes? aluminium|barres? de seuils?|nez de marche|"
            r"seuils?|chemins? de cables?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "m²",
        0.88,
        re.compile(
            r"\b(?:peinture|enduit|revetement|carrelage|faience|sol souple|"
            r"etancheite|isolation|doublage|cloison|plafond|bardage|toiture|"
            r"membrane|chape|ragreage|moquette|bac acier|pare-vapeur|"
            r"reglage de fond de forme|geotextiles?|platres?|"
            r"peau (?:interieure|exterieure)|engazonnement)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "m³",
        0.82,
        re.compile(
            # "remblai" seul ne couvrait pas ses formes dérivées réelles
            # ("remblaiements", très fréquent en Structure/Gros Œuvre :
            # 97 % de pureté m³ sur 30 projets réels /Volumes/PARTAGE).
            r"\b(?:terrassements?|deblais?|remblais?(?:ements?)?|betons?|grave|"
            r"terre vegetale|fouilles?|evacuation des terres|longrines?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "kg",
        0.78,
        re.compile(r"\b(?:acier|armature|charpente metallique)\b", re.IGNORECASE),
    ),
    (
        "Ens",
        0.82,
        re.compile(
            r"\b(?:installation|preparation|etudes?|plans?|dossier|essais?|"
            r"controle|mise en service|nettoyage|depose|demolition|"
            r"signalisation|implantation|repliement|protection|reservations?|"
            r"constat des lieux|accessoires|ouvrages complementaires|recolement|"
            r"base[\s-]vie|aire de chantier|bennes?|recettage|formations?|"
            r"branchements? provisoires?|calfeutrements?|percements?|"
            r"evacuations?|raccordements?|calorifuges?|etiquetage|reperage|"
            r"sterilisation du reseau|constat d'huissier|gestion des dechets|"
            r"registres? de reglage|contrat d'entretien|coupe[\s-]feu|"
            r"silencieux|pieges? a son)\b",
            re.IGNORECASE,
        ),
    ),
]

# Un objet du CCTP se retrouve régulièrement décomposé en plusieurs lignes du
# DPGF, dont certaines n'ont pas d'équivalent textuel dans le CCTP source
# (calibres normalisés, ouvrages complémentaires systématiques...). Chaque
# règle ci-dessous vient d'un rapprochement CCTP<->DPGF réel, lot par lot, sur
# les projets 2023-2026, et n'a été retenue que si le même couple
# concept-parent -> extra apparaît dans au moins 3 projets distincts (pas
# seulement plusieurs fois dans le même DCE). Les lignes ajoutées par ces
# règles sont marquées `origin="rule-derived"` et restent `to_review` : elles
# ne prétendent jamais avoir été lues dans le CCTP.
DecompositionChild = tuple[str, str]  # (désignation, unité)
DECOMPOSITION_RULES: list[tuple[re.Pattern[str], list[DecompositionChild]]] = [
    (
        re.compile(r"tubes?\s+(?:en\s+)?cuivre", re.IGNORECASE),
        [
            ("Tube cuivre diam. 12/14", "ml"),
            ("Tube cuivre diam. 14/16", "ml"),
            ("Tube cuivre diam. 16/18", "ml"),
            ("Tube cuivre diam. 20/22", "ml"),
            ("Tube cuivre diam. 30/32", "ml"),
            ("Calorifuge antigel épaisseur 19 mm", "Ens"),
            ("Calorifuge anticondensation épaisseur 13 mm", "Ens"),
        ],
    ),
    (
        re.compile(r"tubes?\s+(?:en\s+)?pvc", re.IGNORECASE),
        [
            ("Tube PVC diam. 40", "ml"),
            ("Tube PVC diam. 50", "ml"),
            ("Tube PVC diam. 63", "ml"),
            ("Tube PVC diam. 75", "ml"),
            ("Tube PVC diam. 100", "ml"),
            ("Tube PVC diam. 110", "ml"),
            ("Tube PVC diam. 125", "ml"),
        ],
    ),
    (
        re.compile(r"tubes?\s+(?:en\s+)?per\b", re.IGNORECASE),
        [
            ("Tube PER Ø12", "ml"),
            ("Tube PER Ø16", "ml"),
            ("Tube PER Ø20", "ml"),
        ],
    ),
    (
        re.compile(r"vannes?\s+d['’]?\s*(?:isolement|arret)", re.IGNORECASE),
        [
            ("Vanne d'isolement DN 15", "U"),
            ("Vanne d'isolement DN 20", "U"),
            ("Vanne d'isolement DN 25", "U"),
            ("Vanne d'équilibrage DN", "U"),
        ],
    ),
    (
        re.compile(r"fourreaux?\s+rouges?", re.IGNORECASE),
        [
            ("Fourreau rouge DN63", "ml"),
            ("Fourreau rouge DN90", "ml"),
            ("Fourreau rouge DN160", "ml"),
            ("Chambre de tirage — taille 40 x 40 cm avec fonte B125", "U"),
            ("Chambre de tirage — taille 80 x 80 cm avec fonte C250", "U"),
            ("Chambre de tirage — taille 80 x 80 cm avec fonte D400", "U"),
        ],
    ),
    (
        re.compile(r"reseaux?\s+divers", re.IGNORECASE),
        [
            ("Tranchée 1 réseau", "ml"),
            ("Tranchée 2 réseaux", "ml"),
            ("Tranchée 3 réseaux", "ml"),
            ("Tranchée 4 réseaux", "ml"),
            ("Tranchée 5 réseaux", "ml"),
        ],
    ),
    (
        re.compile(r"travaux\s+generaux", re.IGNORECASE),
        [
            ("Implantation, piquetage, dossier EXE, dossier DOE", "Ens"),
            (
                "Installation de chantier VRD : base vie, entretien, "
                "barriérage, balisage et signalisation",
                "Ens",
            ),
        ],
    ),
    (
        re.compile(
            r"ouvrages?\s+d['’]?\s*assainissement|"
            r"collecteurs?\s+d['’]?\s*assainissement|"
            r"\bremblais?\b",
            re.IGNORECASE,
        ),
        [
            ("Regard de visite diamètre 1000 avec fonte D400", "U"),
            ("Regard de branchement 60x60 avec fonte B125", "U"),
            ("Regard de branchement 80x80 avec fonte B125", "U"),
            ("Contrôle qualité des ouvrages d'assainissement (étanchéité RV)", "Ens"),
            (
                "Contrôle qualité des collecteurs d'assainissement "
                "(curage, étanchéité, ITV)",
                "Ens",
            ),
        ],
    ),
    (
        re.compile(r"controle\s+qualite.*(?:compactage|couche\s+de\s+forme)", re.IGNORECASE),
        [
            ("Contrôle qualité des ouvrages d'assainissement (étanchéité RV)", "Ens"),
            (
                "Contrôle qualité des collecteurs d'assainissement "
                "(curage, étanchéité, ITV)",
                "Ens",
            ),
            (
                "Contrôle qualité des enrobés : contrôle de densité, "
                "carottage pour contrôle des épaisseurs",
                "Ens",
            ),
        ],
    ),
    (
        re.compile(r"\bvoirie\b", re.IGNORECASE),
        [
            ("Géotextile classe V", "m²"),
            ("Réglage de fond de forme", "m²"),
            ("Couche de forme en grave non traitée 0/31.5", "m³"),
            ("Fourniture et mise en œuvre de BBSG 0/10 pour couche de surface", "m²"),
        ],
    ),
    (
        re.compile(r"cloison\s+\d{2,3}[/.]\d{2,3}", re.IGNORECASE),
        [
            ("Plus-value plaque hydrofuge", "m²"),
            ("Doublage 1/2 stil isolant", "m²"),
        ],
    ),
    (
        re.compile(r"encadrements?\s+de\s+baies?", re.IGNORECASE),
        [
            ("Bavette basse des ouvertures", "ml"),
            ("Linteau de baies", "ml"),
        ],
    ),
    (
        re.compile(r"support\s+d['’]?\s*etancheite", re.IGNORECASE),
        [
            ("Bac acier sur charpente", "m²"),
            ("Ouvrages complémentaires : points spécifiques", "Ens"),
        ],
    ),
    (
        re.compile(r"\bappareillage\b", re.IGNORECASE),
        [
            ("Interrupteur va-et-vient couleur blanc", "U"),
            ("Détecteur de présence/mouvement", "U"),
            ("Prise 2P+T standard encastrée couleur blanc", "U"),
        ],
    ),
    (
        re.compile(r"ossatures?\s+de\s+facade", re.IGNORECASE),
        [
            ("Encadrements de menuiseries", "kg"),
            ("Lisses support de bardage", "kg"),
            ("Chevêtres techniques", "Ens"),
        ],
    ),
    (
        re.compile(r"ossatures?\s+de\s+toiture|\bpannes\b", re.IGNORECASE),
        [
            ("Chevêtres EP", "U"),
            ("Chevêtres de lanternaux", "U"),
        ],
    ),
    (
        re.compile(r"nettoyage\s+de\s+la\s+chaussee", re.IGNORECASE),
        [
            ("Marquage normalisé place PMR", "U"),
            ("Marquage bande STOP", "m²"),
            ("F&P de panneau AB4 « STOP »", "U"),
        ],
    ),
    (
        re.compile(r"lecteur\s+vigik", re.IGNORECASE),
        [
            ("Badge Vigik", "U"),
            ("BP de sortie NO/NF lumineux loi Handicap", "U"),
        ],
    ),
    (
        re.compile(r"eclairage\s+de\s+securite|\bbaes\b", re.IGNORECASE),
        [
            ("BAES étanche", "U"),
            ("Dépose / repose des BAES existants", "Ens"),
        ],
    ),
    (
        re.compile(r"prise\s+rj\s?45", re.IGNORECASE),
        [
            ("Test et recette de l'installation, cuivre et optique", "Ens"),
            ("Recettage", "Ens"),
        ],
    ),
    (
        re.compile(r"beton\s+de\s+proprete", re.IGNORECASE),
        [
            ("Gros béton de rattrapage", "m³"),
        ],
    ),
    (
        re.compile(r"trappes?\s+de\s+visite", re.IGNORECASE),
        [
            ("Protection coupe-feu", "Ens"),
        ],
    ),
    (
        re.compile(r"bennes?\s+de\s+chantier", re.IGNORECASE),
        [
            ("Moyens de levage et accès chantier", "Ens"),
            ("Aménagement de l'aire de chantier", "Ens"),
        ],
    ),
    (
        re.compile(r"sterilisation\s+du\s+reseau", re.IGNORECASE),
        [
            ("Repérage étiquetage", "Ens"),
            ("Analyse d'eau", "Ens"),
        ],
    ),
    (
        re.compile(r"chambres?\s+de\s+tirage", re.IGNORECASE),
        [
            ("Chambre de tirage — type L1T", "U"),
            ("Chambre de tirage — taille 40 x 40 cm avec fonte B125", "U"),
        ],
    ),
    (
        re.compile(r"\bfilets\b", re.IGNORECASE),
        [
            ("Garde-corps", "ml"),
            ("Accès toiture phase chantier", "Ens"),
        ],
    ),
    (
        re.compile(r"reprise\s+des\s+sorties\s+en\s+pied\s+de\s+batiment", re.IGNORECASE),
        [
            ("Raccordement sur le réseau existant", "Ens"),
            ("Station de refoulement EU 4 l/s", "Ens"),
        ],
    ),
    (
        re.compile(r"releves?\s+d['’]?\s*etancheite", re.IGNORECASE),
        [
            ("Thermosoudage des supports sur la membrane", "ml"),
        ],
    ),
    (
        re.compile(r"\bragreage\b", re.IGNORECASE),
        [
            ("Système d'étanchéité faïence murale", "m²"),
        ],
    ),
    (
        re.compile(r"espaces?\s+verts?", re.IGNORECASE),
        [
            ("Préparations avant plantations et engazonnement", "m²"),
        ],
    ),
    (
        re.compile(r"\bonduleurs?\b", re.IGNORECASE),
        [
            ("Câbles alternatif RO2V des onduleurs à l'armoire AC", "Ens"),
            ("Chemins de câbles en dalle en acier galvanisé à chaud", "ml"),
        ],
    ),
    (
        re.compile(r"\bthermometres?\b", re.IGNORECASE),
        [
            ("Clapet anti-retour DN", "U"),
            ("Filtre DN", "U"),
        ],
    ),
    (
        re.compile(r"robinet\s+de\s+purge", re.IGNORECASE),
        [
            ("Purgeur d'air automatique isolable", "U"),
        ],
    ),
    (
        re.compile(r"vanne\s+de\s+regulation", re.IGNORECASE),
        [
            ("Boîtier de commande et de régulation", "U"),
            ("Pompe de relevage des condensats montée d'usine", "U"),
            ("Liaison et raccordement", "Ens"),
        ],
    ),
    (
        re.compile(r"clapet\s+antipollution", re.IGNORECASE),
        [
            ("Anti-bélier", "Ens"),
        ],
    ),
    (
        re.compile(r"pieges?\s+a\s+son", re.IGNORECASE),
        [
            ("Bouche de soufflage débit > 200 m3/h", "U"),
        ],
    ),
    (
        re.compile(r"branchement\s+du\s+batiment", re.IGNORECASE),
        [
            ("Vanne DN", "U"),
        ],
    ),
    (
        re.compile(r"calorifugeage\s+du\s+reseau\s+eau\s+froide", re.IGNORECASE),
        [
            ("Calorifuge anticondensation épaisseur 13 mm", "Ens"),
        ],
    ),
    (
        re.compile(r"garantie\s+de\s+reprise", re.IGNORECASE),
        [
            ("Contrat d'entretien", "Ens"),
        ],
    ),
    (
        re.compile(r"\bregulation\b", re.IGNORECASE),
        [
            ("Purgeur d'air", "Ens"),
        ],
    ),
]

# Certains mots sont ambigus au global (câblage, câbles, réseau, canalisation
# ne dépassent pas 60 % de pureté sur l'unité tous lots confondus) mais
# deviennent fiables une fois qu'on sait de quel corps d'état vient le lot :
# minage réel 2023-2026, groupé par LOT_FAMILY_RULES. Ex. "canalisation" est
# ml à 96 % dans un lot VRD (des tranchées) mais Ens à 56-67 % dans un lot
# électricité/CVC (un forfait de câblage). Ces règles priment sur UNIT_RULES
# quand le lot est classé et que le mot y est effectivement pur.
FAMILY_UNIT_OVERRIDES: dict[str, list[tuple[str, float, re.Pattern[str]]]] = {
    "vrd": [
        (
            "ml",
            0.9,
            re.compile(r"\b(?:canalisations?|cables?|cablage)\b", re.IGNORECASE),
        ),
    ],
    "electricite": [
        (
            "Ens",
            0.75,
            re.compile(r"\b(?:cablage|reseau)\b", re.IGNORECASE),
        ),
    ],
    "cvc": [
        (
            "Ens",
            0.7,
            re.compile(r"\b(?:reseau|cablage|canalisations?|cables?)\b", re.IGNORECASE),
        ),
        # Minage réel 2026, deux passes :
        # 1) 3 CCTP/DPGF (Océania Lot 16, Quaero Lot 12, IFO_MAR Lot 06) —
        #    donnait à tort "réseaux aérauliques" → ml et "passerelle"/
        #    "automate" → U sur la seule foi de 1-2 documents.
        # 2) 96 projets réels dépareillés depuis /Volumes/PARTAGE/ME/B_PROJETS
        #    (17 617 couples désignation/unité) — a contredit "réseaux
        #    aérauliques" (en réalité Ens à 80 %, 10 projets) et n'a trouvé
        #    quasiment aucune preuve pour "passerelle" (67 %, 9 projets) ni
        #    "automate" (2 projets seulement) : ces deux dernières règles sont
        #    retirées faute de preuve, pas remplacées.
        (
            "Ens",
            0.78,
            re.compile(r"reseaux?\s+aerauliques?", re.IGNORECASE),
        ),
        (
            "U",
            0.85,
            re.compile(
                r"vases?\s+d['’]?\s*expansion|\bcaissons?\b|\bboitiers?\b|"
                r"\bsoupapes?\b|\btamis\b|desemboueurs?",
                re.IGNORECASE,
            ),
        ),
        (
            "Ens",
            0.72,
            re.compile(r"\bregulation\b|\bcollecteurs?\b", re.IGNORECASE),
        ),
    ],
    "desamiantage_demolition": [
        (
            "Ens",
            0.75,
            re.compile(r"\b(?:reseau|cables?|cablage|canalisations?)\b", re.IGNORECASE),
        ),
    ],
    # Les 6 blocs suivants viennent du même minage réel à grande échelle que
    # "cvc" ci-dessus (662 couples projet/oficio, /Volumes/PARTAGE/ME/B_PROJETS,
    # 2026) — mots retenus seulement à ≥6 projets indépendants et ≥78 % de
    # pureté dominante. La plupart du vocabulaire déjà couvert par UNIT_RULES
    # (vanne, calorifuge, robinet, filtre, purgeur, clapet, manomètre,
    # étanchéité, cloison, doublage, plafond, plâtre...) ressort exactement
    # avec la même unité sur cet échantillon indépendant : bonne confirmation,
    # aucun changement nécessaire là où c'est déjà couvert.
    "fondations_gros_oeuvre": [
        (
            "m²",
            0.85,
            re.compile(r"\bmaconnerie\b", re.IGNORECASE),
        ),
        (
            "U",
            0.78,
            re.compile(r"\bpanneau\w*\b", re.IGNORECASE),
        ),
        (
            "Ens",
            0.8,
            re.compile(r"\bbranchements?\b|\bconstat\b", re.IGNORECASE),
        ),
    ],
    "plomberie_sanitaire": [
        (
            "Ens",
            0.88,
            re.compile(r"\bdegorgements?\b|\btampons?\b|\bcondensation\b", re.IGNORECASE),
        ),
        (
            "ml",
            0.85,
            re.compile(r"\bdiam\b", re.IGNORECASE),
        ),
    ],
    "serrurerie_metallerie": [
        (
            "U",
            0.85,
            re.compile(
                r"\bvantaux\b|\bvantail\b|\bportillons?\b|\bbutoirs?\b",
                re.IGNORECASE,
            ),
        ),
    ],
    "couverture_etancheite_bardage": [
        (
            "ml",
            0.85,
            re.compile(r"\bcouvertines?\b", re.IGNORECASE),
        ),
        (
            "ml",
            0.78,
            # Contredit la règle générique ("descente" penche vers U tous
            # lots confondus) : en couverture/bardage il s'agit quasi
            # toujours de descentes d'eaux pluviales facturées au mètre
            # linéaire (80 % ml, 10 projets réels) — cas d'école de mot
            # ambigu au global mais pur une fois le lot connu.
            re.compile(r"\bdescentes?\b", re.IGNORECASE),
        ),
    ],
    "menuiserie_exterieure": [
        (
            "U",
            0.8,
            re.compile(r"\bimpostes?\b", re.IGNORECASE),
        ),
        (
            "ml",
            0.72,
            re.compile(r"\bappuis\b", re.IGNORECASE),
        ),
    ],
    "menuiserie_interieure": [
        (
            "Ens",
            0.8,
            re.compile(r"\bsignaletique\b", re.IGNORECASE),
        ),
        (
            "U",
            0.72,
            re.compile(r"\bplacards?\b", re.IGNORECASE),
        ),
    ],
    "cloisons_doublages_plafonds": [
        (
            "m²",
            0.85,
            re.compile(
                r"\bhydrofuges?\b|\bparements?\b|\bossatures?\b|\bdalles?\b",
                re.IGNORECASE,
            ),
        ),
        (
            "U",
            0.72,
            re.compile(r"\bbloc\w*\b", re.IGNORECASE),
        ),
    ],
    "peinture": [
        (
            "m²",
            0.78,
            # Contredit la règle générique ("beton" → m³, un volume) : en
            # peinture, "béton" désigne quasi toujours le support à peindre
            # (une surface, 83 % m², 12 projets réels), jamais le volume
            # coulé — même logique que "descente" en couverture ci-dessus.
            re.compile(r"\bbetons?\b", re.IGNORECASE),
        ),
        (
            "m²",
            0.82,
            re.compile(
                r"\bsupports?\b|\bpreparations?\b|\bparois\b|\blasures?\b|"
                r"\bbatis\b",
                re.IGNORECASE,
            ),
        ),
    ],
    "revetements_sols": [
        (
            "U",
            0.8,
            re.compile(r"\bsiphons?\b", re.IGNORECASE),
        ),
        (
            "m²",
            0.78,
            # Ne recoupe pas la règle prioritaire "isolation acoustique" →
            # Ens (qui exige le mot "isolation") : ici "acoustique" seul
            # décrit une sous-couche résiliente de sol, facturée au m².
            re.compile(r"\bacoustique\b", re.IGNORECASE),
        ),
    ],
    "espaces_verts_clotures_nettoyage": [
        (
            "U",
            0.75,
            re.compile(r"\barbres?\b", re.IGNORECASE),
        ),
        (
            "m²",
            0.75,
            re.compile(r"\bpaillages?\b", re.IGNORECASE),
        ),
    ],
}

# Séquence usuelle des corps d'état dans un DCE Moduo (démolition/VRD en tête,
# second œuvre et espaces verts en fin de classeur). Elle sert uniquement de
# départage quand plusieurs CCTP indépendants produisent des codes de lot égaux
# ou absents ; un code de lot explicite et distinct reste toujours prioritaire.
LOT_FAMILY_RULES: list[tuple[str, int, re.Pattern[str]]] = [
    # Un lot combiné "CVC-Désenfumage" (courant en réel — cf. IFO_MAR Lot 06)
    # doit rester classé "cvc" : sans cette règle en tête de liste, le simple
    # mot "desenfumage" matcherait d'abord la règle couverture/étanchéité/
    # bardage plus bas (pensée pour un lot de désenfumage seul, sans CVC), ce
    # qui privait tout le lot des règles d'unité spécifiques CVC.
    (
        "cvc",
        12,
        re.compile(
            r"(?:\bcvc\b|chauffage|ventilation|climatisation).{0,40}desenfumage|"
            r"desenfumage.{0,40}(?:\bcvc\b|chauffage|ventilation|climatisation)",
            re.IGNORECASE,
        ),
    ),
    (
        "desamiantage_demolition",
        0,
        re.compile(r"desamiant\w*|demoli\w*|deplomb\w*|depollution|curage", re.IGNORECASE),
    ),
    ("vrd", 1, re.compile(r"\bvrd\b|voirie|reseaux? divers", re.IGNORECASE)),
    (
        "fondations_gros_oeuvre",
        2,
        re.compile(
            r"gros[\s-]oeuvre|fondation|maconnerie|dallage|\bgo\b", re.IGNORECASE
        ),
    ),
    ("charpente", 3, re.compile(r"charpente", re.IGNORECASE)),
    (
        "couverture_etancheite_bardage",
        4,
        re.compile(r"couverture|etanche\w*|bardage|zinguerie|desenfumage", re.IGNORECASE),
    ),
    (
        "menuiserie_exterieure",
        5,
        re.compile(r"menuiserie\w*\s+ext\w*|facade|mur[\s-]rideau", re.IGNORECASE),
    ),
    (
        "serrurerie_metallerie",
        6,
        re.compile(
            r"serrurerie|metallerie|portes? industrielles?|portes? sectionnelles?",
            re.IGNORECASE,
        ),
    ),
    (
        "cloisons_doublages_plafonds",
        7,
        re.compile(r"cloison|doublage|faux[\s-]plafond|platrerie", re.IGNORECASE),
    ),
    (
        "menuiserie_interieure",
        8,
        re.compile(r"menuiserie\w*\s+int\w*|menuiserie\w*\s+bois", re.IGNORECASE),
    ),
    (
        "revetements_sols",
        9,
        re.compile(
            r"revetement\w*\s+de\s+sols?|carrelage|faience|chape|sol souple|"
            r"parquet|resine",
            re.IGNORECASE,
        ),
    ),
    ("peinture", 10, re.compile(r"peinture", re.IGNORECASE)),
    (
        "electricite",
        11,
        re.compile(
            r"electricit\w*|courants? (?:forts?|faibles?)|\bcfo\b|\bcfa\b|"
            r"photovoltaique",
            re.IGNORECASE,
        ),
    ),
    ("cvc", 12, re.compile(r"\bcvc\b|chauffage|ventilation|climatisation", re.IGNORECASE)),
    (
        "plomberie_sanitaire",
        13,
        re.compile(r"plomberie|sanitaire|sprinkler|\bria\b|detection gaz", re.IGNORECASE),
    ),
    (
        "ascenseur",
        14,
        re.compile(r"ascenseur|monte[\s-]charges?|plateformes? elevatrices?", re.IGNORECASE),
    ),
    (
        "espaces_verts_clotures_nettoyage",
        15,
        re.compile(r"espaces? verts?|cloture|portail|nettoyage", re.IGNORECASE),
    ),
]
DEFAULT_LOT_FAMILY_RANK = 99


def classify_lot_family(title: str) -> tuple[str, int]:
    normalized = _normalized(title)
    for family, rank, pattern in LOT_FAMILY_RULES:
        if pattern.search(normalized):
            return family, rank
    return "autre", DEFAULT_LOT_FAMILY_RANK

GENERIC_SECTION_TITLES = {
    "description",
    "prescription",
    "prescriptions",
    "generalites",
    "objet",
    "consistance des travaux",
    "description des ouvrages",
    "description des travaux",
    "descriptif des ouvrages",
    # Minage réel 2026 sur 3 CCTP/DPGF CVC (Océania, Quaero, IFO_MAR) : ces
    # sous-titres narratifs n'ont jamais d'équivalent en ligne de DPGF chez
    # l'économiste — ce sont des paragraphes d'explication, pas des ouvrages
    # à chiffrer.
    "principe",
    "conditions exterieures",
    "conditions interieures",
    "acoustique",
    "certification eurovent",
    "certification passive house",
    "niveaux sonores",
    "charges internes",
    "bilan estime",
    "limites de prestations",
    "efficacite energetique des moteurs",
}
SPECIFICATION_ONLY = re.compile(
    r"^(?:nature des prestations|localisation|apercu(?: de l['’]ouvrage)?|"
    r"mode d['’]execution|mise en œuvre|caracteristiques(?: techniques)?|"
    r"performances?|documents? de reference|normes? et reglementations?|"
    r"specifications? generales(?: de la technologie retenue)?|"
    r"interface avec le lot .+)$",
    re.IGNORECASE,
)


def _is_generic_administrative_title(title: str) -> bool:
    normalized = _normalized(title)
    return normalized in GENERIC_SECTION_TITLES or bool(
        SPECIFICATION_ONLY.match(normalized)
    )


_APOSTROPHES = re.compile(r"[‘’ʼ´`]")


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(
        character for character in value if not unicodedata.combining(character)
    )
    # Word CCTP text overwhelmingly uses the curly apostrophe (’) while Excel
    # cells and hand-typed rules tend to use the straight one ('). NFKD does
    # not fold these into each other (they are distinct characters, not
    # accent variants), so every regex keyed on "d'appui"/"d'étanchéité" and
    # every duplicate check silently missed half of real documents.
    value = _APOSTROPHES.sub("'", value)
    # "œuvre" ("GROS ŒUVRE", correct French typography) uses the œ ligature
    # (U+0153) — unlike accented letters, NFKD does NOT decompose it into
    # "oe", so every regex written with plain "oeuvre" (LOT_FAMILY_RULES,
    # UNIT_RULES...) was silently failing to match real CCTP using it.
    value = value.replace("œ", "oe").replace("Œ", "OE")
    value = value.replace("æ", "ae").replace("Æ", "AE")
    return re.sub(r"\s+", " ", value).strip().casefold()


def _clean_title(value: str) -> str:
    value = re.sub(r"\.{3,}\s*\d+\s*$", "", value)
    value = re.sub(r"\s+", " ", value).strip(" \t-–—:.;")
    return value[:240]


def _normalize_code(value: str) -> str:
    return re.sub(r"[.\-]+$", "", value.replace("-", ".").strip())


def _code_parts(code: str) -> tuple[str, ...]:
    return tuple(part for part in re.split(r"[.\-]", code) if part)


def _code_level(code: str) -> int:
    return len(_code_parts(code))


def _natural_code_key(code: str) -> tuple:
    parts: list[tuple[int, int | str]] = []
    for part in _code_parts(code):
        if part.isdigit():
            parts.append((0, int(part)))
        else:
            parts.append((1, part.casefold()))
    return tuple(parts)


def lot_sort_key(lot: dict[str, Any]) -> tuple:
    code = str(lot.get("code") or "")
    title = str(lot.get("title") or "")
    # The lot code stays the primary key so a project with its own coherent
    # numbering keeps that order untouched. The corps-d'état rank only breaks
    # ties when several independently authored CCTP produce an equal or
    # missing code, which otherwise falls back to a meaningless title sort.
    _, family_rank = classify_lot_family(title)
    return (_natural_code_key(code), family_rank, title.casefold())


def _heading_level(block: TextBlock, code: str) -> int:
    if code:
        return _code_level(code)
    style_match = re.search(r"(?:heading|titre)\s*(\d+)", block.style, re.IGNORECASE)
    if style_match:
        return max(1, min(8, int(style_match.group(1))))
    return 2


def _canonical_unit(value: str) -> str:
    normalized = _normalized(value)
    if normalized in {"m2", "metre carre", "metres carres"}:
        return "m²"
    if normalized in {"m3", "metre cube", "metres cubes"}:
        return "m³"
    if normalized in {"ml", "metre lineaire", "metres lineaires"}:
        return "ml"
    if normalized in {"u", "unite", "unites"}:
        return "U"
    if normalized in {"kg", "kilogramme", "kilogrammes"}:
        return "kg"
    if normalized in {"h", "heure", "heures"}:
        return "h"
    if normalized == "mois":
        return "mois"
    return "Ens"


def _infer_unit(
    title: str, context: str, lot_family: str | None = None
) -> tuple[str, str, float]:
    explicit = EXPLICIT_UNIT.search(f"{title}. {context[:500]}")
    if explicit:
        return _canonical_unit(explicit.group("unit")), "explicit", 0.99
    normalized_title = _normalized(title)
    for unit, confidence, pattern in FAMILY_UNIT_OVERRIDES.get(lot_family or "", []):
        if pattern.search(normalized_title):
            return unit, "rule", confidence
    for unit, confidence, pattern in UNIT_RULES:
        if pattern.search(normalized_title):
            return unit, "rule", confidence
    return "Ens", "default", 0.62


def _infer_quantity(title: str, context: str) -> tuple[float | None, str]:
    # Dimensions such as "15 mm" and technical counts are deliberately ignored.
    match = EXPLICIT_QUANTITY.search(f"{title}. {context[:500]}")
    if not match:
        return None, "missing"
    try:
        return float(match.group("value").replace(",", ".")), "explicit"
    except ValueError:
        return None, "missing"


def _stable_id(source_id: str, code: str, title: str, index: int) -> str:
    digest = hashlib.sha1(
        f"{source_id}|{code}|{title}|{index}".encode("utf-8")
    ).hexdigest()
    return f"ln_{digest[:14]}"


def _lot_identity(document: ExtractedDocument) -> tuple[str, str]:
    beginning = "\n".join(block.text for block in document.blocks[:160])
    match = LOT_PATTERN.search(beginning)
    if match:
        code = match.group("code").upper()
        if code.isdigit() and len(code) < 2:
            code = code.zfill(2)
        title = re.sub(
            r"^\s*CCTP\s+", "", _clean_title(match.group("title")), flags=re.IGNORECASE
        )
        return code, title
    stem = re.sub(r"(?i)\bCCTP\b", "", document.path.stem)
    stem = re.sub(r"[_\-]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    code_match = re.search(
        r"\b(?:LOT\s*)?([A-Z]?\d{1,3}(?:[.\-]\d+)?)\b", stem, re.IGNORECASE
    )
    code = code_match.group(1).upper() if code_match else ""
    if code.isdigit() and len(code) < 2:
        code = code.zfill(2)
    title = stem or document.path.stem
    return code, title[:100]


@dataclass
class HeadingCandidate:
    block_index: int
    code: str
    title: str
    level: int
    page: int | None
    source_kind: str
    font_size: float
    bold: bool


def _toc_pages(blocks: list[TextBlock]) -> set[int]:
    by_page: dict[int, list[str]] = defaultdict(list)
    for block in blocks:
        if block.page is not None:
            by_page[block.page].append(block.text)
    pages: set[int] = set()
    for page, texts in by_page.items():
        normalized = [_normalized(text) for text in texts]
        dotted = sum(1 for text in texts if TOC_PATTERN.search(text))
        if any(text in {"sommaire", "table des matieres"} for text in normalized):
            pages.add(page)
        elif dotted >= 4:
            pages.add(page)
    return pages


def _looks_like_heading(block: TextBlock, code: str) -> bool:
    if block.source_kind in {"paragraph", "table_row"}:
        return bool(
            re.search(r"(?:heading|titre)", block.style, re.IGNORECASE)
            or block.bold
            or code
        )
    return bool(
        block.bold
        or block.style.casefold().startswith("heading")
        or block.source_kind == "pdf_merged_heading"
        or _code_level(code) >= 2
    )


def _all_numbered_candidates(
    blocks: list[TextBlock], ignored_pages: set[int]
) -> list[HeadingCandidate]:
    candidates: list[HeadingCandidate] = []
    for index, block in enumerate(blocks):
        text = block.text.strip()
        if (
            not text
            or len(text) > 260
            or block.page in ignored_pages
            or TOC_PATTERN.search(text)
            or LOT_PATTERN.search(text)
        ):
            continue
        match = NUMBERED_HEADING.match(text)
        if not match:
            continue
        code = _normalize_code(match.group("code"))
        title = _clean_title(match.group("title"))
        if not code or len(title) < 2 or not _looks_like_heading(block, code):
            continue
        candidates.append(
            HeadingCandidate(
                block_index=index,
                code=code,
                title=title,
                level=_heading_level(block, code),
                page=block.page,
                source_kind=block.source_kind,
                font_size=block.font_size,
                bold=block.bold,
            )
        )
    return candidates


def _style_heading_level(style: str) -> int | None:
    match = re.search(r"(?:heading|titre)\s*(\d+)", style, re.IGNORECASE)
    if match:
        return max(1, min(8, int(match.group(1))))
    return None


def _needs_style_based_numbering(blocks: list[TextBlock]) -> bool:
    # Word's native multilevel-list numbering ("1.2.3 Titre") is applied by
    # the numbering engine at render time and is NOT part of a paragraph's
    # extracted text — only the resolved Heading/Titre style survives. A CCTP
    # authored this way has zero literally-numbered headings even though it
    # is fully structured, so the regex-based candidates above stay empty.
    # This is common enough in the real corpus (roughly 4 out of 5 DOCX CCTP
    # sampled) that it needs its own detection path rather than being treated
    # as an edge case.
    styled = [block for block in blocks if _style_heading_level(block.style) is not None]
    if not styled:
        return False
    literally_numbered = sum(
        1 for block in styled if NUMBERED_HEADING.match(block.text.strip())
    )
    return (literally_numbered / len(styled)) < 0.5


def _synthesize_style_candidates(
    blocks: list[TextBlock], ignored_pages: set[int]
) -> list[HeadingCandidate]:
    candidates: list[HeadingCandidate] = []
    counters = [0] * 8
    for index, block in enumerate(blocks):
        text = block.text.strip()
        if (
            not text
            or len(text) > 260
            or block.page in ignored_pages
            or TOC_PATTERN.search(text)
            or LOT_PATTERN.search(text)
        ):
            continue
        level = _style_heading_level(block.style)
        if level is None:
            continue
        title = _clean_title(text)
        if len(title) < 2:
            continue
        counters[level - 1] += 1
        for deeper in range(level, 8):
            counters[deeper] = 0
        code = ".".join(str(part) for part in counters[:level])
        candidates.append(
            HeadingCandidate(
                block_index=index,
                code=code,
                title=title,
                level=level,
                page=block.page,
                source_kind=block.source_kind,
                font_size=block.font_size,
                bold=block.bold,
            )
        )
    return candidates


def _select_work_perimeter(
    candidates: list[HeadingCandidate],
    forced_anchor_code: str | None = None,
) -> tuple[list[HeadingCandidate], dict[str, Any]]:
    if forced_anchor_code:
        normalized_code = _normalize_code(forced_anchor_code)
        forced = next(
            (c for c in candidates if c.code == normalized_code), None
        )
        if forced is not None:
            forced_prefix = _code_parts(forced.code)[0]
            selected = [
                candidate
                for candidate in candidates
                if candidate.block_index >= forced.block_index
                and _code_parts(candidate.code)
                and _code_parts(candidate.code)[0] == forced_prefix
            ]
            return selected, {
                "method": "llm_confirmed_anchor",
                "anchor_code": forced.code,
                "anchor_title": forced.title,
                "start_page": forced.page,
                "confidence": 0.9,
            }

    anchors = [
        candidate for candidate in candidates if WORK_ANCHOR.search(candidate.title)
    ]
    if anchors:
        anchor = anchors[0]
        anchor_prefixes = {
            _code_parts(candidate.code)[0]
            for candidate in anchors
            if _code_parts(candidate.code)
        }
        selected = [
            candidate
            for candidate in candidates
            if candidate.block_index >= anchor.block_index
            and _code_parts(candidate.code)
            and _code_parts(candidate.code)[0] in anchor_prefixes
        ]
        return selected, {
            "method": "explicit_anchor",
            "anchor_code": anchor.code,
            "anchor_title": anchor.title,
            "start_page": anchor.page,
            "confidence": 0.98,
        }

    prefix_counts: Counter[str] = Counter()
    first_by_prefix: dict[str, HeadingCandidate] = {}
    for candidate in candidates:
        parts = _code_parts(candidate.code)
        if len(parts) < 2:
            continue
        prefix_counts[parts[0]] += 1
        first_by_prefix.setdefault(parts[0], candidate)
    if prefix_counts:
        prefix, count = max(
            prefix_counts.items(),
            key=lambda item: (
                item[1],
                _natural_code_key(item[0]),
            ),
        )
        selected = [
            candidate
            for candidate in candidates
            if _code_parts(candidate.code)
            and _code_parts(candidate.code)[0] == prefix
        ]
        first = first_by_prefix[prefix]
        confidence = 0.92 if count >= 6 else 0.78
        return selected, {
            "method": "dominant_numbered_chapter",
            "anchor_code": prefix,
            "anchor_title": first.title,
            "start_page": first.page,
            "confidence": confidence,
        }

    return [], {
        "method": "not_found",
        "anchor_code": "",
        "anchor_title": "",
        "start_page": None,
        "confidence": 0.0,
    }


def _deduplicate_candidates(
    candidates: list[HeadingCandidate],
) -> tuple[list[HeadingCandidate], set[str]]:
    result: list[HeadingCandidate] = []
    exact_seen: set[tuple[str, str]] = set()
    titles_by_code: dict[str, set[str]] = defaultdict(set)
    conflicting_codes: set[str] = set()
    for candidate in candidates:
        if (
            candidate.level == 1
            and candidate.code in titles_by_code
            and not WORK_ANCHOR.search(candidate.title)
        ):
            # Prevent sentences such as "3 voies revient en position..." from
            # becoming a second chapter 3 inside a technical description.
            continue
        key = (candidate.code.casefold(), _normalized(candidate.title))
        if key in exact_seen:
            continue
        exact_seen.add(key)
        title_key = _normalized(candidate.title)
        titles_by_code[candidate.code].add(title_key)
        if len(titles_by_code[candidate.code]) > 1:
            conflicting_codes.add(candidate.code)
        result.append(candidate)
    return result, conflicting_codes


_CONTROL_CHILD_PATTERN = re.compile(r"^controle\s+qualit", re.IGNORECASE)


def _apply_decomposition_rules(
    lines: list[dict[str, Any]], source_id: str, lot_family: str | None = None
) -> list[dict[str, Any]]:
    # Grouping "Contrôle qualité" lines at the end of their section (instead
    # of wherever the triggering CCTP text sat) matches how VRD DPGF are
    # conventionally laid out. Other trades may order control/test lines
    # differently, so this stays VRD-only until a similar convention is
    # confirmed for another lot family.
    defer_controls = lot_family == "vrd"
    existing_normalized = {
        _normalized(str(line.get("designation") or "")) for line in lines
    }
    existing_normalized.discard("")
    triggered_rules: set[int] = set()
    expanded: list[dict[str, Any]] = []
    deferred_controls: list[dict[str, Any]] = []
    added_count = 0

    def flush_deferred_controls() -> None:
        expanded.extend(deferred_controls)
        deferred_controls.clear()

    def is_duplicate(child_key: str) -> bool:
        # A child's designation always shares the parent's trigger phrase
        # (e.g. "Tube cuivre" is a substring of every "Tube cuivre diam..."
        # child), so a plain substring test would flag every child as an
        # existing duplicate. Only count it as a real duplicate when the
        # matched existing designation covers most of the child text — i.e.
        # the CCTP already wrote out that specific variant, not just the
        # generic parent concept.
        for existing in existing_normalized:
            if not existing:
                continue
            shorter, longer = (
                (existing, child_key)
                if len(existing) <= len(child_key)
                else (child_key, existing)
            )
            if len(shorter) >= 6 and shorter in longer and len(shorter) >= 0.7 * len(longer):
                return True
        return False

    for line in lines:
        # A new chapter/sub-chapter ("x" or "x.x") closes out whatever
        # "Contrôle qualité" lines were queued while working through the
        # previous section, so they land grouped at the end of their own
        # section (matching real DPGF layout) instead of scattered wherever
        # the triggering CCTP text happened to sit inside that section.
        if defer_controls and line.get("kind") == "section" and int(line.get("level") or 0) <= 2:
            flush_deferred_controls()
        expanded.append(line)
        if line.get("kind") != "item":
            continue
        designation = str(line.get("designation") or "")
        normalized_designation = _normalized(designation)
        for rule_index, (pattern, children) in enumerate(DECOMPOSITION_RULES):
            if rule_index in triggered_rules or not pattern.search(normalized_designation):
                continue
            triggered_rules.add(rule_index)
            for child_designation, child_unit in children:
                child_key = _normalized(child_designation)
                if is_duplicate(child_key):
                    continue
                added_count += 1
                child_line = {
                    "id": _stable_id(
                        source_id, "", child_designation, 900000 + added_count
                    ),
                    "kind": "item",
                    "level": int(line.get("level") or 1) + 1,
                    "code": "",
                    "designation": child_designation,
                    "description": "",
                    "unit": child_unit,
                    "unit_source": "rule",
                    "unit_confidence": 0.75,
                    "quantity": None,
                    "quantity_source": "missing",
                    "unit_price": None,
                    "included": True,
                    "confidence": 0.6,
                    "review_status": "to_review",
                    "review_reason": (
                        f"Ajouté selon la règle métier : {designation.strip()} "
                        f"implique {child_designation}"
                    ),
                    "review_fields": [],
                    "source_id": source_id,
                    "source_page": None,
                    "source_excerpt": "",
                    "origin": "rule-derived",
                }
                existing_normalized.add(child_key)
                if defer_controls and _CONTROL_CHILD_PATTERN.search(child_key):
                    deferred_controls.append(child_line)
                else:
                    expanded.append(child_line)
    flush_deferred_controls()
    return expanded


def _build_candidates(document: ExtractedDocument) -> list[HeadingCandidate]:
    ignored_pages = _toc_pages(document.blocks)
    all_candidates = _all_numbered_candidates(document.blocks, ignored_pages)
    if _needs_style_based_numbering(document.blocks):
        style_candidates = _synthesize_style_candidates(document.blocks, ignored_pages)
        if style_candidates:
            all_candidates = style_candidates
    return all_candidates


def list_heading_candidates(document: ExtractedDocument) -> list[dict[str, Any]]:
    """Lightweight candidate headings for an external caller (e.g. an LLM
    assist) to pick a perimeter anchor from, without running the full
    deterministic line-building pass. Kept free of any LLM dependency so
    parser.py stays purely deterministic."""
    candidates = _build_candidates(document)
    return [
        {"code": c.code, "title": c.title, "level": c.level}
        for c in candidates
    ]


def parse_document(
    document: ExtractedDocument,
    source_id: str,
    forced_anchor_code: str | None = None,
) -> dict[str, Any]:
    lot_code, lot_title = _lot_identity(document)
    lot_family, _ = classify_lot_family(lot_title)
    ignored_pages = _toc_pages(document.blocks)
    all_candidates = _build_candidates(document)
    candidates, perimeter = _select_work_perimeter(all_candidates, forced_anchor_code)
    candidates, conflicting_codes = _deduplicate_candidates(candidates)
    lines: list[dict[str, Any]] = []
    warnings = list(document.warnings)

    if not candidates:
        warnings.append(
            "Le chapitre des ouvrages n'a pas été identifié avec assez de certitude. "
            "Aucune ligne hasardeuse n'a été créée."
        )
    elif perimeter["method"] == "dominant_numbered_chapter":
        warnings.append(
            f"Périmètre déduit du chapitre dominant {perimeter['anchor_code']} "
            "(aucun titre « Description des ouvrages » explicite)."
        )

    anchor_code = str(perimeter.get("anchor_code") or "")
    for index, candidate in enumerate(candidates):
        next_index = (
            candidates[index + 1].block_index
            if index + 1 < len(candidates)
            else len(document.blocks)
        )
        context_blocks = document.blocks[
            candidate.block_index + 1 : min(next_index, candidate.block_index + 14)
        ]
        context = re.sub(
            r"\s+",
            " ",
            " ".join(block.text for block in context_blocks),
        ).strip()
        has_child = (
            index + 1 < len(candidates)
            and candidates[index + 1].level > candidate.level
            and _code_parts(candidates[index + 1].code)[: candidate.level]
            == _code_parts(candidate.code)
        )
        is_anchor = candidate.code == anchor_code and (
            (perimeter["method"] == "explicit_anchor" and WORK_ANCHOR.search(candidate.title))
            or perimeter["method"] == "llm_confirmed_anchor"
        )
        # Only "x" and "x.x" codes read as real chapter/sub-chapter titles in
        # a DPGF (e.g. "3" SPECIFICATIONS TECHNIQUES GENERALES, "3.1" TRAVAUX
        # GENERAUX) — and only when they actually own smaller numerals ("x.x"
        # can just as well be a genuine leaf item in a shallower CCTP). Codes
        # three levels deep or more are always priceable rows, even when a
        # CCTP heading at that depth structurally owns smaller numerals (e.g.
        # a "3.4.2 Fourniture et pose de canalisation :" item introducing its
        # own itemised sub-parts) — treating those as titles was producing
        # spurious bold headers instead of a plain, indented item.
        is_section = (
            is_anchor
            or candidate.level <= 1
            or (has_child and candidate.level == 2)
        )
        kind = "section" if is_section else "item"
        if kind == "item" and not is_anchor and _is_generic_administrative_title(
            candidate.title
        ):
            # Narrative CCTP sub-headings ("Généralités", "Principe",
            # "Acoustique", "Certification EUROVENT"...) never become their
            # own DPGF line in practice — only their real, priceable
            # children (if any) do, and those are still emitted normally on
            # their own iteration of this loop.
            continue
        unit, unit_source, unit_confidence = _infer_unit(
            candidate.title, context, lot_family
        )
        quantity, quantity_source = _infer_quantity(candidate.title, context)

        classification_confidence = float(perimeter["confidence"])
        if candidate.source_kind == "pdf_merged_heading":
            classification_confidence = min(0.99, classification_confidence + 0.01)
        if not candidate.bold and not candidate.source_kind == "paragraph":
            classification_confidence -= 0.03
        if is_section:
            confidence = max(0.9, classification_confidence)
        else:
            confidence = (
                classification_confidence * 0.78 + unit_confidence * 0.22
            )

        review_reasons: list[str] = []
        if kind == "item" and candidate.code in conflicting_codes:
            review_reasons.append(f"Code {candidate.code} présent avec plusieurs intitulés")
            confidence -= 0.12
        if kind == "item" and float(perimeter["confidence"]) < 0.85:
            review_reasons.append("Périmètre des ouvrages à confirmer")
            confidence -= 0.08
        confidence = round(max(0.35, min(0.99, confidence)), 2)
        included = not bool(OPTION_PATTERN.search(candidate.title))
        lines.append(
            {
                "id": _stable_id(source_id, candidate.code, candidate.title, index),
                "kind": kind,
                "level": candidate.level,
                "code": candidate.code,
                "designation": candidate.title,
                "description": context[:1200],
                "unit": None if kind == "section" else unit,
                "unit_source": None if kind == "section" else unit_source,
                "unit_confidence": None if kind == "section" else unit_confidence,
                "quantity": None if kind == "section" else quantity,
                "quantity_source": None if kind == "section" else quantity_source,
                "unit_price": None,
                "included": included,
                "confidence": confidence,
                "review_status": "to_review" if review_reasons else "validated",
                "review_reason": " · ".join(review_reasons),
                "review_fields": (
                    ["code"] if candidate.code in conflicting_codes else []
                ),
                "source_id": source_id,
                "source_page": candidate.page,
                "source_excerpt": f"{candidate.code} {candidate.title}. {context[:500]}".strip(),
                "origin": "deterministic-v2",
            }
        )

    lines = _apply_decomposition_rules(lines, source_id, lot_family)

    item_count = sum(1 for line in lines if line["kind"] == "item")
    if item_count == 0:
        warnings.append("Aucun poste chiffrable n'a été identifié automatiquement.")
    return {
        "id": f"lot_{hashlib.sha1(source_id.encode('utf-8')).hexdigest()[:12]}",
        "code": lot_code,
        "title": lot_title,
        "source_id": source_id,
        "lines": lines,
        "warnings": warnings,
        "perimeter": {
            **perimeter,
            "ignored_toc_pages": sorted(ignored_pages),
            "candidate_count_before_perimeter": len(all_candidates),
            "selected_heading_count": len(candidates),
        },
    }


def recompute_stats(lots: list[dict[str, Any]]) -> dict[str, Any]:
    all_lines = [line for lot in lots for line in lot.get("lines", [])]
    items = [line for line in all_lines if line.get("kind") == "item"]
    review = [line for line in items if line.get("review_status") != "validated"]
    trusted_units = [
        line
        for line in items
        if line.get("unit_source") in {"explicit", "rule"}
        or line.get("origin") == "manual"
    ]
    explicit_quantities = [
        line
        for line in items
        if line.get("quantity_source") == "explicit"
        or (line.get("origin") == "manual" and line.get("quantity") is not None)
    ]
    classification = (
        sum(float(line.get("confidence") or 0) for line in items) / len(items)
        if items
        else 0.0
    )
    accepted_ratio = (len(items) - len(review)) / len(items) if items else 0.0
    unit_quality = (
        sum(float(line.get("unit_confidence") or 1.0) for line in items) / len(items)
        if items
        else 0.0
    )
    extraction_index = (
        classification * 0.58 + accepted_ratio * 0.27 + unit_quality * 0.15
        if items
        else 0.0
    )
    return {
        "lots": len(lots),
        "sections": sum(1 for line in all_lines if line.get("kind") == "section"),
        "items": len(items),
        "to_review": len(review),
        "validated": len(items) - len(review),
        "unit_coverage": round(len(trusted_units) / len(items) * 100) if items else 0,
        "quantity_coverage": (
            round(len(explicit_quantities) / len(items) * 100) if items else 0
        ),
        "classification_confidence": round(classification, 2),
        "accepted_ratio": round(accepted_ratio, 2),
        "average_confidence": round(min(0.99, extraction_index), 2),
    }
