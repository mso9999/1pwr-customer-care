/**
 * UX tutorial: workflow metadata (step copy lives in i18n tutorial.json).
 * Links / help hashes align 1:1 with each workflow's steps array in locale files.
 */

export interface TutorialWorkflowDef {
  id: string;
  /** i18n key prefix under tutorial.json, e.g. workflows.lifecycle */
  i18nKey: string;
  /** Typical RBAC roles (display only) */
  rolesKey: string;
  links: (string | null)[];
  /** Help page section id — deep link /help#id */
  helpSectionIds: (string | null)[];
}

export const TUTORIAL_WORKFLOWS: TutorialWorkflowDef[] = [
  {
    id: 'orientation',
    i18nKey: 'workflows.orientation',
    rolesKey: 'roles.allEmployees',
    links: ['/dashboard', null, null, '/help', '/tutorial'],
    helpSectionIds: ['dashboard', 'login', null, 'overview', null],
  },
  {
    id: 'lifecycle',
    i18nKey: 'workflows.lifecycle',
    rolesKey: 'roles.onmSuperadmin',
    links: [
      '/customers/new',
      '/payment-verification',
      '/commission',
      '/assign-meter',
      '/customer-data',
      '/pipeline',
    ],
    helpSectionIds: ['customers', 'payments', 'commission', 'meters', 'customers', 'reports'],
  },
  {
    id: 'payments',
    i18nKey: 'workflows.payments',
    rolesKey: 'roles.financeAll',
    links: ['/record-payment', '/payment-verification', '/transactions', '/customer-data'],
    helpSectionIds: ['payments', 'payments', 'data-browsers', 'customers'],
  },
  {
    id: 'reporting',
    i18nKey: 'workflows.reporting',
    rolesKey: 'roles.allEmployees',
    links: ['/om-report', '/financial', '/check-meters', '/tickets'],
    helpSectionIds: ['reports', 'reports', 'meters', 'reports'],
  },
  {
    id: 'commerce',
    i18nKey: 'workflows.commerce',
    rolesKey: 'roles.financeOps',
    links: ['/tariffs', '/financing', '/export'],
    helpSectionIds: ['tariffs', 'financing', 'export'],
  },
  {
    id: 'dataAudit',
    i18nKey: 'workflows.dataAudit',
    rolesKey: 'roles.allEmployees',
    links: ['/accounts', '/tables', '/mutations', '/sync'],
    helpSectionIds: ['data-browsers', 'data-browsers', 'admin', 'admin'],
  },
];
