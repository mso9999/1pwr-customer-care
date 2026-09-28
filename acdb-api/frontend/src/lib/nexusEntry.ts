/**
 * Employees who arrive from Nexus are already identified. They should enter
 * Customer Care as staff. The employee/customer/committee chooser is only for
 * someone who opened this site directly.
 */

const FLAG = 'cc_nexus_entry';
const NEXUS_HOST = 'nexus.1pwrafrica.com';

export function markNexusEntry(): void {
  try {
    sessionStorage.setItem(FLAG, '1');
  } catch {
    /* private mode */
  }
}

export function clearNexusEntry(): void {
  try {
    sessionStorage.removeItem(FLAG);
  } catch {
    /* private mode */
  }
}

export function isNexusEntry(): boolean {
  const params = new URLSearchParams(window.location.search);
  if (params.get('direct') === '1') return false;
  if (params.get('from') === 'nexus' || params.has('sso_token')) return true;
  try {
    if (sessionStorage.getItem(FLAG) === '1') return true;
  } catch {
    /* private mode */
  }
  try {
    if (!document.referrer) return false;
    return new URL(document.referrer).hostname === NEXUS_HOST;
  } catch {
    return false;
  }
}

/** Nexus mints a staff token and returns the browser to /auth/sso. */
export function nexusAuthorizeUrl(returnPath = '/dashboard'): string {
  const safe = returnPath.startsWith('/') && !returnPath.startsWith('//')
    ? returnPath
    : '/dashboard';
  const receiver = `https://cc.1pwrafrica.com/auth/sso?return=${encodeURIComponent(safe)}`;
  return `https://${NEXUS_HOST}/sso/authorize?tool=cc&redirect_uri=${encodeURIComponent(receiver)}`;
}
