import { Navigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';

interface Props {
  children: React.ReactNode;
  requireEmployee?: boolean;
  requireRole?: string[];
}

export default function ProtectedRoute({ children, requireEmployee, requireRole }: Props) {
  const { user, loading } = useAuth();

  if (loading) {
    return <div className="flex justify-center items-center h-64"><div className="text-gray-400">Loading...</div></div>;
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  if (requireEmployee && user.user_type !== 'employee') {
    return <Navigate to="/my/profile" replace />;
  }

  if (requireRole && !requireRole.includes(user.role)) {
    return <div className="text-center py-16 text-red-600">Access denied. Required role: {requireRole.join(', ')}</div>;
  }

  return <>{children}</>;
}
