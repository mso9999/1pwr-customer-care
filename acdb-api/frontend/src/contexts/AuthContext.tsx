import { createContext, useContext, useState, useEffect, type ReactNode } from 'react';
import { getMe } from '../lib/api';

interface User {
  user_type: string;
  user_id: string;
  role: string;
  roles?: string[];
  cc_roles?: string[];
  privilege_system?: string;
  privilege_level?: string;
  privilege_actions?: string[];
  privilege_version?: string;
  scope_countries?: string[];
  scope_organizations?: string[];
  role_crud_owners?: string[];
  name: string;
  email: string;
  department?: string;
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
  isRegistrar: boolean;
  isSuperadmin: boolean;
  canWrite: boolean;
  canWriteCustomers: boolean;
  hasPrivilegeAction: (action: string) => boolean;
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
  const isRegistrar = user?.user_type === 'registrar';
  const effectiveRoles = user?.roles || user?.cc_roles || (user?.role ? [user.role] : []);
  const hasSignedCcPrivilege = Boolean(user?.privilege_version && user?.privilege_system === 'cc');
  const hasPrivilegeAction = (action: string) => hasSignedCcPrivilege
    ? Boolean(user?.privilege_actions?.includes(action))
    : action === 'view_customer_operations'
      ? isEmployee
      : action === 'operate_customer_care'
        ? effectiveRoles.some((role) => ['superadmin', 'onm_team', 'engineering'].includes(role))
        : action === 'approve_financial_and_control'
          ? effectiveRoles.some((role) => ['superadmin', 'onm_team', 'finance_team', 'engineering'].includes(role))
          : action === 'administer_cc'
            ? effectiveRoles.includes('superadmin')
            : false;
  const isSuperadmin = hasPrivilegeAction('administer_cc');
  const canWrite = hasSignedCcPrivilege
    ? hasPrivilegeAction('operate_customer_care') || hasPrivilegeAction('approve_financial_and_control')
    : Boolean(user?.permissions?.write_customers || user?.permissions?.write_transactions);
  const canWriteCustomers = hasSignedCcPrivilege
    ? hasPrivilegeAction('operate_customer_care')
    : Boolean(user?.permissions?.write_customers);

  return (
    <AuthContext.Provider value={{ user, token, login, logout, loading, isEmployee, isCustomer, isRegistrar, isSuperadmin, canWrite, canWriteCustomers, hasPrivilegeAction }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
