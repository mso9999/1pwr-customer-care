import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { nexusSsoLogin } from '../lib/api';

/**
 * Nexus SSO receiver (route: /auth/sso).
 *
 * Nexus /sso/authorize redirects here with ?sso_token=<Firebase custom
 * token>&nonce=...&from=nexus (+ our own ?return= path). We POST the token to
 * the backend /api/auth/sso, which verifies it and returns the normal CC
 * employee JWT; then we store the session and resume at ?return= (or the
 * dashboard). The monthly staff PIN was already checked by Nexus.
 */
export default function SsoReceiverPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const handled = useRef(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (handled.current) return;
    handled.current = true;

    const params = new URLSearchParams(window.location.search);
    const token = params.get('sso_token');
    const from = params.get('from');
    // Same-site path to resume after sign-in (guard against open redirect).
    const rawReturn = params.get('return') || '/dashboard';
    const returnTo =
      rawReturn.startsWith('/') && !rawReturn.startsWith('//') ? rawReturn : '/dashboard';

    if (!token || from !== 'nexus') {
      setError('Invalid sign-on link.');
      return;
    }

    nexusSsoLogin(token)
      .then((res) => {
        login(res.access_token, res.user as never);
        navigate(returnTo, { replace: true });
      })
      .catch((err: Error) => {
        console.error('[Nexus SSO] failed', err);
        setError(err.message || 'Your sign-in link is invalid or expired.');
      });
  }, [login, navigate]);

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-blue-100 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-lg p-8 max-w-md w-full text-center">
        {error ? (
          <>
            <p className="text-red-600 mb-3">{error}</p>
            <p className="text-gray-500 text-sm">
              Relaunch Customer Care from{' '}
              <a href="https://nexus.1pwrafrica.com" className="text-blue-600 underline">
                Nexus
              </a>{' '}
              or{' '}
              <a href="/login?fallback=1" className="text-blue-600 underline">
                sign in manually
              </a>
              .
            </p>
          </>
        ) : (
          <>
            <div className="h-8 w-8 mx-auto mb-3 animate-spin rounded-full border-4 border-blue-600 border-t-transparent" />
            <p className="text-gray-500">Signing you in via Nexus…</p>
          </>
        )}
      </div>
    </div>
  );
}
