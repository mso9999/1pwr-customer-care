import { useState } from 'react';
import { Link, Outlet, useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';

export default function Layout() {
  const { user, logout, isEmployee, isCustomer, isSuperadmin } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  const navLink = (to: string, label: string, accent = false) => {
    const active = location.pathname === to || location.pathname.startsWith(to + '/');
    const base = accent
      ? active
        ? 'bg-blue-600 text-white'
        : 'bg-blue-50 text-blue-700 hover:bg-blue-100'
      : active
        ? 'bg-blue-50 text-blue-700'
        : 'text-gray-600 hover:bg-gray-100 hover:text-blue-700';
    return (
      <Link
        to={to}
        onClick={() => setMenuOpen(false)}
        className={`block px-3 py-2 rounded-md text-sm font-medium transition ${base}`}
      >
        {label}
      </Link>
    );
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* ── Header ── */}
      <header className="bg-white shadow-sm sticky top-0 z-40">
        {/* ─ Upper bar: branding + user ─ */}
        <div>
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex items-center justify-between h-12">
            {/* Left: logo + title */}
            <Link to="/" className="flex items-center gap-2.5" onClick={() => setMenuOpen(false)}>
              <img src="/1pwr-logo.png" alt="1PWR" className="h-8 w-auto" />
              <span className="text-lg font-bold text-blue-700 whitespace-nowrap">Customer Care</span>
            </Link>

            {/* Right: user info + hamburger */}
            <div className="flex items-center gap-3">
              {user && (
                <div className="hidden sm:flex items-center gap-3">
                  {isEmployee && (
                    <span className="text-sm font-medium text-gray-700 truncate max-w-[180px]">
                      {user.name || user.user_id}
                    </span>
                  )}
                  {isEmployee && (
                    <span className="px-2 py-0.5 bg-blue-100 text-blue-700 text-xs font-medium rounded-full">{user.role}</span>
                  )}
                  <button onClick={handleLogout} className="text-sm text-red-600 hover:text-red-800 font-medium">Logout</button>
                </div>
              )}

              {/* Hamburger (mobile) */}
              <button
                onClick={() => setMenuOpen(!menuOpen)}
                className="md:hidden p-2 rounded-md text-gray-500 hover:bg-gray-100 focus:outline-none"
                aria-label="Toggle menu"
              >
                {menuOpen ? (
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                ) : (
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                  </svg>
                )}
              </button>
            </div>
          </div>
        </div>

        {/* ─ Lower bar: desktop nav ─ */}
        <div className="hidden md:block border-b border-gray-100 bg-gray-50/60">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="flex items-center gap-1 h-10 -mb-px">
              {isEmployee && (
                <>
                  {navLink('/dashboard', 'Dashboard')}
                  {navLink('/om-report', 'O&M Report', true)}
                  {navLink('/financial', 'Financial', true)}
                  {navLink('/customers', 'Customers')}
                  {navLink('/customer-data', 'Customer Data')}
                  {navLink('/tables', 'Tables')}
                  {navLink('/export', 'Export')}
                  {navLink('/tariffs', 'Tariffs')}
                  {navLink('/mutations', 'Mutations')}
                  {navLink('/sync', 'Sync', true)}
                  {isSuperadmin && navLink('/admin/roles', 'Roles')}
                </>
              )}
              {isCustomer && (
                <>
                  {navLink('/my/dashboard', 'Dashboard')}
                  {navLink('/my/profile', 'My Account')}
                </>
              )}
            </div>
          </div>
        </div>

        {/* ─ Mobile menu panel ─ */}
        {menuOpen && (
          <div className="md:hidden border-t bg-white px-4 pb-4 pt-2 space-y-1 shadow-lg">
            {isEmployee && (
              <>
                {navLink('/dashboard', 'Dashboard')}
                {navLink('/om-report', 'O&M Report', true)}
                {navLink('/financial', 'Financial', true)}
                {navLink('/customers', 'Customers')}
                {navLink('/customer-data', 'Customer Data')}
                {navLink('/tables', 'Tables')}
                {navLink('/export', 'Export')}
                {navLink('/tariffs', 'Tariffs')}
                {navLink('/mutations', 'Mutations')}
                {navLink('/sync', 'Sync', true)}
                {isSuperadmin && navLink('/admin/roles', 'Roles')}
              </>
            )}
            {isCustomer && (
              <>
                {navLink('/my/dashboard', 'Dashboard')}
                {navLink('/my/profile', 'My Account')}
              </>
            )}

            {user && (
              <div className="pt-3 mt-2 border-t flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-gray-700">{user.name || user.user_id}</p>
                  <span className="px-2 py-0.5 bg-blue-100 text-blue-700 text-xs rounded-full">{user.role}</span>
                </div>
                <button onClick={handleLogout} className="text-sm text-red-600 hover:text-red-800 px-3 py-1.5 border border-red-200 rounded-lg">
                  Logout
                </button>
              </div>
            )}
          </div>
        )}
      </header>

      {/* Main content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4 sm:py-6">
        <Outlet />
      </main>
    </div>
  );
}
