import { Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../contexts/AuthContext';

interface Props {
  children: React.ReactNode;
  requireEmployee?: boolean;
  requireRole?: string[];
}

export default function ProtectedRoute({ children, requireEmployee, requireRole }: Props) {
  const { t } = useTranslation(['common']);
  const { user, loading } = useAuth();

  if (loading) {
    return <div className="flex justify-center items-center h-64"><div className="text-gray-400">{t('common:loading')}</div></div>;
  }

  if (!user) {
    const path = window.location.pathname;
    const params = new URLSearchParams(window.location.search);
    // Customers aren't Nexus users: keep the local login for the root and
    // customer area, and as an emergency fallback (?fallback=1, e.g. Nexus
    // outage). Staff deep links go straight to Nexus, which SSOs back to
    // /auth/sso and resumes at ?return=.
    const customerFriendly = path === '/' || path.startsWith('/my');
    if (customerFriendly || params.get('fallback') === '1') {
      return <Navigate to="/login" replace />;
    }
    window.location.replace(
      'https://nexus.1pwrafrica.com/sso/authorize?tool=cc&redirect_uri=' +
        encodeURIComponent(
          'https://cc.1pwrafrica.com/auth/sso?return=' +
            encodeURIComponent(path + window.location.search)
        )
    );
    return null;
  }

  if (requireEmployee && user.user_type !== 'employee') {
    return <Navigate to="/my/profile" replace />;
  }

  const effectiveRoles = user.roles || user.cc_roles || [user.role];
  if (requireRole && !requireRole.some((role) => effectiveRoles.includes(role))) {
    const countryCode = localStorage.getItem('cc_country') || 'LS';
    const countryName = ({ BN: 'Benin', BJ: 'Benin', LS: 'Lesotho', ZM: 'Zambia' } as Record<string, string>)[countryCode]
      || t('common:privilegeDenied.yourCountry');
    return (
      <div className="max-w-3xl mx-auto my-12 rounded-xl border border-red-200 bg-white shadow-sm overflow-hidden">
        <div className="bg-red-50 px-6 py-4 border-b border-red-200">
          <h1 className="text-lg font-semibold text-red-800">{t('common:privilegeDenied.title')}</h1>
          <p className="text-sm text-red-700 mt-1">{t('common:privilegeDenied.summary')}</p>
        </div>
        <div className="p-6 space-y-5 text-sm text-gray-700">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-lg bg-gray-50 p-4">
              <p className="font-medium text-gray-900">{t('common:privilegeDenied.assigned')}</p>
              <p className="mt-1 font-mono">{effectiveRoles.join(', ') || 'generic'}</p>
            </div>
            <div className="rounded-lg bg-amber-50 p-4">
              <p className="font-medium text-gray-900">{t('common:privilegeDenied.required')}</p>
              <p className="mt-1 font-mono">{requireRole.join(', ')}</p>
            </div>
          </div>
          <div>
            <p className="font-semibold text-gray-900 mb-2">{t('common:privilegeDenied.whoCanChange')}</p>
            <ul className="space-y-2 list-disc pl-5">
              <li>{t('common:privilegeDenied.hrOwner', { country: countryName })}</li>
              <li>{t('common:privilegeDenied.nexusOwner')}</li>
              <li>{t('common:privilegeDenied.ccOwner')}</li>
            </ul>
          </div>
          <p className="rounded-lg bg-blue-50 p-4 text-blue-900">{t('common:privilegeDenied.next')}</p>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
