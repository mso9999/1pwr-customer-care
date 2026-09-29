"""Build the formatted Benin subscription contract for Mionwa Generation.

Source text: Contrat d'abonnement type, janvier 2025. The customer particulars
stay blank. The contracting party is Mionwa Generation S.A. The 1PWR logo is
the header mark.
"""

import os
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

HERE = os.path.dirname(os.path.abspath(__file__))
LOGO = os.path.join(HERE, "..", "media", "1pwr-logo.png")
NAVY = RGBColor(0x1B, 0x3A, 0x6B)
RULE = "1B3A6B"
BLANK = "…………………………………………"

OPERATOR = (
    "MIONWA GENERATION S.A., immatriculée au registre du commerce et du crédit "
    "mobilier sous le numéro RCCM RB/COT/20 B 27888, IFU 3202011866450, ayant son "
    "siège au 01 BP 1112 Cotonou (Ilot 1053, quartier Gbedjromedé), ci-après dénommée "
    "le prestataire."
)


def _shade(cell, fill):
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def _set_run(run, size=11, bold=False, color=None, italic=False):
    run.font.name = "Calibri"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic
    if color is not None:
        run.font.color.rgb = color


def add_p(doc, text, *, size=11, bold=False, center=False, space_before=0, space_after=6, color=None, italic=False):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(space_before)
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = 1.08
    if center:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(text)
    _set_run(run, size=size, bold=bold, color=color, italic=italic)
    return p


def add_heading_bar(doc, text):
    table = doc.add_table(rows=1, cols=1)
    table.autofit = True
    cell = table.cell(0, 0)
    _shade(cell, RULE)
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run(text)
    _set_run(run, size=12, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def add_field_table(doc, rows):
    table = doc.add_table(rows=len(rows), cols=2)
    table.style = "Table Grid"
    table.autofit = True
    for i, (label, value) in enumerate(rows):
        left, right = table.rows[i].cells
        _shade(left, "F4F7FB")
        left.text = ""
        right.text = ""
        lp = left.paragraphs[0]
        rp = right.paragraphs[0]
        lr = lp.add_run(label)
        rr = rp.add_run(value)
        _set_run(lr, size=10, bold=True, color=NAVY)
        _set_run(rr, size=10)
        left.width = Cm(7)
        right.width = Cm(10)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)


def add_article(doc, title, paragraphs):
    add_p(doc, title, size=12, bold=True, color=NAVY, space_before=10, space_after=3)
    for text in paragraphs:
        add_p(doc, text, size=10.5, space_after=4)


def build(path):
    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(1.5)
    section.bottom_margin = Cm(1.6)
    section.left_margin = Cm(1.7)
    section.right_margin = Cm(1.7)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fr = footer.add_run("MIONWA GENERATION  ·  Contrat d’abonnement type  ·  Janvier 2025  ·  1PWR")
    _set_run(fr, size=8, color=RGBColor(0x6B, 0x72, 0x80))

    if os.path.isfile(LOGO):
        logo_p = doc.add_paragraph()
        logo_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        logo_p.paragraph_format.space_after = Pt(2)
        logo_p.add_run().add_picture(LOGO, width=Cm(4.6))

    add_p(doc, "MIONWA GENERATION", size=11, bold=True, center=True, color=NAVY, space_after=0)
    add_p(doc, "Mini-réseaux d’électrification hors réseau  ·  Bénin", size=9, center=True, color=RGBColor(0x4B, 0x55, 0x63), space_after=8)
    add_p(doc, "CONTRAT D’ABONNEMENT TYPE", size=16, bold=True, center=True, color=NAVY, space_after=2)
    add_p(
        doc,
        "POUR LA FOURNITURE D’ÉLECTRICITÉ À PARTIR DES MINI-RÉSEAUX\nD’ÉLECTRIFICATION HORS RÉSEAU AU BÉNIN",
        size=11, bold=True, center=True, space_after=2,
    )
    add_p(doc, "Janvier 2025", size=10, italic=True, center=True, space_after=10)

    add_heading_bar(doc, "I  —  CONDITIONS PARTICULIÈRES")
    add_p(doc, f"Contrat n°  {BLANK}", size=11, bold=True, space_after=6)

    add_p(doc, "Opérateur", size=11, bold=True, color=NAVY, space_before=4, space_after=2)
    add_p(doc, OPERATOR, size=10.5, space_after=8)

    add_p(doc, "Client", size=11, bold=True, color=NAVY, space_after=2)
    add_field_table(doc, [
        ("Prénoms", BLANK),
        ("Nom", BLANK),
        ("Raison sociale", BLANK),
        ("Représentant légal", BLANK),
        ("Téléphone", BLANK),
        ("Localité", BLANK),
        ("Commune", BLANK),
        ("Catégorie de client", BLANK),
        ("Pièce justifiant l’occupation légale", BLANK),
        ("Numéro et nature du document d’identité", "☐ CIN    ☐ Carte CNSS    ☐ Carte RAVIP\n☐ Passeport    ☐ Permis de conduire    ☐ CIP    ☐ IFU"),
        ("N° du document", BLANK),
        ("Type de branchement / compteur", "☐ Monophasé (bridage)    ☐ Monophasé (sans bridage)    ☐ Triphasé"),
        ("Frais de raccordement payés (FCFA)", BLANK),
        ("Date de paiement", BLANK),
    ])

    add_heading_bar(doc, "II  —  ACCEPTATION")
    add_p(
        doc,
        "Par la signature de " + OPERATOR + " et "
        f"{BLANK}, demeurant à {BLANK}, Commune de {BLANK}, le client s’engage à respecter "
        "les termes et conditions indiqués ci-après dans le cadre du service de fourniture "
        "d’électricité proposé par Mionwa Generation.",
        size=10.5,
    )
    add_p(
        doc,
        "(*) Si le client est analphabète ou ne comprend pas le français, faire attester sa signature "
        "par un témoin autre qu’un personnel de l’opérateur.",
        size=9, italic=True, space_before=4,
    )

    sig = doc.add_table(rows=2, cols=3)
    sig.autofit = True
    labels = ("Pour l’opérateur", "Le client", "Le témoin (*)")
    for i, label in enumerate(labels):
        cell = sig.rows[0].cells[i]
        cell.text = ""
        r = cell.paragraphs[0].add_run(label)
        _set_run(r, size=10, bold=True, color=NAVY)
        line = sig.rows[1].cells[i]
        line.text = ""
        p = line.paragraphs[0]
        p.paragraph_format.space_before = Pt(28)
        rr = p.add_run("Signature\nDate : ______________")
        _set_run(rr, size=9, color=RGBColor(0x4B, 0x55, 0x63))
    add_p(
        doc,
        "En présence de Mme/M. " + BLANK + ". Je confirme que le contenu de ce contrat a été relu et "
        "expliqué au client dans sa langue, avant l’apposition de sa signature, et que ce dernier a compris "
        "et approuvé les termes et conditions prévus au présent contrat.",
        size=10, space_before=8,
    )

    add_heading_bar(doc, "III  —  CONDITIONS GÉNÉRALES")
    articles = [
        ("Article 1 — Objet et dispositions générales", [
            "Le contrat d’abonnement définit les modalités de vente de l’électricité par " + OPERATOR + " "
            "Les présentes conditions générales de vente de l’électricité sont établies conformément aux textes "
            "législatifs et réglementaires en vigueur, aux dispositions du titre d’exploitation entre le concédant "
            "et l’opérateur, au cahier des charges, et au règlement de service d’exploitation de système "
            "d’électrification hors réseau au Bénin.",
        ]),
        ("Article 2 — Raccordement et branchements", [
            "Mionwa Generation est tenu de raccorder au mini-réseau de distribution électrique réalisé toute personne "
            "physique ou morale qui en fait la demande, pour autant que ce branchement soit situé à l’intérieur "
            "du périmètre de sa concession et à condition que le point de livraison du demandeur soit situé à "
            "moins de 30 m du réseau existant, dès qu’il a au préalable souscrit à un abonnement et qu’il a payé "
            "les frais de branchement ou un apport initial, le cas échéant. Au-delà de cette distance, Mionwa Generation "
            "est tenu d’évaluer l’option la plus adéquate pour apporter le service électrique au client, moyennant "
            "des frais supplémentaires.",
            "Le branchement réalisé dans un délai de 30 jours qui suit le paiement effectif des frais de "
            "branchement à la demande du client est fait dans les conditions définies par le règlement de service "
            "d’exploitation de système d’électrification hors réseau au Bénin. Il fait partie intégrante du "
            "mini-réseau public de distribution de l’énergie électrique de l’opérateur et est entretenu et "
            "renouvelé par Mionwa Generation. Le client est tenu de veiller à ne pas altérer le bon fonctionnement des "
            "équipements constitutifs du branchement et de faciliter l’accès de ces équipements aux agents "
            "de Mionwa Generation pour les besoins de pose du compteur, de contrôle, d’entretien, de renouvellement et, "
            "le cas échéant, de dépose.",
            "La première mise sous tension des installations intérieures du client est conditionnée à un contrôle "
            "de conformité préalable aux conditions minimales de sécurité de l’installation électrique intérieure "
            "du client par Mionwa Generation ou par un organisme habilité proposé par Mionwa Generation, et sanctionné par un "
            "procès-verbal de visite qui indique entre autres les types d’installations électriques intérieures "
            "retenus par le client. Mionwa Generation devra également expliquer au client les exigences minimales de "
            "sécurité applicables aux installations électriques intérieures. Le client doit signaler à Mionwa Generation "
            "dans les plus brefs délais toute situation anormale constatée.",
        ]),
        ("Article 3 — Installations électriques intérieures", [
            "Les installations électriques intérieures doivent être réalisées par un électricien habilité, dans "
            "le respect des normes techniques de sécurité électrique. Le client peut confier la réalisation de "
            "son installation électrique intérieure à un tiers s’il le souhaite. Dans le cas où le client choisit "
            "de faire son installation intérieure par Mionwa Generation, la livraison de l’installation intérieure et des "
            "équipements électriques installés au client par l’opérateur fait l’objet d’un procès-verbal de "
            "réception signé contradictoirement, qui transfère au client la propriété des équipements. "
            "L’installation électrique intérieure est utilisée et entretenue par le client, conformément aux "
            "normes et règlements techniques en vigueur, et placée sous son entière responsabilité. Dans le cas "
            "où l’installation électrique intérieure est réalisée par l’opérateur, les modalités de financement "
            "et de paiement de l’installation seront définies d’un commun accord entre le client et l’opérateur "
            "et feront l’objet d’un contrat séparé.",
            "Mionwa Generation ne pourra en aucun cas être tenu pour responsable de tout dommage matériel, corporel ou "
            "de toute autre nature résultant d’un mauvais entretien, d’une mauvaise utilisation ou d’un "
            "dysfonctionnement d’une installation électrique intérieure, sans préjudice des responsabilités "
            "spécifiques à la réalisation des installations.",
            "L’installation et l’entretien des installations électriques intérieures sont réalisés de manière à "
            "éviter tout problème de fonctionnement du réseau de distribution, à ne pas compromettre la sécurité "
            "des personnes qui interviennent sur ces installations dans le cadre du service, et à empêcher "
            "l’usage illicite et frauduleux de l’énergie électrique.",
            "Tout appareil ou partie de l’installation qui constituerait un danger ou une gêne pour le "
            "fonctionnement normal du réseau de distribution, notamment par défaut de protection efficace, doit "
            "être immédiatement isolé ou remplacé par le client, sous peine de suspension de la fourniture par "
            "Mionwa Generation.",
            "Tout client désirant utiliser un moyen quelconque de production autonome d’électricité doit équiper "
            "son installation de production d’électricité d’appareils de commutation et de protection appropriés, "
            "de sorte à ne jamais réinjecter de l’énergie sur le réseau.",
            "Mionwa Generation peut, à tout moment, isoler les installations du client après l’avoir informé en cas de "
            "défaillance grave de ces dernières, produisant un déclenchement des protections du mini-réseau. "
            "Mionwa Generation peut par la suite, sans formalité ni préavis, interrompre la fourniture de l’énergie "
            "électrique s’il est reconnu que les installations électriques intérieures sont défectueuses ou non "
            "conformes aux normes et aux règlements en vigueur.",
        ]),
        ("Article 4 — Fourniture et interruption", [
            "La livraison se fait en principe en monophasé ou en triphasé, à la fréquence 50 Hz et sous la "
            "tension nominale 230 volts entre phase et neutre et de 400 volts entre phases. Les tolérances "
            "admises par rapport aux valeurs nominales de la fréquence et de la tension sont respectivement "
            "de (±) 4 % et (±) 10 %.",
            "Mionwa Generation fournit l’électricité en tout temps, sous réserve des interruptions pouvant résulter "
            "d’une situation d’urgence, d’un accident, d’un bris d’équipement ou du déclenchement de "
            "l’appareillage de protection du réseau, et de tout cas de force majeure.",
            "Mionwa Generation peut interrompre, en tout temps, la fourniture d’électricité aux fins d’entretien, de "
            "réparation, de modification ou de gestion du réseau, ou pour des fins de sécurité ou d’utilité "
            "publique.",
            "En cas d’interruption programmée justifiée par des travaux sur le mini-réseau, l’opérateur est tenu "
            "d’en informer le client par la voie la plus adaptée, au minimum 48 heures préalablement à la "
            "réalisation desdits travaux.",
            "En cas d’interruption d’énergie liée à des incidents ou événements extérieurs (déclenchements de "
            "ligne, perturbations atmosphériques, accidents, effondrements de réseau, ou tout autre événement "
            "fortuit en dehors du contrôle de l’opérateur), l’opérateur est tenu d’informer le client qui en fait "
            "la demande, sur l’origine de cette interruption, dans un délai de 72 heures à compter de la "
            "réception de ladite demande.",
        ]),
        ("Article 5 — Compteurs", [
            "L’énergie vendue est mesurée par un compteur. Le type de compteur est fixé par Mionwa Generation en "
            "fonction des caractéristiques des installations à alimenter. Les compteurs sont fournis, étalonnés, "
            "posés et entretenus par l’opérateur. Ils sont posés à l’intérieur ou en limite extérieure de "
            "propriété ou en haut de poteau, et accessibles à tout moment aux agents de l’opérateur dûment "
            "mandatés. Si le compteur est posé en haut de poteau, Mionwa Generation doit mettre à la disposition du "
            "client une interface déportée ou toute autre solution lui permettant de disposer d’informations "
            "concernant sa consommation en kilowattheures (kWh) ou en valeur monétaire (FCFA).",
            "Dans le cas où le compteur n’est pas installé sur support de ligne, le client est tenu d’aménager "
            "un emplacement nécessaire pour l’installation, accessible à tout moment pour permettre d’effectuer "
            "facilement les lectures et de procéder aisément aux opérations de vérification et d’entretien.",
            "Il est expressément interdit au client de déplacer les compteurs ou d’apporter une modification aux "
            "compteurs (bris de plomb, etc.), au calibre du disjoncteur, aux colonnes montantes, au câblage des "
            "tableaux, aux appareils installés sur le tableau de comptage et à leurs accessoires de protection.",
            "Tout client peut demander la vérification de son compteur par les agents de Mionwa Generation. À cet effet, "
            "un rendez-vous est pris et une inspection sur place est proposée dans un délai de dix (10) jours à "
            "compter de la réception de la réclamation du client. En cas d’anomalie ou de défectuosité de "
            "l’appareil de comptage ou de contrôle, il est procédé à son remplacement ainsi qu’au redressement "
            "de la facturation en conséquence.",
        ]),
        ("Article 6 — Facturation, tarifs et paiement", [
            "L’offre de tarif applicable au présent contrat figure en annexe 2 « Grille tarifaire et coût de "
            "branchement ». Ces dispositions tarifaires applicables sont celles approuvées par la Direction "
            "générale de la promotion de l’économie rurale (DGPER). La révision tarifaire obligatoire se fait "
            "sur la base d’une périodicité de vingt-quatre (24) mois.",
            "Le mode de paiement est le prépaiement. Le client est facturé au kWh et règle sur ses recharges "
            "d’électricité, à l’exception du remboursement de ses installations électriques intérieures, les "
            "éléments ci-après : la quantité d’énergie consommée facturée au prix du kWh de la catégorie de "
            "client ; la redevance fixe mensuelle (prime fixe), qui sera défalquée de la recharge d’unités en "
            "premier chef et à chaque recharge du compteur ; les droits et taxes imposés par la législation en "
            "vigueur.",
            "Un bordereau de prix des petites interventions de l’opérateur est adopté par la DGPER et mis à jour "
            "périodiquement. Il fixe les prix plafonds à facturer par l’opérateur au client pour certaines "
            "prestations spécifiques formulées par le client, comme les demandes de changement de catégorie "
            "d’abonnement ou d’augmentation de puissance, de déplacement de compteur, etc.",
        ]),
        ("Article 7 — Suspension de la fourniture", [
            "Mionwa Generation peut suspendre la fourniture d’électricité au client dans les cas suivants :",
            "• la sécurité publique l’exige ou l’autorité ayant compétence en la matière le demande ;",
            "• le client n’apporte pas les modifications ou les ajustements nécessaires pour que son installation "
            "électrique soit conforme aux normes en vigueur et ne soit plus cause de perturbation du mini-réseau ;",
            "• le client n’utilise pas l’électricité conformément aux clauses du présent contrat et/ou refuse de "
            "respecter les termes de ce contrat ;",
            "• le client refuse l’accès à son installation électrique intérieure par les agents de l’opérateur "
            "pour inspecter une panne, en violation des dispositions du présent contrat ;",
            "• le client procède à une revente du courant qui lui est fourni ;",
            "• le client est convaincu de vol d’électricité.",
            "La durée de la suspension est précisée au client mais ne peut dépasser deux (02) ans.",
        ]),
        ("Article 8 — Résiliation", [
            "Le client peut à tout moment résilier le présent contrat d’abonnement en se présentant auprès de "
            "l’opérateur. À la demande de résiliation, l’opérateur procède à la suspension de la fourniture "
            "d’énergie, à la vérification d’absence de fraude et à la dépose éventuelle du compteur.",
            "En cas de décès du client, ses héritiers ou ses ayants droit doivent procéder à la résiliation "
            "dudit contrat en bonne et due forme, sous peine d’être déchus de toute action en rétablissement "
            "en cas de suspension d’énergie. Au cas où un nouvel utilisateur souhaite reprendre le compteur du "
            "client décédé dans un délai de moins de 3 mois, l’opérateur engage avec les héritiers ou ayants "
            "droit les formalités de résiliation du contrat initial et de conclusion d’un contrat d’abonnement "
            "avec le nouveau client, sans frais de branchement.",
            "L’opérateur se réserve le droit de résilier le contrat d’abonnement : au terme de la période de "
            "suspension ; en cas d’usage illicite ou frauduleux de l’électricité ou de constatation d’un vol "
            "d’électricité chez le client ; en cas de récidive d’une revente d’électricité par le client, "
            "dûment constatée par ses services compétents.",
            "Lorsque le client n’est pas le consommateur de l’électricité, Mionwa Generation engage avec le nouveau "
            "consommateur les formalités de résiliation du contrat initial et de conclusion d’un contrat "
            "d’abonnement avec le nouveau client, sans frais de branchement pour ce dernier. Le contrat "
            "d’abonnement peut être résilié d’office en cas de manquement à une ou plusieurs dispositions "
            "contractuelles.",
        ]),
        ("Article 9 — Réabonnement", [
            "Tout ancien client dont le contrat a été résilié doit payer au titre de son réabonnement les frais "
            "de branchement correspondant à la catégorie à laquelle il souscrit.",
        ]),
        ("Article 10 — Migration entre services", [
            "Le changement de niveau de service, dans le respect des dispositions du présent contrat "
            "d’abonnement, doit faire l’objet d’un avenant en relation avec le niveau de service choisi. Pour "
            "le passage à un niveau de service supérieur, le client doit verser à Mionwa Generation la différence entre "
            "les frais de branchement des deux niveaux de services. Tout changement du niveau de service à la "
            "demande du client est conditionné par le règlement des frais y afférents.",
        ]),
        ("Article 11 — Respect de la puissance souscrite", [
            "Le client est tenu de maintenir son appel de puissance à tout moment dans la limite de son niveau "
            "de service ou de sa puissance souscrite, conformément aux dispositions de son contrat d’abonnement.",
        ]),
        ("Article 12 — Obligations du client", [
            "Le client doit respecter les droits de l’opérateur en tant que distributeur exclusif de "
            "l’électricité dans son périmètre de concession. Il est formellement interdit au client de "
            "distribuer l’énergie électrique hors du point de livraison établi par Mionwa Generation. Il ne doit pas "
            "non plus effectuer aucune opération sur le branchement (câble de raccordement, compteur et "
            "disjoncteur). Il ne peut pas acquérir en remplacement, ni déplacer ou apporter une modification à "
            "ces matériels et équipements nécessaires au raccordement au mini-réseau de Mionwa Generation. Le client est "
            "tenu de veiller à ne pas altérer le bon fonctionnement des équipements constitutifs des "
            "branchements et de faciliter l’accès de ces installations aux agents de Mionwa Generation pour les besoins "
            "de contrôle, d’entretien, de renouvellement et, le cas échéant, de dépose.",
        ]),
        ("Article 13 — Fraudes", [
            "Le client doit éviter tout genre de fraudes (vol d’énergie électrique), sous peine de poursuites "
            "judiciaires devant la juridiction pénale territorialement compétente.",
        ]),
        ("Article 14 — Réclamations", [
            "Le client peut faire une réclamation à Mionwa Generation. Ce dernier doit, après réception de la "
            "réclamation, expliquer au client le problème et les mesures prises ou à prendre pour le résoudre, "
            "dans un délai maximal de 10 jours ouvrables à compter de la date de réception de la réclamation.",
            "Dans son retour, Mionwa Generation doit indiquer un délai raisonnable de résolution du problème, lorsque "
            "celui-ci est avéré. Si ce délai n’est pas tenu ou si le client n’obtient pas un retour de Mionwa Generation "
            "dans un délai de 60 jours suivant sa réclamation, il peut saisir la DGPER.",
            "Mionwa Generation se réserve le droit de réclamer tous dommages et intérêts au client en réparation de "
            "tout préjudice subi par l’opérateur résultant des manquements du client, après avoir informé la DGPER.",
        ]),
        ("Article 15 — Durée", [
            "Le présent contrat est conclu pour une durée indéterminée et entre en vigueur à la date du raccordement.",
        ]),
        ("Article 16 — Règlement des différends", [
            "Avant toute saisie judiciaire, les parties s’engagent à privilégier un règlement à l’amiable du "
            "différend, par l’intermédiaire de la DGPER, lorsque cela est applicable. À défaut de règlement à "
            "l’amiable dans un délai de 30 jours suivant la notification d’une partie à l’autre, tout litige "
            "relatif à l’exécution, à l’interprétation ou à la résiliation du présent contrat relève de la "
            "compétence exclusive des juridictions du lieu du branchement du client, statuant en matière commerciale.",
            "Toute infraction aux dispositions légales et réglementaires relatives à l’usage de l’électricité, "
            "notamment en cas de fraude (vol d’énergie électrique), sera poursuivie devant les juridictions "
            "pénales territorialement compétentes.",
        ]),
        ("Article 17 — Stipulations diverses", [
            "Le client ne peut pas employer le courant électrique à un usage différent de celui qui est indiqué "
            "dans les conditions particulières de son contrat d’abonnement. L’abonnement étant personnel, toute "
            "cession de logement, magasin ou établissement donne obligatoirement lieu à un renouvellement du "
            "contrat au nom du nouvel occupant.",
            "Toutes les modifications apportées par l’autorité compétente à la réglementation sur le secteur de "
            "l’électricité pendant le cours d’un abonnement seront applicables de plein droit à celui-ci dès la "
            "mise en œuvre de ces modifications.",
        ]),
    ]
    for title, paras in articles:
        add_article(doc, title, paras)

    add_heading_bar(doc, "ANNEXE 1  —  PIÈCES DE LA DEMANDE D’ABONNEMENT")
    annex1 = [
        ("Personne physique",
         "Copie de la carte d’identité nationale pour les Béninois, de la carte d’identité d’étranger pour les "
         "non-Béninois, ou de la carte diplomatique pour les diplomates. Adresse du domicile. Adresse "
         "professionnelle. Numéros de téléphone du domicile et professionnel. Profession. Numéro de police de "
         "l’abonnement précédent. Si le souscripteur est propriétaire : pièce d’identité en cours de validité. "
         "Si le souscripteur est locataire : sa pièce d’identité et celle du propriétaire, en cours de validité."),
        ("Personne morale",
         "Copie de la carte d’identité du représentant. Copie du registre de commerce ou de l’autorisation légale "
         "d’exercer. Adresse de présentation des factures ou toute pièce attestant l’occupation légale du lieu. "
         "Numéro de téléphone professionnel. Numéro de boîte postale. Activité. Numéro de police de l’abonnement "
         "précédent. Numéro IFU. CIP du propriétaire en cas de location, ou le contrat de bail."),
        ("Administration ou collectivité",
         "Lettre d’accompagnement du service demandeur. Local ou lieu à desservir. Copie du titre légal "
         "d’occupation (contrat de location, titre de propriété, bail). Nom. Raison sociale. Adresse de livraison. "
         "Nom du propriétaire."),
        ("Utilisation de l’électricité",
         "Type d’usage prévu. Liste des appareils en place et leur puissance."),
        ("Exonération fiscale",
         "Documents justificatifs du ministère des Finances."),
    ]
    for title, body in annex1:
        add_p(doc, title, size=11, bold=True, color=NAVY, space_before=6, space_after=2)
        add_p(doc, body, size=10.5)

    add_heading_bar(doc, "ANNEXE 2  —  GRILLE TARIFAIRE ET COÛT DE BRANCHEMENT")
    add_p(doc, "Tarifs approuvés par la DGPER. Les cases de tarif au kWh restent à compléter.", size=9, italic=True)

    tariff = doc.add_table(rows=1, cols=4)
    tariff.style = "Table Grid"
    headers = ("Catégorie", "Prime fixe\n(FCFA/mois)", "Tarif 1\n(FCFA/kWh)", "Tarif 2\n(FCFA/kWh)")
    for i, text in enumerate(headers):
        cell = tariff.rows[0].cells[i]
        _shade(cell, RULE)
        cell.text = ""
        run = cell.paragraphs[0].add_run(text)
        _set_run(run, size=9, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))
    categories = [
        "1 — Ménage à consommation faible",
        "2 — Ménage à consommation moyenne",
        "3 — Ménage à consommation élevée",
        "4 — Activités génératrices de revenu",
        "5 — Infrastructure socio-communautaire",
        "6 — Société GSM ou individu",
    ]
    for name in categories:
        row = tariff.add_row().cells
        row[0].text = ""
        r = row[0].paragraphs[0].add_run(name)
        _set_run(r, size=9)
        for cell in row[1:]:
            cell.text = ""
            rr = cell.paragraphs[0].add_run(" ")
            _set_run(rr, size=9)

    add_p(doc, "Frais de branchement", size=11, bold=True, color=NAVY, space_before=10, space_after=3)
    fees = doc.add_table(rows=1, cols=2)
    fees.style = "Table Grid"
    for i, text in enumerate(("Catégorie", "Coût du branchement (FCFA)")):
        cell = fees.rows[0].cells[i]
        _shade(cell, RULE)
        cell.text = ""
        run = cell.paragraphs[0].add_run(text)
        _set_run(run, size=9, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))
    fee_rows = [
        ("Branchement social — ménage à faible consommation", "5 000"),
        ("Catégorie 2 — ménages à consommation moyenne", "10 000"),
        ("Catégorie 3 — ménages à consommation élevée", "10 000"),
        ("Catégorie 4 — activités génératrices de revenu", "10 000"),
        ("Catégorie 5 — infrastructure socio-communautaire", "10 000"),
        ("Catégorie 6", "10 000"),
    ]
    for name, amount in fee_rows:
        row = fees.add_row().cells
        row[0].text = ""
        row[1].text = ""
        a = row[0].paragraphs[0].add_run(name)
        b = row[1].paragraphs[0].add_run(amount)
        _set_run(a, size=9)
        _set_run(b, size=9, bold=True)

    add_p(
        doc,
        "Document établi à partir du contrat d’abonnement type de janvier 2025. "
        "La partie contractante est Mionwa Generation S.A. La marque 1PWR figure en en-tête.",
        size=8, italic=True, color=RGBColor(0x6B, 0x72, 0x80), space_before=12,
    )
    doc.save(path)


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "contrat-abonnement-bj.docx"
    build(out)
    print(out)
