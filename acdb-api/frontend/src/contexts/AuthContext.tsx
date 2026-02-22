import { createContext, useContext, useState, useEffect, type ReactNode } from 'react';
import { getMe } from '../lib/api';

interface User {
  user_type: string;
  user_id: string;
  role: string;
  name: string;
  email: string;
  permissions: Record<string, boolean>;
  [key: string]: unknown;
}

interface AuthContextType {
  user: User | null;
  token: string | null;
  login: (token: string, user: User) => void;
  logout: () => void;
  loading: boolean;
  isEmployee: boolean;
  isCustomer: boolean;
  isSuperadmin: boolean;
  canWrite: boolean;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(localStorage.getItem('cc_token'));
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (token) {
      getMe()
        .then((data) => {
          setUser(data as unknown as User);
        })
        .catch(() => {
          localStorage.removeItem('cc_token');
          localStorage.removeItem('cc_user');
          setToken(null);
          setUser(null);
        })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, [token]);

  const login = (newToken: string, newUser: User) => {
    localStorage.setItem('cc_token', newToken);
    localStorage.setItem('cc_user', JSON.stringify(newUser));
    setToken(newToken);
    setUser(newUser);
  };

  const logout = () => {
    localStorage.removeItem('cc_token');
    localStorage.removeItem('cc_user');
    setToken(null);
    setUser(null);
  };

  const isEmployee = user?.user_type === 'employee';
  const isCustomer = user?.user_type === 'customer';
  const isSuperadmin = user?.role === 'superadmin';
  const canWrite = isEmployee;

  return (
    <AuthContext.Provider value={{ user, token, login, logout, loading, isEmployee, isCustomer, isSuperadmin, canWrite }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
