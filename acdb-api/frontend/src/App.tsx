import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { CountryProvider } from './contexts/CountryContext';
import Layout from './components/Layout';
import ProtectedRoute from './components/ProtectedRoute';
import LoginPage from './pages/LoginPage';
import DashboardPage from './pages/DashboardPage';
import CustomersPage from './pages/CustomersPage';
import CustomerDetailPage from './pages/CustomerDetailPage';
import NewCustomerWizard from './pages/NewCustomerWizard';
import AssignMeterPage from './pages/AssignMeterPage';
import CustomerDataPage from './pages/CustomerDataPage';
import TablesPage from './pages/TablesPage';
import TableBrowserPage from './pages/TableBrowserPage';
import ExportPage from './pages/ExportPage';
import MyProfilePage from './pages/MyProfilePage';
import CustomerDashboardPage from './pages/CustomerDashboardPage';
import AdminRolesPage from './pages/AdminRolesPage';
import MutationsPage from './pages/MutationsPage';
import OMReportPage from './pages/OMReportPage';
import FinancialPage from './pages/FinancialPage';
import SyncPage from './pages/SyncPage';
import CommissionCustomerPage from './pages/CommissionCustomerPage';
import TariffManagementPage from './pages/TariffManagementPage';

function HomeRedirect() {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  if (user.user_type === 'customer') return <Navigate to="/my/dashboard" replace />;
  return <Navigate to="/dashboard" replace />;
}

export default function App() {
  return (
    <BrowserRouter>
      <CountryProvider>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<ProtectedRoute><Layout /></ProtectedRoute>}>
            <Route index element={<HomeRedirect />} />
            {/* Employee routes */}
            <Route path="dashboard" element={<ProtectedRoute requireEmployee><DashboardPage /></ProtectedRoute>} />
            <Route path="customers" element={<ProtectedRoute requireEmployee><CustomersPage /></ProtectedRoute>} />
            <Route path="customers/new" element={<ProtectedRoute requireEmployee><NewCustomerWizard /></ProtectedRoute>} />
            <Route path="customers/:id" element={<ProtectedRoute requireEmployee><CustomerDetailPage /></ProtectedRoute>} />
            <Route path="assign-meter" element={<ProtectedRoute requireEmployee><AssignMeterPage /></ProtectedRoute>} />
            <Route path="customer-data" element={<ProtectedRoute requireEmployee><CustomerDataPage /></ProtectedRoute>} />
            <Route path="tables" element={<ProtectedRoute requireEmployee><TablesPage /></ProtectedRoute>} />
            <Route path="tables/:name" element={<ProtectedRoute requireEmployee><TableBrowserPage /></ProtectedRoute>} />
            <Route path="export" element={<ProtectedRoute requireEmployee><ExportPage /></ProtectedRoute>} />
            <Route path="mutations" element={<ProtectedRoute requireEmployee><MutationsPage /></ProtectedRoute>} />
            <Route path="om-report" element={<ProtectedRoute requireEmployee><OMReportPage /></ProtectedRoute>} />
            <Route path="financial" element={<ProtectedRoute requireEmployee><FinancialPage /></ProtectedRoute>} />
            <Route path="sync" element={<ProtectedRoute requireEmployee><SyncPage /></ProtectedRoute>} />
            <Route path="commission" element={<ProtectedRoute requireEmployee><CommissionCustomerPage /></ProtectedRoute>} />
            <Route path="tariffs" element={<ProtectedRoute requireEmployee><TariffManagementPage /></ProtectedRoute>} />
            {/* Customer routes */}
            <Route path="my/dashboard" element={<CustomerDashboardPage />} />
            <Route path="my/profile" element={<MyProfilePage />} />
            {/* Admin routes */}
            <Route path="admin/roles" element={<ProtectedRoute requireEmployee requireRole={['superadmin']}><AdminRolesPage /></ProtectedRoute>} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
      </CountryProvider>
    </BrowserRouter>
  );
}
