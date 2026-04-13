import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import type { ReactNode } from 'react';

/** Match Layout.tsx: FR toggle sets `fr`; resolved locale may be `fr-*`. */
export function useHelpLangIsFr(): boolean {
  const { i18n } = useTranslation();
  return Boolean(i18n.language?.startsWith('fr'));
}

/* ------------------------------------------------------------------ */
/*  Shared helper components                                          */
/* ------------------------------------------------------------------ */

export function P({ children }: { children: ReactNode }) {
  return <p className="text-sm text-gray-700 leading-relaxed mb-3">{children}</p>;
}
export function Bold({ children }: { children: ReactNode }) {
  return <strong className="font-semibold text-gray-900">{children}</strong>;
}
export function Code({ children }: { children: ReactNode }) {
  return <code className="px-1.5 py-0.5 bg-gray-100 rounded text-xs font-mono text-blue-700">{children}</code>;
}
export function PageLink({ to, children }: { to: string; children: ReactNode }) {
  return <Link to={to} className="text-blue-600 hover:underline font-medium">{children}</Link>;
}
export function Ol({ children }: { children: ReactNode }) {
  return <ol className="list-decimal list-inside text-sm text-gray-700 space-y-1.5 mb-3 ml-1">{children}</ol>;
}
export function Ul({ children }: { children: ReactNode }) {
  return <ul className="list-disc list-inside text-sm text-gray-700 space-y-1.5 mb-3 ml-1">{children}</ul>;
}
export function SubHead({ children }: { children: ReactNode }) {
  return <h4 className="text-sm font-bold text-gray-800 mt-5 mb-2">{children}</h4>;
}
export function Tip({ children }: { children: ReactNode }) {
  const { t } = useTranslation(['help']);
  return (
    <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm text-blue-800 mb-3">
      <span className="font-semibold">{t('help:tip')}</span> {children}
    </div>
  );
}
export function Warning({ children }: { children: ReactNode }) {
  const { t } = useTranslation(['help']);
  return (
    <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-800 mb-3">
      <span className="font-semibold">{t('help:important')}</span> {children}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Section type                                                      */
/* ------------------------------------------------------------------ */

export interface HelpSection {
  id: string;
  content: ReactNode;
}

/* ------------------------------------------------------------------ */
/*  Feature-map table (shared data, translated headers)               */
/* ------------------------------------------------------------------ */

const FEATURE_ROWS: [string, string, string, string, string][] = [
  ['Customer Mgmt',  'Gestion clients',       'Search & browse customers',         'Rechercher et parcourir les clients',      '/customers'],
  ['Customer Mgmt',  'Gestion clients',       'Register new customer',             'Créer un nouveau client',                  '/customers/new'],
  ['Customer Mgmt',  'Gestion clients',       'Customer profile & detail',         'Profil et détail du client',               '/customers/:id'],
  ['Customer Mgmt',  'Gestion clients',       'Customer data & transactions',      'Données et transactions du client',        '/customer-data'],
  ['Customer Mgmt',  'Gestion clients',       'Commission customer',               'Mise en service',                          '/commission'],
  ['Metering',       'Comptage',              'View & search meters',              'Afficher et rechercher des compteurs',     '/meters'],
  ['Metering',       'Comptage',              'Assign meter to customer',          'Attribuer un compteur à un client',        '/assign-meter'],
  ['Metering',       'Comptage',              'Check meter comparison',            'Comparaison des compteurs de contrôle',    '/check-meters'],
  ['Payments',       'Paiements',             'Record missed payment',             'Enregistrer un paiement manqué',           '/record-payment'],
  ['Payments',       'Paiements',             'Payment verification',              'Vérification des paiements',               '/payment-verification'],
  ['Financing',      'Financement',           'Product templates & agreements',    'Modèles de produits et accords',           '/financing'],
  ['Financing',      'Financement',           'Extend credit (from customer page)','Accorder un crédit (depuis la fiche client)', '/customers/:id'],
  ['Reports',        'Rapports',              'O&M quarterly report',              'Rapport trimestriel O&M',                  '/om-report'],
  ['Reports',        'Rapports',              'Financial analytics (ARPU)',        'Analyses financières (ARPU)',              '/financial'],
  ['Reports',        'Rapports',              'Onboarding pipeline',               'Pipeline d\'intégration',                  '/pipeline'],
  ['Reports',        'Rapports',              'Maintenance / ticket log',          'Journal maintenance / tickets',            '/tickets'],
  ['Data',           'Données',               'Accounts / Transactions / Tables',  'Comptes / Transactions / Tables',          '/accounts'],
  ['Data',           'Données',               'Export to CSV / XLSX',              'Exporter en CSV / XLSX',                   '/export'],
  ['Admin',          'Administration',        'Tariff management',                 'Gestion des tarifs',                       '/tariffs'],
  ['Admin',          'Administration',        'Role management',                   'Gestion des rôles',                        '/admin/roles'],
  ['Admin',          'Administration',        'Audit trail',                       'Journal d\'audit',                         '/mutations'],
  ['Admin',          'Administration',        'UGridPlan sync',                    'Synchronisation uGridPlan',                '/sync'],
];

/* ------------------------------------------------------------------ */
/*  ACCDB diff table rows                                             */
/* ------------------------------------------------------------------ */

const ACCDB_ROWS_EN: [string, string][] = [
  ['Windows RDP required',                    'Web browser from any device'],
  ['VBA forms in Access database',            'Modern React web application'],
  ['Dropbox file paths for imports/exports',  'In-browser data entry and download'],
  ['Spreadsheet-based bulk registration',     'Web forms + UGridPlan sync'],
  ['Spreadsheet-based payment verification',  'In-portal verification queue with bulk actions'],
  ['Reports exported to Dropbox directory',   'Interactive charts + CSV/XLSX export'],
  ['No financing capability',                 'Full asset financing with contract generation'],
  ['Manual kWh balance tracking',             'Automated balance engine'],
  ['No meter comparison',                     'Check meter deviation analysis'],
  ['No real-time data',                       'Live SparkMeter + 1Meter data'],
  ['Single-user at a time',                   'Multi-user concurrent access'],
  ['No audit trail',                          'Full mutation logging with revert capability'],
  ['No customer self-service',                'Customer login with personal dashboard'],
];

const ACCDB_ROWS_FR: [string, string][] = [
  ['Connexion Windows RDP requise',                          'Navigateur web depuis n\'importe quel appareil'],
  ['Formulaires VBA dans Access',                            'Application web React moderne'],
  ['Chemins Dropbox pour imports/exports',                   'Saisie et téléchargement directement dans le navigateur'],
  ['Inscription en masse par tableur',                       'Formulaires web + synchronisation uGridPlan'],
  ['Vérification des paiements par tableur',                 'File de vérification dans le portail avec actions groupées'],
  ['Rapports exportés vers un dossier Dropbox',              'Graphiques interactifs + export CSV/XLSX'],
  ['Aucune fonctionnalité de financement',                   'Financement d\'actifs complet avec génération de contrats'],
  ['Suivi manuel du solde kWh',                              'Moteur de solde automatisé'],
  ['Aucune comparaison de compteurs',                        'Analyse des écarts des compteurs de contrôle'],
  ['Pas de données en temps réel',                           'Données SparkMeter + 1Meter en direct'],
  ['Un seul utilisateur à la fois',                          'Accès multi-utilisateurs simultané'],
  ['Aucun journal d\'audit',                                 'Journalisation complète des modifications avec possibilité d\'annulation'],
  ['Pas de libre-service pour les clients',                  'Connexion client avec tableau de bord personnel'],
];

/* ------------------------------------------------------------------ */
/*  Section content components (bilingual)                            */
/* ------------------------------------------------------------------ */

function OverviewContent() {
  const { t } = useTranslation(['help']);
  const fr = useHelpLangIsFr();

  return (
    <>
      <P>
        {fr
          ? <>Le <Bold>portail Service Client 1PWR (CC)</Bold> est une application web de gestion des opérations clients de mini-réseaux. Il remplace l'ancien système basé sur Access (ACCDB). Toutes les opérations se font via un navigateur web — aucun RDP, logiciel de bureau ou partage de fichiers n'est nécessaire.</>
          : <>The <Bold>1PWR Customer Care (CC) Portal</Bold> is a web-based application for managing mini-grid customer operations. It replaces the former ACCDB-based database system. All operations are performed through a web browser — no RDP, desktop software, or file shares are required.</>}
      </P>
      <P>
        {fr
          ? <>Accédez au portail à l'adresse <Bold>cc.1pwrafrica.com</Bold>. Il fonctionne sur ordinateurs, tablettes et téléphones.</>
          : <>Access the portal at <Bold>cc.1pwrafrica.com</Bold>. It works on desktops, tablets, and phones.</>}
      </P>
      <SubHead>{fr ? 'Paiements mobiles et pipeline de données' : 'Mobile payments & data pipeline'}</SubHead>
      <P>
        {fr
          ? <>Les confirmations <Bold>M-Pesa</Bold> (Lesotho) ou <Bold>MTN MoMo</Bold> (Bénin) sont reçues par la passerelle SMS nationale, puis <Bold>reflétées en JSON</Bold> vers l’API CC (<Code>/api/sms/incoming</Code> ou <Code>/api/bn/sms/incoming</Code>). Le serveur enregistre le paiement dans <Bold>1PDB</Bold>, puis crédite le bon compteur SparkMeter (<Bold>Koios</Bold> ou <Bold>ThunderCloud</Bold> selon le site). Les crédits manuels passent par <PageLink to="/record-payment">Enregistrer un paiement</PageLink> — ne pas créditer directement dans Koios.</>
          : <><Bold>M-Pesa</Bold> (Lesotho) or <Bold>MTN MoMo</Bold> (Benin) confirmations are received by the national SMS gateway, then <Bold>mirrored as JSON</Bold> to the CC API (<Code>/api/sms/incoming</Code> or <Code>/api/bn/sms/incoming</Code>). The server records the payment in <Bold>1PDB</Bold>, then credits the correct SparkMeter account (<Bold>Koios</Bold> vs <Bold>ThunderCloud</Bold> depends on site). Manual credits use <PageLink to="/record-payment">Record Payment</PageLink> — do not credit customers directly in Koios.</>}
      </P>
      <SubHead>{fr ? 'Pays et langue' : 'Country & language'}</SubHead>
      <P>
        {fr
          ? <>Le sélecteur de pays (haut de l’écran) bascule l’API entre Lesotho (<Code>/api</Code>) et le Bénin (<Code>/api/bn</Code>). La langue de l’interface (EN / FR) est indépendante : après avoir choisi le français, tout le manuel ci-dessous s’affiche en français.</>
          : <>The country selector (sidebar) switches the API between Lesotho (<Code>/api</Code>) and Benin (<Code>/api/bn</Code>). Display language (EN / FR) is separate: after choosing French, this entire guide renders in French.</>}
      </P>
      <SubHead>{fr ? 'Carte des fonctionnalités' : 'Feature Map'}</SubHead>
      <div className="overflow-x-auto mb-3">
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="bg-gray-100 text-left text-gray-600">
              <th className="px-3 py-2 font-semibold">{t('help:featureMap.category')}</th>
              <th className="px-3 py-2 font-semibold">{t('help:featureMap.feature')}</th>
              <th className="px-3 py-2 font-semibold">{t('help:featureMap.page')}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {FEATURE_ROWS.map(([catEn, catFr, featEn, featFr, page], i) => (
              <tr key={i} className="hover:bg-gray-50">
                <td className="px-3 py-1.5 text-gray-500">{fr ? catFr : catEn}</td>
                <td className="px-3 py-1.5 font-medium text-gray-800">{fr ? featFr : featEn}</td>
                <td className="px-3 py-1.5"><Code>{page}</Code></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function LoginContent() {
  const fr = useHelpLangIsFr();

  if (fr) {
    return (
      <>
        <SubHead>Connexion employé</SubHead>
        <Ol>
          <li>Aller sur <Bold>cc.1pwrafrica.com</Bold>.</li>
          <li>Entrer votre <Bold>ID employé</Bold> et votre <Bold>mot de passe</Bold>.</li>
          <li>Cliquer sur <Bold>Se connecter</Bold>. Vous serez redirigé vers le tableau de bord.</li>
        </Ol>

        <SubHead>Libre-service client</SubHead>
        <P>Les clients peuvent s'inscrire et se connecter avec leur ID client. La vue client affiche leur tableau de bord personnel avec le solde, l'historique de consommation et les informations de profil.</P>

        <SubHead>Rôles</SubHead>
        <Ul>
          <li><Bold>superadmin</Bold> — Accès complet, y compris la gestion des rôles et la configuration du système.</li>
          <li><Bold>onm_team</Bold> — Fonctionnalités d'exploitation et maintenance, mise en service, gestion des compteurs.</li>
          <li><Bold>finance_team</Bold> — Rapports financiers, vérification des paiements, gestion du financement.</li>
          <li><Bold>generic</Bold> — Accès en lecture aux données clients et aux rapports.</li>
        </Ul>
      </>
    );
  }

  return (
    <>
      <SubHead>Employee Login</SubHead>
      <Ol>
        <li>Navigate to <Bold>cc.1pwrafrica.com</Bold>.</li>
        <li>Enter your <Bold>Employee ID</Bold> and <Bold>password</Bold>.</li>
        <li>Click <Bold>Sign In</Bold>. You will be redirected to the Dashboard.</li>
      </Ol>

      <SubHead>Customer Self-Service</SubHead>
      <P>Customers can register and log in with their customer ID. The customer view shows their personal dashboard with balance, consumption history, and profile information.</P>

      <SubHead>Roles</SubHead>
      <Ul>
        <li><Bold>superadmin</Bold> — Full access, including role management and system configuration.</li>
        <li><Bold>onm_team</Bold> — Operations & maintenance features, commissioning, meter management.</li>
        <li><Bold>finance_team</Bold> — Financial reporting, payment verification, financing management.</li>
        <li><Bold>generic</Bold> — Basic read access to customer data and reports.</li>
      </Ul>
    </>
  );
}

function CustomerMgmtContent() {
  const fr = useHelpLangIsFr();

  if (fr) {
    return (
      <>
        <SubHead>Rechercher et parcourir (<PageLink to="/customers">/customers</PageLink>)</SubHead>
        <P>La liste des clients permet une recherche textuelle par nom, numéro de compte et ID. Cliquez sur une ligne pour ouvrir la fiche détaillée du client.</P>

        <SubHead>Créer un nouveau client (<PageLink to="/customers/new">/customers/new</PageLink>)</SubHead>
        <Ol>
          <li>Cliquer sur <Bold>+ Nouveau client</Bold> ou naviguer vers <Code>/customers/new</Code>.</li>
          <li>Remplir : prénom, nom, ID national, téléphone, site/concession, type de client.</li>
          <li>Cliquer sur <Bold>Enregistrer</Bold>. Un numéro de compte est attribué automatiquement.</li>
        </Ol>
        <Tip>
          Lors de la création d'un client, le compte est <Bold>automatiquement synchronisé avec SparkMeter</Bold> (Koios). Il n'est plus nécessaire de créer le client manuellement dans Koios.
        </Tip>

        <SubHead>Fiche client (<Code>/customers/:id</Code>)</SubHead>
        <P>Affiche tous les champs du dossier client avec possibilité de modification. Les boutons d'action incluent :</P>
        <Ul>
          <li><Bold>Modifier</Bold> — Modification des champs en ligne</li>
          <li><Bold>Voir les données</Bold> — Accéder aux données et transactions du client</li>
          <li><Bold>Mise en service</Bold> — Lancer l'assistant de mise en service</li>
          <li><Bold>Accorder un crédit</Bold> — Ouvrir l'assistant de financement (pour les clients mis en service)</li>
          <li><Bold>Attribuer un compteur</Bold> — Attribuer un compteur à ce client</li>
          <li><Bold>Résilier</Bold> — Mettre fin au service (l'historique est conservé)</li>
        </Ul>

        <SubHead>Consultation des données client (<PageLink to="/customer-data">/customer-data</PageLink>)</SubHead>
        <P>Entrez un numéro de compte (ex : <Code>0045MAK</Code>) pour voir :</P>
        <Ul>
          <li><Bold>Solde</Bold> — Solde actuel en kWh et équivalent en devise</li>
          <li><Bold>Consommation moy.</Bold> — kWh par jour</li>
          <li><Bold>Estimation de recharge</Bold> — Jours restants au rythme actuel</li>
          <li><Bold>Dernier paiement</Bold> — Montant et date du dernier paiement</li>
          <li><Bold>Financement actif</Bold> — Résumé de la dette avec barres de progression (le cas échéant)</li>
          <li><Bold>Historique des transactions</Bold> — Tableau triable avec modification en ligne</li>
          <li><Bold>Graphiques de consommation</Bold> — Vues 24h, 7 jours, 30 jours et 12 mois</li>
        </Ul>
      </>
    );
  }

  return (
    <>
      <SubHead>Search & Browse (<PageLink to="/customers">/customers</PageLink>)</SubHead>
      <P>The customer list supports text search across names, account numbers, and IDs. Click any customer row to open their detail page.</P>

      <SubHead>Register New Customer (<PageLink to="/customers/new">/customers/new</PageLink>)</SubHead>
      <Ol>
        <li>Click <Bold>+ New Customer</Bold> or navigate to <Code>/customers/new</Code>.</li>
        <li>Fill in: first name, last name, national ID, phone number, site/concession, customer type.</li>
        <li>Click <Bold>Save</Bold>. An account number is assigned automatically.</li>
      </Ol>
      <Tip>
        When a customer is created, the account is <Bold>automatically synced to SparkMeter</Bold> (Koios). There is no need to manually create the customer in Koios.
      </Tip>

      <SubHead>Customer Detail (<Code>/customers/:id</Code>)</SubHead>
      <P>Shows all fields from the customer record with edit capability. Action buttons include:</P>
      <Ul>
        <li><Bold>Edit</Bold> — Inline field editing</li>
        <li><Bold>View Data</Bold> — Jump to customer data / transaction view</li>
        <li><Bold>Commission</Bold> — Start the commissioning wizard</li>
        <li><Bold>Extend Credit</Bold> — Open the financing wizard (for commissioned customers)</li>
        <li><Bold>Assign Meter</Bold> — Assign a meter to this customer</li>
        <li><Bold>Decommission</Bold> — Terminate service (preserves all history)</li>
      </Ul>

      <SubHead>Customer Data Lookup (<PageLink to="/customer-data">/customer-data</PageLink>)</SubHead>
      <P>Enter an account number (e.g., <Code>0045MAK</Code>) to see:</P>
      <Ul>
        <li><Bold>Balance</Bold> — Current kWh balance and currency equivalent</li>
        <li><Bold>Avg Consumption</Bold> — kWh per day</li>
        <li><Bold>Estimated Recharge Time</Bold> — Days until balance runs out at current rate</li>
        <li><Bold>Last Payment</Bold> — Most recent payment amount and date</li>
        <li><Bold>Active Financing</Bold> — Debt summary with progress bars (if applicable)</li>
        <li><Bold>Transaction History</Bold> — Sortable table with inline editing</li>
        <li><Bold>Consumption Charts</Bold> — 24h, 7-day, 30-day, and 12-month views</li>
      </Ul>
    </>
  );
}

function CommissionContent() {
  const fr = useHelpLangIsFr();

  if (fr) {
    return (
      <>
        <P>La <PageLink to="/commission">page de mise en service</PageLink> propose un assistant multi-étapes pour finaliser le raccordement d'un client.</P>
        <Ol>
          <li><Bold>Rechercher</Bold> le client par numéro de compte ou ID client.</li>
          <li><Bold>Vérifier/mettre à jour</Bold> les détails : nom, ID national, téléphone, coordonnées GPS, type de client, phase de service, ampérage.</li>
          <li><Bold>Capturer la signature</Bold> — le client signe sur l'écran de la tablette/téléphone.</li>
          <li><Bold>Générer les contrats</Bold> — des PDF bilingues (anglais/sesotho) sont générés et enregistrés.</li>
          <li><Bold>Envoyer le SMS</Bold> — le lien de téléchargement du contrat est envoyé automatiquement au client.</li>
        </Ol>

        <SubHead>Étapes de mise en service</SubHead>
        <P>Le système suit sept étapes par client. Elles peuvent être mises à jour individuellement ou en masse :</P>
        <Ol>
          <li>Frais de raccordement payés</li>
          <li>Frais de tableau de distribution payés</li>
          <li>Tableau de distribution testé</li>
          <li>Tableau de distribution installé</li>
          <li>Câble aérien connecté</li>
          <li>Compteur installé</li>
          <li>Client mis en service</li>
        </Ol>
        <Tip>Utilisez la page <PageLink to="/pipeline">Pipeline d'intégration</PageLink> pour voir combien de clients sont à chaque étape.</Tip>
      </>
    );
  }

  return (
    <>
      <P>The <PageLink to="/commission">commission page</PageLink> provides a multi-step wizard to finalize a customer's service connection.</P>
      <Ol>
        <li><Bold>Look up</Bold> the customer by account number or customer ID.</li>
        <li><Bold>Verify/update</Bold> details: name, national ID, phone, GPS coordinates, customer type, service phase, ampacity.</li>
        <li><Bold>Capture signature</Bold> — the customer signs on the tablet/phone canvas.</li>
        <li><Bold>Generate contracts</Bold> — bilingual (English/Sesotho) PDFs are generated and stored.</li>
        <li><Bold>Send SMS</Bold> — the contract download link is sent to the customer automatically.</li>
      </Ol>

      <SubHead>Commissioning Steps</SubHead>
      <P>The system tracks seven steps per customer. These can be updated individually or in bulk:</P>
      <Ol>
        <li>Connection fee paid</li>
        <li>Readyboard fee paid</li>
        <li>Readyboard tested</li>
        <li>Readyboard installed</li>
        <li>Airdac connected</li>
        <li>Meter installed</li>
        <li>Customer commissioned</li>
      </Ol>
      <Tip>Use the <PageLink to="/pipeline">Onboarding Pipeline</PageLink> page to see how many customers are at each stage.</Tip>
    </>
  );
}

function PaymentsContent() {
  const fr = useHelpLangIsFr();

  if (fr) {
    return (
      <>
        <SubHead>Enregistrer un paiement manqué (<PageLink to="/record-payment">/record-payment</PageLink>)</SubHead>
        <P>Lorsqu'un paiement n'est pas capté par la passerelle SMS (ex : téléphone hors ligne), enregistrez-le manuellement :</P>
        <Ol>
          <li>Entrer le <Bold>numéro de compte</Bold> (ex : <Code>0045MAK</Code>).</li>
          <li>Entrer le <Bold>montant</Bold> dans la devise locale.</li>
          <li>Facultatif : spécifier un ID compteur et une note.</li>
          <li>Cliquer sur <Bold>Enregistrer le paiement</Bold>.</li>
        </Ol>
        <P>Le système convertit automatiquement en kWh au tarif en vigueur, crédite le solde du client et crédite SparkMeter.</P>
        <Warning>
          Si le client a un financement actif, le paiement est automatiquement réparti entre l'électricité et le remboursement de la dette. Un indicateur affiche la répartition sur l'écran de résultat.
        </Warning>

        <SubHead>Vérification des paiements (<PageLink to="/payment-verification">/payment-verification</PageLink>)</SubHead>
        <P>Les frais de raccordement et de tableau de distribution nécessitent une vérification par l'équipe financière.</P>
        <Ol>
          <li>Ouvrir la page Vérification des paiements — par défaut, le statut <Bold>En attente</Bold> est affiché.</li>
          <li>Filtrer par type de paiement ou statut si nécessaire.</li>
          <li>Sélectionner les paiements à l'aide des cases à cocher (sélection groupée disponible).</li>
          <li>Ajouter une note si souhaité.</li>
          <li>Cliquer sur <Bold>Vérifier</Bold> ou <Bold>Rejeter</Bold>.</li>
        </Ol>
        <P>Utilisez le bouton <Bold>Exporter XLSX</Bold> pour télécharger la vue actuelle pour les archives de l'équipe financière.</P>
      </>
    );
  }

  return (
    <>
      <SubHead>Record Missed Payment (<PageLink to="/record-payment">/record-payment</PageLink>)</SubHead>
      <P>When a payment is missed by the SMS gateway (e.g., gateway phone offline), record it manually:</P>
      <Ol>
        <li>Enter the <Bold>account number</Bold> (e.g., <Code>0045MAK</Code>).</li>
        <li>Enter the <Bold>amount</Bold> in local currency.</li>
        <li>Optionally specify a meter ID and note.</li>
        <li>Click <Bold>Record Payment</Bold>.</li>
      </Ol>
      <P>The system converts the currency to kWh at the current tariff rate, credits the customer's balance, and credits SparkMeter.</P>
      <Warning>
        If the customer has active financing, the payment is automatically split between electricity and debt repayment. An indicator shows the split on the result screen.
      </Warning>

      <SubHead>Payment Verification (<PageLink to="/payment-verification">/payment-verification</PageLink>)</SubHead>
      <P>Connection fees and readyboard fees require finance team verification.</P>
      <Ol>
        <li>Open the Payment Verification page — defaults to <Bold>Pending</Bold> status.</li>
        <li>Filter by payment type or status as needed.</li>
        <li>Select payments using checkboxes (select all available).</li>
        <li>Optionally add a note.</li>
        <li>Click <Bold>Verify</Bold> or <Bold>Reject</Bold>.</li>
      </Ol>
      <P>Use the <Bold>Export XLSX</Bold> button to download the current view for the finance team's records.</P>
    </>
  );
}

function BalanceAdjustmentsContent() {
  const fr = useHelpLangIsFr();

  if (fr) {
    return (
      <>
        <Warning>
          Tous les ajustements de solde (crédits, corrections) pour les clients gérés dans ce portail doivent être effectués
          <Bold> uniquement via le portail CC</Bold> (Lesotho et Bénin). Ne créditez jamais un client directement dans Koios / SparkMeter.
        </Warning>
        <P>
          Le portail CC est synchronisé avec Koios / ThunderCloud. Quand vous enregistrez un paiement dans CC, le crédit est
          automatiquement envoyé au compteur SparkMeter. Si vous créditez directement dans Koios, notre base de données
          ne le verra pas et les soldes se désynchroniseront.
        </P>

        <SubHead>Comment enregistrer un ajustement</SubHead>
        <Ol>
          <li>Ouvrir <Bold>cc.1pwrafrica.com</Bold> et se connecter (choisir le pays : Lesotho ou Bénin).</li>
          <li>Aller sur <PageLink to="/record-payment">Enregistrer un paiement</PageLink>.</li>
          <li>Entrer le <Bold>numéro de compte</Bold> du client (ex : <Code>0001GBO</Code>).</li>
          <li>Entrer le <Bold>montant en XOF</Bold>.</li>
          <li>Cliquer sur <Bold>Enregistrer</Bold>. Le système convertit automatiquement en kWh au tarif en vigueur et crédite le compteur.</li>
        </Ol>

        <SubHead>Vérifier le solde d'un client</SubHead>
        <Ol>
          <li>Aller sur <PageLink to="/customer-data">Données client</PageLink>.</li>
          <li>Entrer le numéro de compte.</li>
          <li>Le <Bold>solde kWh</Bold> et l'<Bold>équivalent XOF</Bold> sont affichés en haut de la page.</li>
        </Ol>

        <SubHead>Ce qu'il ne faut pas faire</SubHead>
        <Ul>
          <li>Créditer un client directement sur sparkmeter.cloud / Koios.</li>
          <li>Modifier un solde manuellement dans l'interface web Koios.</li>
          <li>Contourner CC pour des corrections — même les petits montants doivent passer par le portail.</li>
        </Ul>
        <Tip>
          En cas d'erreur de solde, contactez l'équipe technique. Ne tentez pas de corriger directement dans Koios.
        </Tip>
      </>
    );
  }

  return (
    <>
      <Warning>
        All balance adjustments (credits, corrections) for customers in this portal must be made
        <Bold> exclusively through the CC portal</Bold> (Lesotho and Benin). Never credit a customer directly in Koios / SparkMeter.
      </Warning>
      <P>
        The CC portal is synchronized with Koios / ThunderCloud. When you record a payment in CC, the credit is
        automatically pushed to SparkMeter. If you credit directly in Koios, our database
        won't see it and balances will drift out of sync.
      </P>

      <SubHead>How to Record an Adjustment</SubHead>
      <Ol>
        <li>Open <Bold>cc.1pwrafrica.com</Bold> and log in (select country: Lesotho or Benin).</li>
        <li>Go to <PageLink to="/record-payment">Record Payment</PageLink>.</li>
        <li>Enter the customer's <Bold>account number</Bold> (e.g., <Code>0001GBO</Code>).</li>
        <li>Enter the <Bold>amount in XOF</Bold>.</li>
        <li>Click <Bold>Record</Bold>. The system automatically converts to kWh at the current tariff and credits the meter.</li>
      </Ol>

      <SubHead>Checking a Customer's Balance</SubHead>
      <Ol>
        <li>Go to <PageLink to="/customer-data">Customer Data</PageLink>.</li>
        <li>Enter the account number.</li>
        <li>The <Bold>kWh balance</Bold> and <Bold>XOF equivalent</Bold> are displayed at the top of the page.</li>
      </Ol>

      <SubHead>What NOT to Do</SubHead>
      <Ul>
        <li>Credit a customer directly on sparkmeter.cloud / Koios.</li>
        <li>Manually modify a balance in the Koios web interface.</li>
        <li>Bypass CC for corrections — even small amounts must go through the portal.</li>
      </Ul>
      <Tip>
        If you spot a balance error, contact the technical team. Do not attempt to fix it directly in Koios.
      </Tip>
    </>
  );
}

function FinancingContent() {
  const fr = useHelpLangIsFr();

  if (fr) {
    return (
      <>
        <P>Le système de financement (<PageLink to="/financing">/financing</PageLink>) permet d'accorder un crédit aux clients pour des équipements comme les tableaux de distribution, les réfrigérateurs ou les lanternes solaires. La dette est suivie séparément du solde d'électricité afin que la coupure du compteur prépayé continue de fonctionner normalement.</P>

        <SubHead>Modèles de produits</SubHead>
        <P>Aller dans <Bold>Financement → Modèles de produits</Bold> pour définir des modèles réutilisables :</P>
        <Ul>
          <li><Bold>Nom</Bold> — ex : « Tableau de distribution », « Réfrigérateur »</li>
          <li><Bold>Capital par défaut</Bold> — Montant financé standard</li>
          <li><Bold>Taux d'intérêt</Bold> — ex : 0,10 pour 10 %</li>
          <li><Bold>Frais d'installation</Bold> — Frais d'administration</li>
          <li><Bold>Fraction de remboursement</Bold> — Part de chaque paiement affectée à la dette (ex : 0,20 = 20 %)</li>
          <li><Bold>Taux de pénalité</Bold> — Appliqué au solde en retard</li>
          <li><Bold>Jours de grâce / Intervalle</Bold> — Délai avant pénalité et fréquence de récurrence</li>
        </Ul>

        <SubHead>Accorder un crédit à un client</SubHead>
        <P>Depuis la fiche client, cliquer sur <Bold>Accorder un crédit</Bold>. L'assistant en 4 étapes :</P>
        <Ol>
          <li><Bold>Produit</Bold> — Sélectionner un modèle (pré-remplit les conditions) ou choisir personnalisé.</li>
          <li><Bold>Conditions</Bold> — Ajuster le capital, les intérêts, les frais, la fraction de remboursement, les conditions de pénalité. Le montant total dû est calculé automatiquement.</li>
          <li><Bold>Signature</Bold> — Le client signe sur l'écran pour accepter les conditions.</li>
          <li><Bold>Vérification et confirmation</Bold> — Résumé de toutes les conditions, puis cliquer sur Créer l'accord.</li>
        </Ol>
        <P>Un PDF bilingue signé de l'accord de financement est généré et joint au dossier du client.</P>

        <SubHead>Répartition des paiements</SubHead>
        <Warning>
          Dès qu'un client a un accord de financement actif, <Bold>chaque paiement</Bold> est automatiquement réparti :
        </Warning>
        <Ul>
          <li><Bold>Paiements réguliers</Bold> — Répartis selon la fraction de remboursement. Ex : 100 M avec 20 % → 20 M pour la dette, 80 M pour l'électricité.</li>
          <li><Bold>Paiements dédiés à la dette</Bold> — Si le montant se termine par <Bold>1</Bold> ou <Bold>9</Bold> (ex : 51 M, 101 M, 79 M), la <Bold>totalité</Bold> du montant est affectée à la dette.</li>
          <li><Bold>Accords multiples</Bold> — Les paiements s'appliquent au plus ancien d'abord (FIFO).</li>
        </Ul>

        <SubHead>Tableau des accords</SubHead>
        <P>L'onglet <Bold>Accords</Bold> affiche tous les accords. Filtrer par statut : Actif, Remboursé, En défaut, Annulé. Cliquer sur une ligne pour voir le grand livre complet des paiements, pénalités et ajustements.</P>

        <SubHead>Pénalités automatiques</SubHead>
        <P>Le système effectue une vérification quotidienne des pénalités. Si aucun paiement n'est reçu dans le délai des <Bold>jours de grâce</Bold>, une pénalité de <Bold>taux de pénalité × solde restant dû</Bold> est ajoutée. Les pénalités se répètent à l'intervalle configuré.</P>
      </>
    );
  }

  return (
    <>
      <P>The financing system (<PageLink to="/financing">/financing</PageLink>) allows extending credit to customers for assets like readyboards, refrigerators, or solar lanterns. The debt is tracked separately from electricity balance so prepaid meter relay cutoff continues to function normally.</P>

      <SubHead>Product Templates</SubHead>
      <P>Go to <Bold>Financing → Product Templates</Bold> tab to define reusable templates:</P>
      <Ul>
        <li><Bold>Name</Bold> — e.g., "Readyboard", "Refrigerator"</li>
        <li><Bold>Default Principal</Bold> — Standard financed amount</li>
        <li><Bold>Interest Rate</Bold> — e.g., 0.10 for 10%</li>
        <li><Bold>Setup Fee</Bold> — Administration fee</li>
        <li><Bold>Repayment Fraction</Bold> — Portion of each payment diverted to debt (e.g., 0.20 = 20%)</li>
        <li><Bold>Penalty Rate</Bold> — Applied to overdue balance</li>
        <li><Bold>Grace Days / Interval</Bold> — How long before penalty, how often it recurs</li>
      </Ul>

      <SubHead>Extending Credit to a Customer</SubHead>
      <P>From the customer detail page, click <Bold>Extend Credit</Bold>. The 4-step wizard:</P>
      <Ol>
        <li><Bold>Product</Bold> — Select a template (pre-fills terms) or choose custom.</li>
        <li><Bold>Terms</Bold> — Adjust principal, interest, fees, repayment fraction, penalty terms. The total owed is computed automatically.</li>
        <li><Bold>Signature</Bold> — Customer signs on the screen to acknowledge the terms.</li>
        <li><Bold>Review &amp; Confirm</Bold> — Summary of all terms, then click Create Agreement.</li>
      </Ol>
      <P>A signed bilingual PDF financing agreement is generated and attached to the customer's records.</P>

      <SubHead>Payment Splitting</SubHead>
      <Warning>
        Once a customer has an active financing agreement, <Bold>every payment</Bold> is automatically split:
      </Warning>
      <Ul>
        <li><Bold>Regular payments</Bold> — Split per the repayment fraction. E.g., M100 with 20% fraction → M20 to debt, M80 to electricity.</li>
        <li><Bold>Dedicated debt payments</Bold> — If the amount ends in digit <Bold>1</Bold> or <Bold>9</Bold> (e.g., M51, M101, M79), the <Bold>entire</Bold> amount goes to debt.</li>
        <li><Bold>Multiple agreements</Bold> — Payments apply to the oldest (FIFO) first.</li>
      </Ul>

      <SubHead>Agreements Table</SubHead>
      <P>The <Bold>Agreements</Bold> tab shows all agreements. Filter by status: Active, Paid Off, Defaulted, Cancelled. Click any row to see the full ledger of payments, penalties, and adjustments.</P>

      <SubHead>Automatic Penalties</SubHead>
      <P>The system runs a daily penalty check. If no payment has been received within the <Bold>grace days</Bold>, a penalty of <Bold>penalty rate × outstanding balance</Bold> is added. Penalties repeat at the configured interval.</P>
    </>
  );
}

function MetersContent() {
  const fr = useHelpLangIsFr();

  if (fr) {
    return (
      <>
        <SubHead>Registre des compteurs (<PageLink to="/meters">/meters</PageLink>)</SubHead>
        <P>Parcourez et recherchez tous les compteurs. Chaque fiche affiche : ID compteur, numéro de compte, communauté/site, statut et type.</P>

        <SubHead>Attribuer un compteur (<PageLink to="/assign-meter">/assign-meter</PageLink>)</SubHead>
        <P>Attribuez un compteur à un compte client ou réattribuez entre comptes. L'historique des attributions est conservé.</P>

        <SubHead>Comparaison des compteurs de contrôle (<PageLink to="/check-meters">/check-meters</PageLink>)</SubHead>
        <P>Compare les relevés de production SparkMeter (SM) avec les relevés des compteurs de contrôle 1Meter (1M) :</P>
        <Ul>
          <li>Séries temporelles horaires en kWh avec plage configurable (7 / 14 / 30 jours).</li>
          <li>Statistiques d'écart par compteur : total %, moyenne %, écart-type.</li>
          <li>Résumé de l'écart total sur l'ensemble du parc.</li>
          <li>Indicateurs de santé : en ligne (vert), obsolète (jaune), hors ligne (rouge).</li>
        </Ul>

        <SubHead>Cycle de vie des compteurs</SubHead>
        <P>Les compteurs suivent un cycle de vie : <Code>actif</Code> → <Code>inactif</Code> → <Code>déclassé</Code> → <Code>maintenance</Code>. Tous les changements de statut sont enregistrés dans le journal d'audit.</P>
      </>
    );
  }

  return (
    <>
      <SubHead>Meter Registry (<PageLink to="/meters">/meters</PageLink>)</SubHead>
      <P>Browse and search all meters. Each record shows: meter ID, account number, community/site, status, and type.</P>

      <SubHead>Assign Meter (<PageLink to="/assign-meter">/assign-meter</PageLink>)</SubHead>
      <P>Assign a meter to a customer account or reassign between accounts. History of meter assignments is tracked.</P>

      <SubHead>Check Meter Comparison (<PageLink to="/check-meters">/check-meters</PageLink>)</SubHead>
      <P>Compares SparkMeter (SM) production readings against 1Meter (1M) check meter readings:</P>
      <Ul>
        <li>Hourly kWh time series with configurable time range (7 / 14 / 30 days).</li>
        <li>Per-meter deviation statistics: total %, mean %, standard deviation.</li>
        <li>Fleet-wide total deviation summary across all check meters.</li>
        <li>Meter health indicators: online (green), stale (yellow), offline (red).</li>
      </Ul>

      <SubHead>Meter Lifecycle</SubHead>
      <P>Meters follow a lifecycle: <Code>active</Code> → <Code>inactive</Code> → <Code>decommissioned</Code> → <Code>maintenance</Code>. All status changes are logged in the mutation audit trail.</P>
    </>
  );
}

function ReportsContent() {
  const fr = useHelpLangIsFr();

  if (fr) {
    return (
      <>
        <SubHead>Rapport trimestriel O&M (<PageLink to="/om-report">/om-report</PageLink>)</SubHead>
        <P>Graphiques interactifs conformes au format de rapport trimestriel SMP O&M :</P>
        <Ul>
          <li>Statistiques clients par site (total, actifs, nouveaux par trimestre)</li>
          <li>Croissance trimestrielle des raccordements</li>
          <li>Consommation et revenus par site et par trimestre</li>
          <li>Production vs consommation</li>
          <li>Tendances de consommation moyenne par client</li>
          <li>Consommation par ancienneté du client</li>
        </Ul>

        <SubHead>Analyses financières (<PageLink to="/financial">/financial</PageLink>)</SubHead>
        <P>Analyses de revenus et ARPU incluant les revenus mensuels par site, les tendances ARPU, la répartition par type de paiement et les comparaisons de croissance des revenus.</P>

        <SubHead>Pipeline d'intégration (<PageLink to="/pipeline">/pipeline</PageLink>)</SubHead>
        <P>Visualisation en entonnoir montrant la progression des clients à travers les étapes de mise en service. Comprend les pourcentages de déperdition, le filtrage par site, des cartes récapitulatives (total inscrits, entièrement mis en service, taux de conversion) et un tableau détaillé.</P>
      </>
    );
  }

  return (
    <>
      <SubHead>O&M Quarterly Report (<PageLink to="/om-report">/om-report</PageLink>)</SubHead>
      <P>Interactive charts matching the SMP O&M quarterly report format:</P>
      <Ul>
        <li>Customer statistics per site (total, active, new per quarter)</li>
        <li>Quarterly customer connection growth</li>
        <li>Consumption and revenue per site per quarter</li>
        <li>Generation vs consumption</li>
        <li>Average consumption per customer trends</li>
        <li>Consumption by customer tenure</li>
      </Ul>

      <SubHead>Financial Analytics (<PageLink to="/financial">/financial</PageLink>)</SubHead>
      <P>Revenue and ARPU analytics including monthly revenue by site, ARPU trends, payment type breakdown, and revenue growth comparisons.</P>

      <SubHead>Onboarding Pipeline (<PageLink to="/pipeline">/pipeline</PageLink>)</SubHead>
      <P>A funnel visualization showing customer progress through commissioning stages. Includes drop-off percentages, site filtering, summary cards (total registered, fully commissioned, conversion rate), and a detailed table.</P>
    </>
  );
}

function ExportContent() {
  const fr = useHelpLangIsFr();

  if (fr) {
    return (
      <>
        <P>La <PageLink to="/export">page d'export</PageLink> vous permet de télécharger n'importe quelle table de la base de données en CSV ou XLSX.</P>
        <Ol>
          <li>Sélectionner la table à exporter (clients, comptes, compteurs, transactions, etc.).</li>
          <li>Facultatif : rechercher/filtrer les données.</li>
          <li>Choisir le format : CSV ou Excel (XLSX).</li>
          <li>Cliquer sur <Bold>Exporter</Bold> — le fichier se télécharge dans votre navigateur.</li>
        </Ol>
        <Tip>La page Vérification des paiements dispose aussi d'un bouton <Bold>Exporter XLSX</Bold> pour les archives de l'équipe financière.</Tip>
      </>
    );
  }

  return (
    <>
      <P>The <PageLink to="/export">Export page</PageLink> lets you download any database table as CSV or XLSX.</P>
      <Ol>
        <li>Select the table to export (customers, accounts, meters, transactions, etc.).</li>
        <li>Optionally search/filter the data.</li>
        <li>Select format: CSV or Excel (XLSX).</li>
        <li>Click <Bold>Export</Bold> — the file downloads to your browser.</li>
      </Ol>
      <Tip>The Payment Verification page also has its own <Bold>Export XLSX</Bold> button for finance team records.</Tip>
    </>
  );
}

function TariffsContent() {
  const fr = useHelpLangIsFr();

  if (fr) {
    return (
      <>
        <P>La <PageLink to="/tariffs">page Tarifs</PageLink> gère les taux tarifaires d'électricité par site/concession.</P>
        <Ul>
          <li>Consulter les taux tarifaires actuels pour chaque site.</li>
          <li>Mettre à jour les taux — les modifications prennent effet immédiatement pour les futurs paiements.</li>
          <li>La configuration des tarifs par pays est prise en charge.</li>
        </Ul>
        <Warning>La modification d'un tarif affecte la conversion en kWh des paiements futurs. Les transactions existantes ne sont pas recalculées.</Warning>
      </>
    );
  }

  return (
    <>
      <P>The <PageLink to="/tariffs">Tariffs page</PageLink> manages electricity tariff rates per site/concession.</P>
      <Ul>
        <li>View current tariff rates for each site.</li>
        <li>Update rates — changes take effect for future payments immediately.</li>
        <li>Country-specific tariff configuration is supported.</li>
      </Ul>
      <Warning>Changing a tariff rate affects how future payments are converted to kWh. Existing transactions are not recalculated.</Warning>
    </>
  );
}

function AdminContent() {
  const fr = useHelpLangIsFr();

  if (fr) {
    return (
      <>
        <SubHead>Gestion des rôles (<PageLink to="/admin/roles">/admin/roles</PageLink>)</SubHead>
        <P>Accessible aux utilisateurs <Bold>superadmin</Bold> uniquement :</P>
        <Ul>
          <li>Voir tous les utilisateurs et leurs rôles actuels.</li>
          <li>Attribuer ou modifier les rôles (superadmin, onm_team, finance_team, generic).</li>
          <li>Activer ou désactiver des comptes utilisateurs.</li>
        </Ul>

        <SubHead>Journal d'audit des modifications (<PageLink to="/mutations">/mutations</PageLink>)</SubHead>
        <P>Chaque modification de données (création, mise à jour, suppression) est enregistrée avec l'horodatage, l'utilisateur, la table/l'enregistrement concerné et les anciennes/nouvelles valeurs. Les modifications peuvent être examinées et annulées si nécessaire.</P>

        <SubHead>Synchronisation uGridPlan (<PageLink to="/sync">/sync</PageLink>)</SubHead>
        <P>Le portail s'intègre avec uGridPlan (<Bold>ugp.1pwrafrica.com</Bold>) via API pour la synchronisation des données clients, la création de tickets O&M et le rattachement enquêtes/raccordements. La page de synchronisation affiche l'état des opérations récentes.</P>

        <SubHead>Explorateur de tables brutes (<PageLink to="/tables">/tables</PageLink>)</SubHead>
        <P>Pour les utilisateurs avancés : parcourez n'importe quelle table de la base de données directement avec des fonctionnalités de tri, filtrage et modification en ligne.</P>

        <SubHead>Mises à jour du portail (équipe technique)</SubHead>
        <P>Les changements fusionnés dans la branche <Code>main</Code> du dépôt déclenchent <Bold>GitHub Actions</Bold> : compilation du frontend (Vite), synchronisation du code vers le serveur Linux, installation des dépendances Python si besoin, redémarrage des services <Code>1pdb-api</Code>. L’URL publique <Bold>cc.1pwrafrica.com</Bold> reste la même ; aucune action n’est requise sur le terrain.</P>
      </>
    );
  }

  return (
    <>
      <SubHead>Role Management (<PageLink to="/admin/roles">/admin/roles</PageLink>)</SubHead>
      <P>Available to <Bold>superadmin</Bold> users only:</P>
      <Ul>
        <li>View all users and their current roles.</li>
        <li>Assign or change roles (superadmin, onm_team, finance_team, generic).</li>
        <li>Activate or deactivate user accounts.</li>
      </Ul>

      <SubHead>Mutation Audit Trail (<PageLink to="/mutations">/mutations</PageLink>)</SubHead>
      <P>Every data change (create, update, delete) is logged with timestamp, user, table/record affected, and old/new values. Changes can be reviewed and reverted if needed.</P>

      <SubHead>UGridPlan Sync (<PageLink to="/sync">/sync</PageLink>)</SubHead>
      <P>The portal integrates with UGridPlan (<Bold>ugp.1pwrafrica.com</Bold>) via API for customer data synchronization, O&M ticket creation, and survey/connection binding. The sync page shows recent operation status.</P>

      <SubHead>Raw Table Browser (<PageLink to="/tables">/tables</PageLink>)</SubHead>
      <P>For advanced users: browse any database table directly with sorting, filtering, and inline editing capabilities.</P>

      <SubHead>Portal software updates (technical)</SubHead>
      <P>Changes merged to the <Code>main</Code> branch trigger <Bold>GitHub Actions</Bold>: the frontend is built (Vite), backend and static files are rsync’d to the Linux host, Python dependencies refresh if needed, and <Code>1pdb-api</Code> services restart. The public URL <Bold>cc.1pwrafrica.com</Bold> is unchanged — no action required from field staff.</P>
    </>
  );
}

function AccdbDiffContent() {
  const fr = useHelpLangIsFr();
  const rows = fr ? ACCDB_ROWS_FR : ACCDB_ROWS_EN;

  return (
    <div className="overflow-x-auto mb-3">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="bg-gray-100 text-left text-gray-600">
            <th className="px-3 py-2 font-semibold">{fr ? 'Ancien (ACCDB)' : 'Old (ACCDB)'}</th>
            <th className="px-3 py-2 font-semibold">{fr ? 'Nouveau (Portail CC)' : 'New (CC Portal)'}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {rows.map(([old, nw], i) => (
            <tr key={i} className="hover:bg-gray-50">
              <td className="px-3 py-1.5 text-red-700 line-through opacity-70">{old}</td>
              <td className="px-3 py-1.5 text-green-700 font-medium">{nw}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Public hook: returns fully-translated sections array               */
/* ------------------------------------------------------------------ */

export function useHelpSections(): HelpSection[] {
  return [
    { id: 'overview',             content: <OverviewContent /> },
    { id: 'login',                content: <LoginContent /> },
    { id: 'customers',            content: <CustomerMgmtContent /> },
    { id: 'commission',           content: <CommissionContent /> },
    { id: 'payments',             content: <PaymentsContent /> },
    { id: 'balance-adjustments',  content: <BalanceAdjustmentsContent /> },
    { id: 'financing',            content: <FinancingContent /> },
    { id: 'meters',               content: <MetersContent /> },
    { id: 'reports',              content: <ReportsContent /> },
    { id: 'export',               content: <ExportContent /> },
    { id: 'tariffs',              content: <TariffsContent /> },
    { id: 'admin',                content: <AdminContent /> },
    { id: 'accdb-diff',           content: <AccdbDiffContent /> },
  ];
}
