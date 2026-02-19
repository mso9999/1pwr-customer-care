/**
 * API client for the Customer Care Portal backend.
 */

const COUNTRY_ROUTES: Record<string, string> = {
  LS: '/api',
  BN: '/api/bn',
};

function getApiBase(): string {
  const cc = localStorage.getItem('cc_country') || 'LS';
  return COUNTRY_ROUTES[cc] || '/api';
}

function getToken(): string | null {
  return localStorage.getItem('cc_token');
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> || {}),
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(`${getApiBase()}${path}`, { ...options, headers });

  if (res.status === 401) {
    localStorage.removeItem('cc_token');
    localStorage.removeItem('cc_user');
    window.location.href = '/login';
    throw new Error('Unauthorized');
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }

  // Handle empty responses (204, etc.)
  if (res.status === 204 || res.headers.get('content-length') === '0') {
    return {} as T;
  }

  return res.json();
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: Record<string, unknown>;
}

export async function employeeLogin(employee_id: string, password: string): Promise<LoginResponse> {
  return request('/auth/employee-login', {
    method: 'POST',
    body: JSON.stringify({ employee_id, password }),
  });
}

export async function customerLogin(customer_id: string, password: string): Promise<LoginResponse> {
  return request('/auth/customer-login', {
    method: 'POST',
    body: JSON.stringify({ customer_id, password }),
  });
}

export async function customerRegister(customer_id: string, password: string) {
  return request('/auth/customer-register', {
    method: 'POST',
    body: JSON.stringify({ customer_id, password }),
  });
}

export async function getMe() {
  return request<Record<string, unknown>>('/auth/me');
}

// ---------------------------------------------------------------------------
// Schema
// ---------------------------------------------------------------------------

export interface TableInfo {
  name: string;
  row_count: number;
  column_count: number;
}

export interface ColumnInfo {
  name: string;
  type_name: string;
  nullable: boolean;
  size: number | null;
}

export async function listTables(): Promise<TableInfo[]> {
  return request('/schema/tables');
}

export async function listColumns(table: string): Promise<ColumnInfo[]> {
  return request(`/schema/tables/${encodeURIComponent(table)}/columns`);
}

// ---------------------------------------------------------------------------
// CRUD
// ---------------------------------------------------------------------------

export interface PaginatedResponse {
  rows: Record<string, unknown>[];
  total: number;
  page: number;
  limit: number;
  pages: number;
}

export async function listRows(
  table: string,
  params: { page?: number; limit?: number; sort?: string; order?: string; search?: string; filter_col?: string; filter_val?: string } = {},
): Promise<PaginatedResponse> {
  const qs = new URLSearchParams();
  if (params.page) qs.set('page', String(params.page));
  if (params.limit) qs.set('limit', String(params.limit));
  if (params.sort) qs.set('sort', params.sort);
  if (params.order) qs.set('order', params.order);
  if (params.search) qs.set('search', params.search);
  if (params.filter_col) qs.set('filter_col', params.filter_col);
  if (params.filter_val) qs.set('filter_val', params.filter_val);
  return request(`/tables/${encodeURIComponent(table)}?${qs}`);
}

export async function getRecord(table: string, id: string) {
  return request<{ record: Record<string, unknown>; primary_key: string }>(
    `/tables/${encodeURIComponent(table)}/${encodeURIComponent(id)}`,
  );
}

export async function createRecord(table: string, data: Record<string, unknown>) {
  return request(`/tables/${encodeURIComponent(table)}`, {
    method: 'POST',
    body: JSON.stringify({ data }),
  });
}

export async function updateRecord(table: string, id: string, data: Record<string, unknown>) {
  return request(`/tables/${encodeURIComponent(table)}/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify({ data }),
  });
}

export async function deleteRecord(table: string, id: string) {
  return request(`/tables/${encodeURIComponent(table)}/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
}

// ---------------------------------------------------------------------------
// Customer self-service
// ---------------------------------------------------------------------------

export async function getMyProfile() {
  return request<{ customer: Record<string, unknown> }>('/my/profile');
}

export interface DashboardPayment {
  amount: number;
  date: string | null;
  kwh_purchased: number;
}

export interface DashboardChartPoint {
  date: string;
  kwh: number;
}

export interface DashboardMonthPoint {
  month: string;
  kwh: number;
}

export interface MeterInfo {
  meter_id: string;
  platform: string;
  role: string;
  status: string;
}

export interface MeterComparisonPoint {
  date: string;
  [source: string]: string | number;
}

export interface HourlyPoint {
  hour: string;
  kwh?: number;
  [source: string]: string | number | undefined;
}

export interface CustomerDashboard {
  balance_kwh: number;
  last_payment: DashboardPayment | null;
  avg_kwh_per_day: number;
  estimated_recharge_seconds: number;
  total_kwh_all_time: number;
  total_lsl_all_time: number;
  daily_7d: DashboardChartPoint[];
  daily_30d: DashboardChartPoint[];
  monthly_12m: DashboardMonthPoint[];
  meters?: MeterInfo[];
  meter_comparison?: MeterComparisonPoint[];
  hourly_24h?: HourlyPoint[];
}

export async function getMyDashboard(): Promise<CustomerDashboard> {
  return request<CustomerDashboard>('/my/dashboard');
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

export function exportUrl(table: string, format: 'csv' | 'xlsx', search?: string): string {
  const qs = new URLSearchParams({ format });
  if (search) qs.set('search', search);
  const token = getToken();
  // For downloads, we'll pass the token as a query param (backend should accept it)
  if (token) qs.set('token', token);
  return `${getApiBase()}/export/${encodeURIComponent(table)}?${qs}`;
}

// ---------------------------------------------------------------------------
// Admin
// ---------------------------------------------------------------------------

export interface RoleAssignment {
  employee_id: string;
  cc_role: string;
  assigned_by: string;
  assigned_at: string;
  name?: string;
  email?: string;
}

export async function listRoles(): Promise<RoleAssignment[]> {
  return request('/admin/roles');
}

export async function assignRole(employee_id: string, cc_role: string) {
  return request('/admin/roles', {
    method: 'POST',
    body: JSON.stringify({ employee_id, cc_role }),
  });
}

export async function updateRole(employee_id: string, cc_role: string) {
  return request(`/admin/roles/${encodeURIComponent(employee_id)}`, {
    method: 'PUT',
    body: JSON.stringify({ employee_id, cc_role }),
  });
}

export async function removeRole(employee_id: string) {
  return request(`/admin/roles/${encodeURIComponent(employee_id)}`, {
    method: 'DELETE',
  });
}

// ---------------------------------------------------------------------------
// Sites & health
// ---------------------------------------------------------------------------

export async function listSites() {
  return request<{ sites: { concession: string; customer_count: number }[]; total_sites: number }>('/sites');
}

// Stats
export interface SiteStat {
  site: string;
  mwh: number;
  lsl_thousands: number;
}

export interface SiteSummary {
  sites: SiteStat[];
  totals: { mwh: number; lsl_thousands: number };
  source_table: string;
  site_count: number;
}

export async function getSiteSummary(): Promise<SiteSummary> {
  return request('/stats/site-summary');
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

export interface Mutation {
  id: number;
  timestamp: string;
  user_type: string;
  user_id: string;
  user_name: string;
  action: string;
  table_name: string;
  record_id: string;
  reverted: number;
  reverted_by: string | null;
  reverted_at: string | null;
  old_values?: Record<string, unknown> | null;
  new_values?: Record<string, unknown> | null;
}

export interface MutationListResponse {
  mutations: Mutation[];
  total: number;
  page: number;
  limit: number;
  pages: number;
}

export async function listMutations(
  params: { page?: number; limit?: number; table?: string; user?: string; action?: string } = {},
): Promise<MutationListResponse> {
  const qs = new URLSearchParams();
  if (params.page) qs.set('page', String(params.page));
  if (params.limit) qs.set('limit', String(params.limit));
  if (params.table) qs.set('table', params.table);
  if (params.user) qs.set('user', params.user);
  if (params.action) qs.set('action', params.action);
  return request(`/mutations?${qs}`);
}

export async function getMutation(id: number): Promise<Mutation> {
  return request(`/mutations/${id}`);
}

export async function revertMutation(id: number) {
  return request<{ message: string }>(`/mutations/${id}/revert`, { method: 'POST' });
}

// ---------------------------------------------------------------------------
// O&M Report
// ---------------------------------------------------------------------------

export interface OMOverview {
  total_customers: number;
  active_customers: number;
  terminated_customers: number;
  total_sites: number;
  sites: string[];
  total_mwh: number;
  total_lsl_thousands: number;
}

export interface CustomerSiteStat {
  concession: string;
  total: number;
  active: number;
  new: number;
  activation_rate: number;
}

export interface CustomerGrowthPoint {
  quarter: string;
  new_customers: number;
  cumulative: number;
}

export interface SiteConsumption {
  site: string;
  name: string;
  total_kwh: number;
  quarters: Record<string, number>;
}

export interface CumulativeTrend {
  quarter: string;
  kwh: number;
  lsl: number;
  cumulative_kwh: number;
  cumulative_lsl: number;
}

export interface AvgConsumptionTrend {
  quarter: string;
  customers: number;
  total_kwh: number;
  total_lsl: number;
  avg_daily_kwh_per_customer: number;
  avg_daily_lsl_per_customer: number;
}

export interface SiteOverviewItem {
  concession: string;
  abbreviation: string;
  district: string;
  customer_count: number;
}

export async function getOMOverview(): Promise<OMOverview> {
  return request('/om-report/overview');
}

export async function getCustomerStatsBySite(quarter?: string): Promise<{ sites: CustomerSiteStat[]; totals: Record<string, number>; quarter: string | null }> {
  const qs = quarter ? `?quarter=${encodeURIComponent(quarter)}` : '';
  return request(`/om-report/customer-stats${qs}`);
}

export async function getCustomerGrowth(): Promise<{ growth: CustomerGrowthPoint[]; total: number }> {
  return request('/om-report/customer-growth');
}

export async function getConsumptionBySite(quarter?: string): Promise<{ sites: SiteConsumption[]; total_kwh: number }> {
  const qs = quarter ? `?quarter=${encodeURIComponent(quarter)}` : '';
  return request(`/om-report/consumption-by-site${qs}`);
}

export async function getSalesBySite(quarter?: string): Promise<{ sites: SiteConsumption[]; total_lsl: number }> {
  const qs = quarter ? `?quarter=${encodeURIComponent(quarter)}` : '';
  return request(`/om-report/sales-by-site${qs}`);
}

export async function getCumulativeTrends(): Promise<{ trends: CumulativeTrend[] }> {
  return request('/om-report/cumulative-trends');
}

export async function getAvgConsumptionTrend(): Promise<{ trends: AvgConsumptionTrend[] }> {
  return request('/om-report/avg-consumption-trend');
}

export async function getSiteOverview(): Promise<{ sites: SiteOverviewItem[] }> {
  return request('/om-report/site-overview');
}

// ---------------------------------------------------------------------------
// Sync (uGridPLAN <-> ACCDB)
// ---------------------------------------------------------------------------

export interface SiteProject {
  site_code: string;
  project_id: string;
  site_name: string;
  updated_at?: string;
  last_sync?: string | null;
  synced_count?: number;
}

export interface SyncMatch {
  survey_id: string;
  customer_id: string;
  match_method: string;
  customer_type: string;
  meter_serial: string;
  gps_x: number | null;
  gps_y: number | null;
  accdb_name: string;
  accdb_phone: string;
  ugp_to_sqlite: Record<string, unknown>;
  accdb_to_ugp: Record<string, unknown>;
}

export interface DiscoverResult {
  discovered: number;
  matched: { site_code: string; project_name: string; site_name: string }[];
  matched_count: number;
  unmatched_projects: { project_name: string; project_code: string; portfolio: string }[];
  unmatched_count: number;
}

export interface SyncPreview {
  site: string;
  project_name?: string;
  ugp_connection_count: number;
  accdb_customer_count: number;
  matched: SyncMatch[];
  unmatched_ugp: Record<string, unknown>[];
  unmatched_accdb: string[];
  matched_count: number;
  unmatched_ugp_count: number;
  unmatched_accdb_count: number;
}

export interface SyncResult {
  site: string;
  matched: number;
  sqlite_written: number;
  ugp_updated: number;
  unmatched_ugp: number;
  unmatched_accdb: number;
}

export interface SyncStatus {
  sites: {
    site_code: string;
    site_name: string;
    project_id: string;
    synced_customers: number;
    with_customer_type: number;
    last_sync: string | null;
  }[];
  total_synced: number;
  type_distribution: { type: string; count: number }[];
}

export async function listSyncSites(): Promise<{ sites: SiteProject[] }> {
  return request('/sync/sites');
}

export async function discoverProjects(): Promise<DiscoverResult> {
  return request('/sync/discover', { method: 'POST' });
}

export async function addSyncSite(site_code: string, project_id: string, site_name: string) {
  return request('/sync/sites', {
    method: 'POST',
    body: JSON.stringify({ site_code, project_id, site_name }),
  });
}

export async function getSyncPreview(site: string): Promise<SyncPreview> {
  return request(`/sync/preview?site=${encodeURIComponent(site)}`);
}

export async function executeSyncSite(site: string, push_to_ugp = true, pull_to_sqlite = true): Promise<SyncResult> {
  return request('/sync/execute', {
    method: 'POST',
    body: JSON.stringify({ site, push_to_ugp, pull_to_sqlite }),
  });
}

export async function getSyncStatus(): Promise<SyncStatus> {
  return request('/sync/status');
}

// ---------------------------------------------------------------------------
// Load Curves by Customer Type
// ---------------------------------------------------------------------------

export interface LoadCurve {
  type: string;
  total_kwh: number;
  total_lsl: number;
  customer_count: number;
  avg_daily_kwh: number;
  avg_daily_kwh_per_customer: number;
}

export interface LoadCurveResponse {
  curves: LoadCurve[];
  quarterly: Record<string, unknown>[];
  customer_types?: string[];
  total_typed_customers?: number;
  note?: string;
}

export async function getLoadCurvesByType(quarter?: string): Promise<LoadCurveResponse> {
  const qs = quarter ? `?quarter=${encodeURIComponent(quarter)}` : '';
  return request(`/om-report/load-curves-by-type${qs}`);
}

export interface LoadProfile {
  type: string;
  meter_count: number;
  hourly: { hour: number; avg_kw: number; readings: number }[];
  peak_hour: number;
  peak_kw: number;
}

export interface LoadProfileResponse {
  profiles: LoadProfile[];
  chart_data: Record<string, unknown>[];
  customer_types?: string[];
  total_readings?: number;
  note?: string;
}

export async function getDailyLoadProfiles(site?: string): Promise<LoadProfileResponse> {
  const qs = site ? `?site=${encodeURIComponent(site)}` : '';
  return request(`/om-report/daily-load-profiles${qs}`);
}

// ---------------------------------------------------------------------------
// Employee: customer data lookup
// ---------------------------------------------------------------------------

export interface Transaction {
  id: number;
  account: string;
  meter: string;
  date: string | null;
  amount_lsl: number;
  rate: number;
  kwh: number;
  is_payment: boolean;
  balance?: number | null;
}

export interface TariffInfo {
  rate_lsl: number;
  source: 'global' | 'concession' | 'customer';
  source_key: string;
  effective_from: string;
}

export interface CustomerDataResponse {
  account_number: string;
  profile: Record<string, unknown>;
  meter: {
    meterid: string;
    community: string;
    customer_type: string;
    village: string;
    status: number | null;
    connect_date: string;
  } | null;
  tariff: TariffInfo | null;
  dashboard: CustomerDashboard;
  transactions: Transaction[];
  transaction_count: number;
}

export async function getCustomerData(accountNumber: string): Promise<CustomerDataResponse> {
  return request(`/customer-data/${encodeURIComponent(accountNumber)}`);
}

// ---------------------------------------------------------------------------
// Commission
// ---------------------------------------------------------------------------

export interface CommissionCustomer {
  customer_id: number;
  first_name: string;
  last_name: string;
  phone: string;
  national_id: string;
  concession: string;
  customer_type: string;
  gps_x: string;
  gps_y: string;
  date_connected: string;
}

export interface CommissionContract {
  filename: string;
  lang: string;
  site_code: string;
  url: string;
}

export interface CommissionData {
  customer: CommissionCustomer;
  meter: { meter_id: string; community: string } | null;
  account_number: string;
  existing_contracts: CommissionContract[];
}

export async function getCommissionData(customerId: string): Promise<CommissionData> {
  return request(`/commission/customer/${encodeURIComponent(customerId)}`);
}

export interface CommissionRequest {
  customer_id: number;
  account_number: string;
  site_code: string;
  customer_type: string;
  connection_date: string;
  service_phase: string;
  ampacity: string;
  national_id: string;
  phone_number: string;
  first_name?: string;
  last_name?: string;
  gps_lat?: string;
  gps_lng?: string;
  customer_signature: string;
  commissioned_by?: string;
}

export interface CommissionResult {
  status: string;
  customer_id: number;
  contract_en_url: string;
  contract_so_url: string;
  en_filename: string;
  so_filename: string;
  sms_sent: boolean;
}

export async function executeCommission(data: CommissionRequest): Promise<CommissionResult> {
  return request('/commission/execute', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function getCustomerContracts(customerId: number): Promise<{ contracts: CommissionContract[]; account_number: string }> {
  return request(`/commission/contracts/${customerId}`);
}

export interface DecommissionResult {
  status: string;
  customer_id: number;
  terminated_date: string;
  connected_date: string;
  meters: { source: string; meterid: string; accountnumber: string; community: string }[];
  accounts: { accountnumber: string; meterid: string }[];
}

export async function decommissionCustomer(customerId: number): Promise<DecommissionResult> {
  return request(`/commission/decommission/${customerId}`, { method: 'POST' });
}

// ---------------------------------------------------------------------------
// Tariff management
// ---------------------------------------------------------------------------

export interface ConcessionOverride {
  scope_key: string;
  rate_lsl: number;
  effective_from: string;
  set_by: string;
  set_by_name: string;
  set_at: string;
  notes: string;
  pending: boolean;
}

export interface CustomerOverride {
  scope_key: string;
  rate_lsl: number;
  effective_from: string;
  set_by: string;
  set_by_name: string;
  set_at: string;
  notes: string;
  pending: boolean;
}

export interface TariffCurrentResponse {
  global_rate: number;
  pending_global: { rate_lsl: number; effective_from: string; notes: string } | null;
  concession_overrides: ConcessionOverride[];
  customer_overrides: CustomerOverride[];
  customer_override_count: number;
}

export interface TariffResolveResponse {
  rate_lsl: number;
  source: string;
  source_key: string;
  effective_from: string;
  customer_id: string;
  concession: string;
  cascade: { level: string; key: string; rate_lsl: number; effective_from: string }[];
}

export interface TariffHistoryEntry {
  id: number;
  timestamp: string;
  scope: string;
  scope_key: string;
  rate_lsl: number;
  previous_rate: number | null;
  effective_from: string;
  set_by: string;
  set_by_name: string;
  notes: string;
}

export interface TariffHistoryResponse {
  history: TariffHistoryEntry[];
  total: number;
  page: number;
  limit: number;
  pages: number;
}

export async function getTariffCurrent(): Promise<TariffCurrentResponse> {
  return request('/tariff/current');
}

export async function resolveTariff(identifier: string): Promise<TariffResolveResponse> {
  return request(`/tariff/resolve/${encodeURIComponent(identifier)}`);
}

export async function updateGlobalRate(rate_lsl: number, effective_from?: string, notes?: string) {
  return request('/tariff/global', {
    method: 'PUT',
    body: JSON.stringify({ rate_lsl, effective_from, notes }),
  });
}

export async function updateConcessionRate(code: string, rate_lsl: number, effective_from?: string, notes?: string) {
  return request(`/tariff/concession/${encodeURIComponent(code)}`, {
    method: 'PUT',
    body: JSON.stringify({ rate_lsl, effective_from, notes }),
  });
}

export async function updateCustomerRate(customerId: string, rate_lsl: number, effective_from?: string, notes?: string) {
  return request(`/tariff/customer/${encodeURIComponent(customerId)}`, {
    method: 'PUT',
    body: JSON.stringify({ rate_lsl, effective_from, notes }),
  });
}

export async function deleteConcessionOverride(code: string) {
  return request(`/tariff/concession/${encodeURIComponent(code)}`, { method: 'DELETE' });
}

export async function deleteCustomerOverride(customerId: string) {
  return request(`/tariff/customer/${encodeURIComponent(customerId)}`, { method: 'DELETE' });
}

export async function getTariffHistory(params?: {
  page?: number; limit?: number; scope?: string; key?: string; from?: string; to?: string;
}): Promise<TariffHistoryResponse> {
  const sp = new URLSearchParams();
  if (params?.page) sp.set('page', String(params.page));
  if (params?.limit) sp.set('limit', String(params.limit));
  if (params?.scope) sp.set('scope', params.scope);
  if (params?.key) sp.set('key', params.key);
  if (params?.from) sp.set('from', params.from);
  if (params?.to) sp.set('to', params.to);
  const qs = sp.toString();
  return request(`/tariff/history${qs ? '?' + qs : ''}`);
}

// ---------------------------------------------------------------------------
// Customer account resolution
// ---------------------------------------------------------------------------

export interface CustomerLookupResult {
  customer_id: string;
  first_name: string;
  last_name: string;
  account_numbers: string[];
  concession: string;
  [key: string]: unknown;
}

/**
 * Look up a customer by ID and resolve all known account numbers
 * (from tblaccountnumbers, tblmeter, Copy Of tblmeter, and PLOT NUMBER).
 */
export async function getCustomerWithAccounts(customerId: string): Promise<{ customer: CustomerLookupResult }> {
  return request(`/customers/by-id/${encodeURIComponent(customerId)}`);
}

// ---------------------------------------------------------------------------
// ARPU (Average Revenue Per User)
// ---------------------------------------------------------------------------

export interface ARPUSiteDetail {
  name: string;
  revenue: number;
  customers: number;
  arpu: number;
}

export interface ARPUPoint {
  quarter: string;
  total_revenue: number;
  active_customers: number;
  arpu: number;
  per_site: Record<string, ARPUSiteDetail>;
}

export interface ARPUResponse {
  arpu: ARPUPoint[];
  site_codes: string[];
  site_names: Record<string, string>;
  source_table?: string;
  error?: string;
}

export async function getARPU(): Promise<ARPUResponse> {
  return request('/om-report/arpu');
}

// ── Monthly ARPU ──

export interface MonthlyARPUPoint {
  month: string;
  quarter: string;
  total_revenue: number;
  active_customers: number;
  arpu: number;
  per_site: Record<string, ARPUSiteDetail>;
}

export interface MonthlyARPUResponse {
  monthly_arpu: MonthlyARPUPoint[];
  site_codes: string[];
  site_names: Record<string, string>;
  source_table?: string;
  error?: string;
}

export async function getMonthlyARPU(): Promise<MonthlyARPUResponse> {
  return request('/om-report/monthly-arpu');
}

// ---------------------------------------------------------------------------
// Consumption by Tenure
// ---------------------------------------------------------------------------

export interface ConsumptionByTenureTypeStat {
  type: string;
  customer_count: number;
  total_kwh: number;
  max_tenure_months: number;
}

export interface ConsumptionByTenureResponse {
  /** Each point has tenure_month plus, for each type T: T (mean), T_upper (mean+sd), T_lower (mean-sd) */
  chart_data: Record<string, any>[];
  customer_types: string[];
  type_stats?: ConsumptionByTenureTypeStat[];
  max_tenure_months?: number;
  total_accounts_matched?: number;
  /** 'consumption' = actual meter readings (tblmonthlyconsumption), 'vended' = transaction kWh (fallback) */
  data_source?: 'consumption' | 'vended';
  segmentation?: string;
  mapping_size?: number;
  error?: string;
  debug?: Record<string, any>;
}

export async function getConsumptionByTenure(): Promise<ConsumptionByTenureResponse> {
  return request('/om-report/consumption-by-tenure');
}

// Health is at root level, not under /api
export async function getHealth() {
  const res = await fetch('/health');
  return res.json();
}
