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

  if (requireRole && !requireRole.includes(user.role)) {
    return <div className="text-center py-16 text-red-600">Access denied. Required role: {requireRole.join(', ')}</div>;
  }

  return <>{children}</>;
}
