# Note à l’équipe Bénin — déploiement 1Meter (25/09/2026)

*(English version below.)*

Bonjour à tous,

Merci pour le travail d’installation des passerelles et des 1Meter sur les poteaux. Pour que ce matériel soit visible, supervisé et facturable dans CC, il faut enregistrer chaque étape dans CC au moment où elle est faite. Aujourd’hui, CC ne voit presque rien de ce qui a été posé :

- Un seul 1Meter est attribué à un client (000023021767 → 0001SIN). Les compteurs 000023021718, 000023021750 et 000023021757 ont remonté des données mais ne sont attribués à personne : ils sont invisibles dans la page Compteurs et ne peuvent pas être facturés.
- Aucune passerelle n’est enregistrée sur un poteau dans CC.
- Toutes les passerelles du Bénin sont hors ligne depuis le 23/09 vers 18h43 (heure du Bénin), au même moment. KOT-GW-0001 ne s’est jamais connectée.
- Le parcours opérateur n’est avancé qu’à l’étape « identifiants Starlink » pour SIN et SAM, et n’a pas commencé pour KOT.

CC a été mis à jour aujourd’hui pour vous guider (en français) : le parcours opérateur a deux nouvelles étapes de déploiement, et la page Compteurs affiche un bandeau orange qui liste ce qui manque par site.

## Merci de nous répondre d’ici lundi

1. Où se trouve physiquement chaque passerelle (KOT-GW-0001 à 0004, SIN-GW-0001 à 0003, SAM-GW-0001) : au bureau, ou posée sur un poteau (lequel) ?
2. Que s’est-il passé le 23/09 vers 18h43 : coupure de courant, d’internet ou de Starlink à l’endroit où se trouvaient les passerelles ?
3. Quels compteurs sont déjà installés chez des clients, et pour quel compte ?

## La méthode à suivre à partir de maintenant

Toujours sélectionner **Bénin** dans CC avant de commencer (sinon vous voyez les données du Lesotho).

**A. Au bureau, avant d’aller sur le terrain** — Provisionnement → Parcours opérateur, site choisi, étapes 1 à 10 :
provisionnement avec la station, canari OTA, adressage et validation du lot, approbation de la version (Ingénierie), lot contrôlé, client test.
**Ne posez pas de passerelle sur un poteau tant que les étapes 8 (approbation de la version) et 9 (lot contrôlé) ne sont pas vertes.**

**B. Sur le site, pour chaque passerelle, le jour même :**
1. Posez la passerelle et le boîtier sur le poteau.
2. Provisionnement → **Installation terrain** : choisissez le site, la passerelle et le poteau sur la carte, puis **Installer et vérifier**.
3. Mettez sous tension. CC passe l’installation à « vérifiée en ligne » au premier contact. Sinon, **Lancer le dépannage guidé**.

**C. Pour chaque compteur :**
1. Le client doit déjà être inscrit dans CC (l’inscription se fait avant le raccordement).
2. Câblez le compteur : A, B **et GND**.
3. Provisionnement → **Parc en direct** : vérifiez que le numéro du compteur apparaît. Sinon, appuyez 3 secondes sur le bouton **PRG** de la passerelle et attendez une minute.
4. **Attribuer un compteur** : site, plateforme 1Meter, passerelle + numéro du compteur, compte client, poteau.
5. Terminez la **mise en service** du client.

**D. En fin de journée :**
- Ouvrez la page **Compteurs** : le bandeau orange ne doit plus rien lister pour votre site.
- Dans le parcours opérateur, les étapes 12 et 13 doivent être vertes.
- Envoyez un court point dans le groupe : passerelles posées et enregistrées, compteurs attribués, et tout ce qui est hors ligne.

## À ne pas faire

- Ne déplacez pas un compteur d’une passerelle à une autre sans le signaler (nous voyons les mêmes compteurs passer par KOT-GW-0002, KOT-GW-0003 puis SIN-GW-0001).
- Ne reprovisionnez pas une passerelle qui a déjà un nom `SITE-GW-####`.
- Ne saisissez jamais un mot de passe Starlink dans CC ou dans le groupe WhatsApp.

## Ce que nous faisons de notre côté

- KOT n’a pas encore de projet uGridPlan relié dans CC : **Installation terrain ne fonctionnera pas pour KOT** tant que l’Ingénierie ne l’a pas relié. Nous vous prévenons dès que c’est fait. Pour SIN et SAM, c’est prêt.
- L’Ingénierie traitera l’approbation de la version pour vos sites dès que le canari et la validation seront enregistrés.

Aide complète : CC → Aide → Provisionnement (section « Déployer un site »).

Merci !

---

# Note to the Benin team — 1Meter rollout (25 Sep 2026)

Hello all,

Thank you for the work installing gateways and 1Meters on poles. For that equipment to be visible, monitored and billable in CC, each step has to be recorded in CC as it is done. Today CC sees almost none of what has been installed:

- Only one 1Meter is assigned to a customer (000023021767 → 0001SIN). Meters 000023021718, 000023021750 and 000023021757 have reported data but are not assigned to anyone, so they are invisible on the Meters page and cannot be billed.
- No gateway is recorded on a pole in CC.
- Every Benin gateway has been offline since about 18:43 Benin time on 23 Sep — all at the same moment. KOT-GW-0001 has never connected.
- The operator walkthrough has only reached the "Starlink credentials" step for SIN and SAM, and has not started for KOT.

CC was updated today to guide you (in French): the operator walkthrough has two new rollout steps, and the Meters page shows an amber banner listing what is missing per site.

## Please reply by Monday

1. Where is each gateway physically (KOT-GW-0001 to 0004, SIN-GW-0001 to 0003, SAM-GW-0001): at the office, or on a pole (which one)?
2. What happened on 23 Sep at about 18:43: a power, internet or Starlink outage where the gateways were?
3. Which meters are already installed at customers, and on which account?

## How to approach the work from now on

Always select **Bénin** in CC before starting (otherwise you see Lesotho data).

**A. At the office, before going to the field** — Provisioning → Operator walkthrough, site selected, steps 1–10:
station provisioning, OTA canary, meter addressing and batch validation, release approval (Engineering), controlled batch, test customer.
**Do not put a gateway on a pole until steps 8 (release approval) and 9 (controlled batch) are green.**

**B. On site, for each gateway, the same day:**
1. Mount the gateway and box on the pole.
2. Provisioning → **Field install**: choose the site, the gateway and the pole on the map, then **Install & verify**.
3. Power it. CC marks the install "verified online" on first contact. If not, **Run guided troubleshooting**.

**C. For each meter:**
1. The customer must already be registered in CC (registration comes before connection).
2. Wire the meter: A, B **and GND**.
3. Provisioning → **Fleet live**: confirm the meter serial appears. If not, long-press the gateway's **PRG** button for 3 seconds and wait a minute.
4. **Assign Meter**: site, 1Meter platform, gateway + meter serial, customer account, pole.
5. Finish the customer's **commissioning**.

**D. At the end of the day:**
- Open the **Meters** page: the amber banner should list nothing for your site.
- In the operator walkthrough, steps 12 and 13 should be green.
- Post a short update in the group: gateways installed and recorded, meters assigned, and anything offline.

## Do not

- Move a meter from one gateway to another without reporting it (we see the same meters passing through KOT-GW-0002, KOT-GW-0003 and then SIN-GW-0001).
- Re-provision a gateway that already has a `SITE-GW-####` name.
- Enter a Starlink password in CC or in the WhatsApp group.

## What we are doing on our side

- KOT does not yet have a uGridPlan project linked in CC, so **Field install will not work for KOT** until Engineering links it. We will tell you when it is done. SIN and SAM are ready.
- Engineering will handle release approval for your sites once the canary and validation are recorded.

Full help: CC → Help → Provisioning ("Rolling out a site" section).

Thank you!
