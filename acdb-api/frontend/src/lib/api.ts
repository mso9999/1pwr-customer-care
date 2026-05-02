/**
 * API client for the Customer Care Portal backend.
 */

const COUNTRY_ROUTES: Record<string, string> = {
  LS: '/api',
  BN: '/api/bn',
  ZM: '/api/zm',
};

function getApiBase(): string {
  const cc = localStorage.getItem('cc_country') || 'LS';
  return COUNTRY_ROUTES[cc] || '/api';
}

function getToken(): string | null {
  return localStorage.getItem('cc_token');
}

/** FastAPI often returns `detail` as a string, or an array of { loc, msg, type } validation errors. */
function formatApiErrorDetail(detail: unknown): string {
  if (detail == null) return '';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item;
        if (item && typeof item === 'object' && 'msg' in item) {
          const loc = Array.isArray((item as { loc?: unknown }).loc)
            ? (item as { loc: string[] }).loc.filter((x) => x !== 'body').join('.')
            : '';
          const msg = String((item as { msg: string }).msg);
          return loc ? `${loc}: ${msg}` : msg;
        }
        try {
          return JSON.stringify(item);
        } catch {
          return String(item);
        }
      })
      .filter(Boolean)
      .join('; ');
  }
  if (typeof detail === 'object') {
    try {
      return JSON.stringify(detail);
    } catch {
      return String(detail);
    }
  }
  return String(detail);
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
    const msg = formatApiErrorDetail(body.detail) || res.statusText || `HTTP ${res.status}`;
    throw new Error(msg);
  }

  // Handle empty responses (204, etc.)
  if (res.status === 204 || res.headers.get('content-length') === '0') {
    return {} as T;
  }

  return res.json();
}

async function downloadFile(path: string, fallbackFilename: string, options: RequestInit = {}): Promise<void> {
  const token = getToken();
  const headers: Record<string, string> = {
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
    const msg = formatApiErrorDetail(body.detail) || res.statusText || `HTTP ${res.status}`;
    throw new Error(msg);
  }

  const blob = await res.blob();
  const disposition = res.headers.get('content-disposition') || '';
  const match = disposition.match(/filename="?([^"]+)"?/i);
  const filename = match?.[1] || fallbackFilename;

  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
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
  params: {
    page?: number;
    limit?: number;
    sort?: string;
    order?: string;
    search?: string;
    filter_col?: string;
    filter_val?: string;
    filter_country?: string;
  } = {},
): Promise<PaginatedResponse> {
  const qs = new URLSearchParams();
  if (params.page) qs.set('page', String(params.page));
  if (params.limit) qs.set('limit', String(params.limit));
  if (params.sort) qs.set('sort', params.sort);
  if (params.order) qs.set('order', params.order);
  if (params.search) qs.set('search', params.search);
  if (params.filter_col) qs.set('filter_col', params.filter_col);
  if (params.filter_val) qs.set('filter_val', params.filter_val);
  if (params.filter_country) qs.set('filter_country', params.filter_country);
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

export async function listColdStorage(
  table: string,
  params?: { page?: number; limit?: number },
): Promise<PaginatedResponse> {
  const q = new URLSearchParams();
  if (params?.page) q.set('page', String(params.page));
  if (params?.limit) q.set('limit', String(params.limit));
  const qs = q.toString();
  return request(`/tables/${encodeURIComponent(table)}/cold-storage${qs ? `?${qs}` : ''}`);
}

export async function restoreRecord(table: string, id: string) {
  return request(`/tables/${encodeURIComponent(table)}/${encodeURIComponent(id)}/restore`, {
    method: 'POST',
  });
}

// ---------------------------------------------------------------------------
// Meter lifecycle
// ---------------------------------------------------------------------------

export interface MeterAssignment {
  id: number;
  meter_id: string;
  account_number: string;
  community: string | null;
  assigned_at: string;
  removed_at: string | null;
  removal_reason: string | null;
  replaced_by: string | null;
  notes: string | null;
  current_status?: string;
  platform?: string;
}

export async function decommissionMeter(
  meterId: string,
  body: { reason: string; replacement_meter_id?: string; notes?: string },
) {
  return request(`/meters/${encodeURIComponent(meterId)}/decommission`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export interface AssignMeterRequest {
  customer_identifier: string;
  meter_id: string;
  community: string;
  customer_type: string;
  account_number: string;
  connection_date: string;
  village_name?: string;
  latitude?: string;
  longitude?: string;
}

export interface AssignMeterResult {
  message: string;
  meter_id: string;
  account_number: string;
  customer_id_legacy: number | null;
}

export async function assignMeter(data: AssignMeterRequest): Promise<AssignMeterResult> {
  return request('/meters/assign', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function getMeterHistory(meterId: string): Promise<{ meter_id: string; assignments: MeterAssignment[] }> {
  return request(`/meters/${encodeURIComponent(meterId)}/history`);
}

export async function getAccountMeterHistory(
  accountNumber: string,
): Promise<{ account_number: string; meters: MeterAssignment[]; current_meter: string | null }> {
  return request(`/meters/account/${encodeURIComponent(accountNumber)}/history`);
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
  balance_currency?: number;
  currency_code?: string;
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

export async function downloadCustomersExport(params: {
  format?: 'csv' | 'xlsx'; site?: string; country?: string; search?: string;
} = {}): Promise<void> {
  const qs = new URLSearchParams();
  qs.set('format', params.format || 'csv');
  if (params.site) qs.set('site', params.site);
  else if (params.country) qs.set('country', params.country);
  if (params.search) qs.set('search', params.search);
  const name = params.site
    ? `customers_${params.site}`
    : params.country
      ? `customers_${params.country}`
      : 'customers';
  return downloadFile(`/export/customers-with-accounts?${qs}`, `${name}.${params.format || 'csv'}`);
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

export interface DepartmentMapping {
  department_key: string;
  cc_role: string;
  label: string;
  added_by: string;
  added_at: string;
}

export interface PRDepartment {
  id: string;
  name: string;
  code: string;
  org: string;
  org_name: string;
  active: boolean;
}

export async function listDepartmentMappings(): Promise<DepartmentMapping[]> {
  return request('/admin/department-mappings');
}

export async function addDepartmentMapping(department_key: string, cc_role: string, label: string) {
  return request('/admin/department-mappings', {
    method: 'POST',
    body: JSON.stringify({ department_key, cc_role, label }),
  });
}

export async function removeDepartmentMapping(department_key: string) {
  return request(`/admin/department-mappings/${encodeURIComponent(department_key)}`, {
    method: 'DELETE',
  });
}

export async function listPRDepartments(): Promise<PRDepartment[]> {
  return request('/admin/pr-departments');
}

// ---------------------------------------------------------------------------
// Monthly staff-PIN broadcast (manual trigger from Admin Roles page)
// ---------------------------------------------------------------------------

export interface PinPreview {
  year: number;
  month: number;
  active_countries: string[];
  message: string;
}

export interface PinBroadcastResult {
  country_code: string;
  year: number;
  month: number;
  month_label: string;
  pin_prefix: string;
  ok: boolean;
}

export async function previewMonthlyPin(): Promise<PinPreview> {
  return request('/admin/auth/pin-preview');
}

export async function broadcastMonthlyPin(
  body: { countries?: string[]; include_next_month?: boolean } = {},
): Promise<{ results: PinBroadcastResult[] }> {
  return request('/admin/auth/broadcast-pin', {
    method: 'POST',
    body: JSON.stringify({ include_next_month: true, ...body }),
  });
}

// ---------------------------------------------------------------------------
// Coverage audit (admin)
// ---------------------------------------------------------------------------

export interface CoverageMonthCell {
  rows: number;
  meters: number;
}

export interface CoverageDeficit {
  site: string;
  month: string;
  rows: number;
  baseline_median: number;
  ratio: number;
  missing_pct: number;
  in_progress?: boolean;
  expected_so_far?: number;
  elapsed_fraction?: number;
}

export interface CoverageZeroMeter {
  community: string;
  meter_id: string;
  account_number: string;
  role: string;
  customer_connect_date: string | null;
}

export interface CoverageStaleMeter {
  community: string;
  meter_id: string;
  account_number: string;
  last_reading: string | null;
  stale_days: number | null;
}

export interface CoverageLastIngest {
  last_reading: string | null;
  last_insert: string | null;
  rows_total: number;
}

export interface CoverageCrossCountry {
  community: string;
  meters: number;
  accounts: number;
  this_db_country: string;
}

export interface CoverageAuditPayload {
  country: string;
  database_label: string;
  generated_at: string;
  window_months: number;
  stale_days: number;
  deficit_threshold: number;
  active_counts: Record<string, number>;
  monthly_coverage: Record<string, Record<string, CoverageMonthCell>>;
  monthly_deficits: CoverageDeficit[];
  zero_coverage_meters: CoverageZeroMeter[];
  zero_coverage_summary: Record<string, { active_meters: number; zero_coverage_meters: number; zero_coverage_pct: number | null }>;
  stale_meters: CoverageStaleMeter[];
  last_ingest: Record<string, Record<string, CoverageLastIngest>>;
  cross_country_meters: CoverageCrossCountry[];
  declared_sites_missing_data: string[];
  orphan_sites: string[];
  totals: {
    active_meters: number;
    zero_coverage_meters: number;
    stale_meters: number;
    monthly_deficits_flagged: number;
    sites_with_active_meters: number;
    sites_with_data: number;
  };
  upstream_freshness?: Record<string, unknown> | null;
  upstream_checked_at?: string | null;
}

export interface CoverageSnapshotSummary {
  id: number;
  snapshot_at: string;
  country_code: string;
  active_meters: number;
  zero_coverage_meters: number;
  stale_meters: number;
  monthly_deficits_flagged: number;
  sites_with_active_meters: number;
  sites_with_data: number;
  triggered_by: string | null;
  notes: string | null;
  upstream_checked_at: string | null;
}

export interface CoverageTrendPoint {
  snapshot_at: string;
  active_meters: number;
  zero_coverage_meters: number;
  stale_meters: number;
  monthly_deficits_flagged: number;
}

export async function liveCoverageAudit(params: {
  country?: string;
  window_months?: number;
  stale_days?: number;
  deficit_threshold?: number;
} = {}): Promise<CoverageAuditPayload> {
  const qs = new URLSearchParams();
  if (params.country) qs.set('country', params.country);
  if (params.window_months) qs.set('window_months', String(params.window_months));
  if (params.stale_days) qs.set('stale_days', String(params.stale_days));
  if (params.deficit_threshold !== undefined) qs.set('deficit_threshold', String(params.deficit_threshold));
  const q = qs.toString();
  return request(`/admin/coverage/audit${q ? `?${q}` : ''}`);
}

export async function takeCoverageSnapshot(body: {
  country: string;
  window_months?: number;
  stale_days?: number;
  deficit_threshold?: number;
  notes?: string;
  include_upstream?: boolean;
}): Promise<{ snapshot_id: number; totals: CoverageAuditPayload['totals'] }> {
  return request('/admin/coverage/snapshot', {
    method: 'POST',
    body: JSON.stringify({
      country: body.country,
      window_months: body.window_months ?? 8,
      stale_days: body.stale_days ?? 30,
      deficit_threshold: body.deficit_threshold ?? 0.5,
      notes: body.notes,
      include_upstream: body.include_upstream ?? false,
    }),
  });
}

export async function listCoverageSnapshots(
  country?: string,
  limit = 30,
): Promise<CoverageSnapshotSummary[]> {
  const qs = new URLSearchParams();
  if (country) qs.set('country', country);
  qs.set('limit', String(limit));
  return request(`/admin/coverage/snapshots?${qs.toString()}`);
}

export async function getCoverageSnapshot(id: number): Promise<CoverageAuditPayload & {
  id: number;
  snapshot_at: string;
  triggered_by: string | null;
  notes: string | null;
}> {
  return request(`/admin/coverage/snapshots/${id}`);
}

export async function coverageTrend(
  country = 'LS',
  days = 60,
): Promise<{ country: string; days: number; points: CoverageTrendPoint[] }> {
  return request(`/admin/coverage/trend?country=${encodeURIComponent(country)}&days=${days}`);
}

export async function coverageUpstreamFreshness(
  country = 'LS',
  refresh = false,
): Promise<Record<string, unknown>> {
  const qs = new URLSearchParams();
  qs.set('country', country);
  if (refresh) qs.set('refresh', 'true');
  return request(`/admin/coverage/upstream-freshness?${qs.toString()}`);
}

// ---------------------------------------------------------------------------
// Programs (funder monitoring -- e.g. UEF/ZEDSI Odyssey API)
// ---------------------------------------------------------------------------

export interface Program {
  id: number;
  code: string;
  name: string;
  funder: string | null;
  country_code: string | null;
  description: string | null;
  active: boolean;
  created_at: string;
  member_count: number;
  active_token_count: number;
}

export interface ProgramMembership {
  account_number: string;
  customer_id_legacy: string | null;
  customer_name: string | null;
  site_id: string | null;
  joined_at: string;
  claim_milestone: string | null;
  notes: string | null;
  added_by: string | null;
}

export interface ProgramTokenSummary {
  id: number;
  label: string;
  token_prefix: string;
  issued_at: string;
  issued_by: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
  last_used_ip: string | null;
}

export interface BulkMembershipResult {
  action: 'add' | 'remove';
  requested_count: number;
  affected_count: number;
  skipped_unknown: string[];
}

export interface ProgramTokenIssued {
  token: string;
  summary: ProgramTokenSummary;
}

export async function listPrograms(): Promise<Program[]> {
  return request('/admin/programs');
}

export async function createProgram(body: {
  code: string;
  name: string;
  funder?: string;
  country_code?: string;
  description?: string;
  active?: boolean;
}): Promise<Program> {
  return request('/admin/programs', { method: 'POST', body: JSON.stringify(body) });
}

export async function updateProgram(code: string, body: Partial<{
  name: string;
  funder: string;
  country_code: string;
  description: string;
  active: boolean;
}>): Promise<Program> {
  return request(`/admin/programs/${encodeURIComponent(code)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

export async function bulkProgramMembership(
  code: string,
  body: {
    action: 'add' | 'remove';
    country_codes?: string[];
    site_codes?: string[];
    account_numbers?: string[];
    claim_milestone?: string;
    notes?: string;
  },
): Promise<BulkMembershipResult> {
  return request(`/admin/programs/${encodeURIComponent(code)}/memberships/bulk`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function listProgramMemberships(
  code: string,
  params: { site?: string; search?: string; page?: number; page_size?: number } = {},
): Promise<{ program: string; total: number; page: number; page_size: number; items: ProgramMembership[] }> {
  const qs = new URLSearchParams();
  if (params.site) qs.set('site', params.site);
  if (params.search) qs.set('search', params.search);
  if (params.page) qs.set('page', String(params.page));
  if (params.page_size) qs.set('page_size', String(params.page_size));
  const q = qs.toString();
  return request(`/admin/programs/${encodeURIComponent(code)}/memberships${q ? `?${q}` : ''}`);
}

export async function listProgramTokens(
  code: string,
  includeRevoked = false,
): Promise<ProgramTokenSummary[]> {
  const qs = includeRevoked ? '?include_revoked=true' : '';
  return request(`/admin/programs/${encodeURIComponent(code)}/tokens${qs}`);
}

export async function issueProgramToken(
  code: string,
  body: { label: string; lifetime_days?: number | null },
): Promise<ProgramTokenIssued> {
  return request(`/admin/programs/${encodeURIComponent(code)}/tokens`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function revokeProgramToken(code: string, tokenId: number): Promise<{ revoked: boolean; token_id: number }> {
  return request(`/admin/programs/${encodeURIComponent(code)}/tokens/${tokenId}`, {
    method: 'DELETE',
  });
}

export async function previewProgramDataset(
  code: string,
  params: { dataset: 'electricity-payment' | 'meter-metrics'; from: string; to: string; page?: number; page_size?: number },
): Promise<{ dataset: string; total: number; count: number; data: Record<string, unknown>[] }> {
  const qs = new URLSearchParams();
  qs.set('dataset', params.dataset);
  qs.set('from', params.from);
  qs.set('to', params.to);
  if (params.page) qs.set('page', String(params.page));
  if (params.page_size) qs.set('page_size', String(params.page_size));
  return request(`/admin/programs/${encodeURIComponent(code)}/preview?${qs.toString()}`);
}

export async function downloadProgramConnections(code: string, milestone?: string): Promise<void> {
  const qs = milestone ? `?milestone=${encodeURIComponent(milestone)}` : '';
  return downloadFile(
    `/admin/programs/${encodeURIComponent(code)}/connections.xlsx${qs}`,
    `${code}_connections${milestone ? `_${milestone.replace(/\s+/g, '_')}` : ''}.xlsx`,
  );
}

// ---------------------------------------------------------------------------
// Sites & health
// ---------------------------------------------------------------------------

export async function listSites() {
  return request<{ sites: { concession: string; customer_count: number }[]; total_sites: number }>('/sites');
}

export interface NextAccountPreview {
  community: string;
  next_account_number: string;
}

export async function previewNextAccount(community: string): Promise<NextAccountPreview> {
  return request(`/customers/next-account?community=${encodeURIComponent(community)}`);
}

export interface CustomerRegistrationRequest {
  first_name: string;
  middle_name?: string;
  gender?: string;
  last_name: string;
  community: string;
  phone?: string;
  cell_phone_1?: string;
  cell_phone_2?: string;
  email?: string;
  national_id?: string;
  plot_number?: string;
  street_address?: string;
  city?: string;
  district?: string;
  customer_type?: string;
  gps_lat?: string;
  gps_lon?: string;
  date_service_connected?: string;
  meter_id?: string;
}

export interface CustomerRegistrationResult {
  account_number: string;
  customer_id: number;
  customer_id_legacy: number;
  first_name: string;
  last_name: string;
  community: string;
}

export async function registerCustomerRecord(data: CustomerRegistrationRequest): Promise<CustomerRegistrationResult> {
  return request('/customers/register', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export interface UGPConnection {
  survey_id: string;
  customer_type: string;
  customer_code: string;
  meter_serial: string;
  gps_lat: number | null;
  gps_lon: number | null;
  status: string;
  bound_account: string | null;
  split_parent?: string;
}

export async function listUGPConnections(site: string) {
  return request<{ site: string; count: number; connections: UGPConnection[] }>(
    `/sync/connections?site=${encodeURIComponent(site)}`,
  );
}

export interface SplitConnectionRequest {
  site: string;
  parent_survey_id: string;
  account_number: string;
}

export async function splitConnection(data: SplitConnectionRequest) {
  return request<UGPConnection>('/sync/split-connection', {
    method: 'POST',
    body: JSON.stringify(data),
  });
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

export interface CustomerRecordCompletenessRow {
  customer_type: string;
  customer_count: number;
  customers_with_account: number;
  commissioned_customers: number;
  account_count: number;
  commissioned_accounts: number;
  accounts_with_records: number;
  actual_records: number;
  expected_records: number;
  completeness_pct: number | null;
  first_record_at: string | null;
  last_record_at: string | null;
}

export interface CustomerRecordCompletenessResponse {
  rows: CustomerRecordCompletenessRow[];
  totals: {
    customer_count: number;
    customers_with_account: number;
    commissioned_customers: number;
    account_count: number;
    commissioned_accounts: number;
    accounts_with_records: number;
    actual_records: number;
    expected_records: number;
    completeness_pct: number | null;
  };
  data_as_of: string | null;
  record_source: string;
  note?: string;
}

export async function getCustomerRecordCompleteness(): Promise<CustomerRecordCompletenessResponse> {
  return request('/stats/customer-record-completeness');
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
// Sync (uGridPLAN <-> CC)
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
  account_number: string;
  match_method: string;
  customer_type: string;
  meter_serial: string;
  gps_x: number | null;
  gps_y: number | null;
  cc_name: string;
  cc_phone: string;
  ugp_to_cache: Record<string, unknown>;
  cc_to_ugp: Record<string, unknown>;
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
  cc_customer_count: number;
  matched: SyncMatch[];
  unmatched_ugp: Record<string, unknown>[];
  unmatched_cc: Record<string, unknown>[];
  matched_count: number;
  unmatched_ugp_count: number;
  unmatched_cc_count: number;
}

export interface SyncResult {
  site: string;
  matched: number;
  cache_written: number;
  ugp_updated: number;
  unmatched_ugp: number;
  unmatched_cc: number;
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

export async function executeSyncSite(site: string, push_to_ugp = true, pull_to_cache = true): Promise<SyncResult> {
  return request('/sync/execute', {
    method: 'POST',
    body: JSON.stringify({ site, push_to_ugp, pull_to_cache }),
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

export async function getDailyLoadProfiles(site?: string, customerType?: string): Promise<LoadProfileResponse> {
  const params = new URLSearchParams();
  if (site) params.set('site', site);
  if (customerType) params.set('customer_type', customerType);
  const qs = params.toString() ? `?${params}` : '';
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
  /** M-Pesa / provider receipt when recorded via portal or gateway */
  payment_reference?: string | null;
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
  customer_id_legacy: number;
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
  customer_id?: number;
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
  survey_id?: string;
  customer_signature: string;
  commissioned_by?: string;
}

export interface UpstreamWarning {
  node_1: string;
  node_2: string;
  type: string;
  status_field: string;
  status_value: number;
  status_raw: string;
  cable_size: string;
  length: number;
  subnet: string;
}

export interface UgpSyncResult {
  updated: boolean;
  survey_id: string;
  upstream_warnings: UpstreamWarning[];
  error: string | null;
}

export interface CommissionResult {
  status: string;
  customer_id: number;
  account_number: string;
  contract_en_url: string;
  contract_so_url: string;
  en_filename: string;
  so_filename: string;
  sms_sent: boolean;
  ugp_sync?: UgpSyncResult;
}

export async function executeCommission(data: CommissionRequest): Promise<CommissionResult> {
  return request('/commission/execute', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function getCustomerContracts(customerId: number | string): Promise<{ contracts: CommissionContract[]; account_number: string }> {
  return request(`/commission/contracts/${encodeURIComponent(customerId)}`);
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

export interface EnergizeUpstreamResult {
  updated: number;
  failed: number;
  errors: string[];
}

export async function energizeUpstream(siteCode: string, lines: UpstreamWarning[]): Promise<EnergizeUpstreamResult> {
  return request('/commission/energize-upstream', {
    method: 'POST',
    body: JSON.stringify({ site_code: siteCode, lines }),
  });
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
  customer_id_legacy: string;
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

// ---------------------------------------------------------------------------
// Check Meter Comparison
// ---------------------------------------------------------------------------

export interface CheckMeterPairStats {
  total_deviation_pct: number;
  mean_deviation_pct: number;
  stddev_deviation_pct: number;
  mean_sm_kwh: number;
  mean_1m_kwh: number;
  n_matched_hours: number;
  total_sm_kwh: number;
  total_1m_kwh: number;
}

export interface CheckMeterHealth {
  meter_id: string;
  last_seen_utc: string | null;
  hours_since_report: number | null;
  /** Reported by device via IoT → /api/meters/reading once firmware publishes it */
  firmware_version?: string | null;
  status: 'online' | 'stale' | 'offline' | 'unknown';
}

/** Phase 1 diagnostic: parallel "what-if" balance under the opposite billing
 *  primacy. See docs/ops/1meter-billing-migration-protocol.md. */
export interface CheckMeterWhatIf {
  actual_priority: 'sm' | '1m';
  actual_balance_kwh: number;
  what_if_priority: 'sm' | '1m';
  what_if_balance_kwh: number;
  implied_balance_delta_kwh: number;
}

export interface CheckMeterPair {
  account: string;
  check_meter_id: string;
  primary_meter_id: string;
  stats: CheckMeterPairStats;
  health: CheckMeterHealth;
  /** Present when the balance engine could compute the alternate primacy. */
  balance_what_if?: CheckMeterWhatIf | null;
}

export interface CheckMeterComparisonResponse {
  pairs: CheckMeterPair[];
  time_series: Record<string, any>[];
  days: number;
  cutoff: string;
  note?: string;
}

export async function getCheckMeterComparison(days = 7): Promise<CheckMeterComparisonResponse> {
  return request(`/om-report/check-meter-comparison?days=${days}`);
}

export async function downloadCheckMeterComparisonExcel(days = 7): Promise<void> {
  const suffix = days === 0 ? 'since_firmware_update' : `last_${days}_days`;
  return downloadFile(
    `/om-report/check-meter-comparison/export?days=${days}`,
    `check_meter_comparison_${suffix}.xlsx`,
  );
}

// ---------------------------------------------------------------------------
// Financing
// ---------------------------------------------------------------------------

export interface FinancingProduct {
  id: number;
  name: string;
  default_principal: number;
  default_interest_rate: number;
  default_setup_fee: number;
  default_repayment_fraction: number;
  default_penalty_rate: number;
  default_penalty_grace_days: number;
  default_penalty_interval_days: number;
  is_active: boolean;
}

export interface FinancingAgreement {
  id: number;
  customer_id: number | null;
  account_number: string;
  product_id: number | null;
  product_name: string | null;
  description: string;
  principal: number;
  interest_amount: number;
  setup_fee: number;
  total_owed: number;
  outstanding_balance: number;
  repayment_fraction: number;
  penalty_rate: number;
  penalty_grace_days: number;
  penalty_interval_days: number;
  contract_path: string | null;
  status: string;
  created_at: string;
  created_by: string | null;
  paid_off_at: string | null;
  ledger?: FinancingLedgerEntry[];
}

export interface FinancingLedgerEntry {
  id: number;
  agreement_id: number;
  entry_type: string;
  amount: number;
  balance_after: number;
  source_transaction_id: number | null;
  note: string | null;
  created_at: string;
  created_by: string | null;
}

export interface CustomerFinancingSummary {
  account_number: string;
  total_outstanding: number;
  active_agreements: number;
  agreements: FinancingAgreement[];
}

export async function getFinancingProducts(): Promise<FinancingProduct[]> {
  return request('/financing/products');
}

export async function createFinancingProduct(data: Partial<FinancingProduct>): Promise<{ id: number }> {
  return request('/financing/products', { method: 'POST', body: JSON.stringify(data) });
}

export async function updateFinancingProduct(id: number, data: Partial<FinancingProduct>): Promise<void> {
  return request(`/financing/products/${id}`, { method: 'PUT', body: JSON.stringify(data) });
}

export async function getFinancingAgreements(params?: { status?: string; account_number?: string }): Promise<FinancingAgreement[]> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set('status', params.status);
  if (params?.account_number) qs.set('account_number', params.account_number);
  const q = qs.toString();
  return request(`/financing/agreements${q ? '?' + q : ''}`);
}

export async function createFinancingAgreement(data: {
  account_number: string;
  product_id?: number;
  description: string;
  principal: number;
  interest_amount?: number;
  setup_fee?: number;
  total_owed?: number;
  repayment_fraction?: number;
  penalty_rate?: number;
  penalty_grace_days?: number;
  penalty_interval_days?: number;
  customer_signature_b64?: string;
}): Promise<{ id: number; contracts?: Record<string, string> }> {
  return request('/financing/agreements', { method: 'POST', body: JSON.stringify(data) });
}

export async function getFinancingAgreement(id: number): Promise<FinancingAgreement> {
  return request(`/financing/agreements/${id}`);
}

export async function adjustFinancingAgreement(id: number, data: { entry_type: string; amount: number; note?: string }): Promise<{ outstanding_balance: number; status: string }> {
  return request(`/financing/agreements/${id}/adjust`, { method: 'POST', body: JSON.stringify(data) });
}

export async function getCustomerFinancing(account_number: string): Promise<CustomerFinancingSummary> {
  return request(`/financing/customer/${account_number}`);
}

// ---------------------------------------------------------------------------
// Payment Verification
// ---------------------------------------------------------------------------

export interface PaymentVerification {
  id: number;
  transaction_id: number | null;
  account_number: string;
  payment_type: string;
  amount: number;
  status: string;
  verified_by: string | null;
  verified_at: string | null;
  note: string | null;
  created_at: string;
  first_name?: string;
  last_name?: string;
}

export async function getPendingVerifications(params?: { status?: string; payment_type?: string }): Promise<{ verifications: PaymentVerification[]; total: number }> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set('status', params.status);
  if (params?.payment_type) qs.set('payment_type', params.payment_type);
  const q = qs.toString();
  return request(`/payment-verification/pending${q ? '?' + q : ''}`);
}

export async function verifyPayments(ids: number[], action: 'verify' | 'reject', note?: string): Promise<{ updated: number }> {
  return request('/payment-verification/verify', {
    method: 'POST',
    body: JSON.stringify({ ids, action, note }),
  });
}

export function verificationExportUrl(status: string, paymentType?: string): string {
  const qs = new URLSearchParams({ status });
  if (paymentType) qs.set('payment_type', paymentType);
  const token = getToken();
  if (token) qs.set('token', token);
  return `${getApiBase()}/payment-verification/export?${qs}`;
}

// ---------------------------------------------------------------------------
// Onboarding Pipeline
// ---------------------------------------------------------------------------

export interface PipelineStage {
  stage: string;
  count: number;
}

export interface PipelineResponse {
  funnel: PipelineStage[];
  sites: string[];
}

export async function getOnboardingPipeline(site?: string): Promise<PipelineResponse> {
  const qs = site ? `?site=${encodeURIComponent(site)}` : '';
  return request(`/om-report/pipeline${qs}`);
}

// ---------------------------------------------------------------------------
// Record Manual Payment
// ---------------------------------------------------------------------------

export interface RecordPaymentResult {
  status: string;
  transaction_id: number;
  amount: number;
  kwh: number;
  balance_kwh: number;
  sm_credit: Record<string, any> | null;
  financing?: {
    debt_portion: number;
    electricity_portion: number;
    is_dedicated_payment: boolean;
  };
}

export async function recordManualPayment(data: {
  account_number: string;
  amount: number;
  meter_id?: string;
  note?: string;
  payment_reference: string;
}): Promise<RecordPaymentResult> {
  return request('/payments/record', { method: 'POST', body: JSON.stringify(data) });
}

// ── Cross-country Revenue Summary ──

export interface RevenueCountryMonth {
  month: string;
  revenue_local: number;
  paying_customers: number;
  currency: string;
  country: string;
  arpu_local: number;
}

export interface RevenueCountry {
  country: string;
  country_name: string;
  currency: string;
  fx_to_usd: number;
  active_connections: number;
  months: RevenueCountryMonth[];
}

export interface RevenueConsolidatedMonth {
  month: string;
  revenue_usd: number;
  total_paying_customers: number;
  arpu_usd: number;
  arpu_usd_prorated: number;
  month_fraction: number;
  per_country: Record<string, {
    revenue_local: number;
    paying_customers: number;
    currency: string;
    revenue_usd: number;
  }>;
}

export interface RevenueSummaryResponse {
  countries: RevenueCountry[];
  consolidated: RevenueConsolidatedMonth[];
  fx_rates: Record<string, number>;
  fx_note: string;
  window_months: number;
}

export async function getRevenueSummary(months = 12): Promise<RevenueSummaryResponse> {
  return request(`/stats/revenue-summary?months=${months}`);
}

// ---------------------------------------------------------------------------
// Tickets / Maintenance Log
// ---------------------------------------------------------------------------

export interface Ticket {
  id: number;
  ugp_ticket_id: string;
  source: string;
  phone: string | null;
  customer_id: number | null;
  account_number: string | null;
  site_code: string | null;
  fault_description: string | null;
  category: string | null;
  priority: string | null;
  reported_by: string | null;
  created_at: string;
  ticket_name: string | null;
  failure_time: string | null;
  services_affected: string | null;
  troubleshooting_steps: string | null;
  cause_of_fault: string | null;
  precautions: string | null;
  restoration_time: string | null;
  resolution_approach: string | null;
  duration: string | null;
  status: string;
  updated_at: string | null;
  resolved_by: string | null;
}

export interface TicketsResponse {
  tickets: Ticket[];
  total: number;
  count: number;
}

export async function listTickets(params: {
  limit?: number; offset?: number; site_code?: string;
  status?: string; search?: string;
} = {}): Promise<TicketsResponse> {
  const qs = new URLSearchParams();
  if (params.limit) qs.set('limit', String(params.limit));
  if (params.offset) qs.set('offset', String(params.offset));
  if (params.site_code) qs.set('site_code', params.site_code);
  if (params.status) qs.set('status', params.status);
  if (params.search) qs.set('search', params.search);
  return request(`/tickets?${qs}`);
}

export async function getTicket(id: number | string): Promise<Ticket> {
  return request(`/tickets/${encodeURIComponent(id)}`);
}

export async function createTicket(data: Partial<Ticket>): Promise<{ status: string; id: number }> {
  return request('/tickets', { method: 'POST', body: JSON.stringify(data) });
}

export async function updateTicket(id: number, data: Partial<Ticket>): Promise<{ status: string; id: number }> {
  return request(`/tickets/${id}`, { method: 'PATCH', body: JSON.stringify(data) });
}

export async function downloadTicketsExcel(params: {
  site_code?: string; status?: string; quarter?: string;
} = {}): Promise<void> {
  const qs = new URLSearchParams();
  if (params.site_code) qs.set('site_code', params.site_code);
  if (params.status) qs.set('status', params.status);
  if (params.quarter) qs.set('quarter', params.quarter);
  const q = qs.toString();
  return downloadFile(`/tickets/export${q ? `?${q}` : ''}`, 'Maintenance_Log.xlsx');
}

// ---------------------------------------------------------------------------
// Gensite — generation-site commissioning + inverter telemetry
// ---------------------------------------------------------------------------

export interface GensiteCredentialSpec {
  vendor: string;
  backend: string;
  label: string;
  plain_fields: string[];
  secret_fields: string[];
  extra_fields: string[];
  docs_url: string | null;
  notes: string | null;
}

export interface GensiteVendor {
  vendor: string;
  display_name: string;
  implementation_status: 'ready' | 'stub' | 'scrape' | 'modbus';
  credential_specs: GensiteCredentialSpec[];
}

export interface GensiteVendorsResponse {
  vendors: GensiteVendor[];
  crypto_configured: boolean;
}

export interface GensiteSite {
  code: string;
  country: string;
  kind: string;
  display_name: string;
  district: string | null;
  gps_lat: number | null;
  gps_lon: number | null;
  ugp_project_id: string | null;
  commissioned_at: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
  last_reading_ts?: string | null;
}

export interface GensiteEquipment {
  id: number;
  site_code: string;
  kind: string;
  vendor: string;
  model: string | null;
  serial: string | null;
  role: string | null;
  nameplate_kw: number | null;
  nameplate_kwh: number | null;
  firmware_version: string | null;
  commissioned_at: string | null;
  decommissioned_at: string | null;
  installed_by: string | null;
  notes: string | null;
}

export interface GensiteCredentialMasked {
  id: number;
  site_code: string;
  vendor: string;
  backend: string;
  base_url: string | null;
  username: string | null;
  username_masked?: string;
  site_id_on_vendor: string | null;
  extra: Record<string, unknown>;
  created_by: string | null;
  created_at: string;
  rotated_at: string | null;
  last_verified_at: string | null;
  last_verified_ok: boolean | null;
  last_verify_error: string | null;
  has_secret: boolean;
  has_api_key: boolean;
}

export interface GensiteLiveReading {
  equipment_id: number;
  ts_utc: string;
  ac_kw: number | null;
  ac_kwh_total: number | null;
  pv_kw: number | null;
  battery_kw: number | null;
  battery_soc_pct: number | null;
  grid_kw: number | null;
  ac_freq_hz: number | null;
  ac_v_avg: number | null;
  status_code: string | null;
  vendor: string;
  kind: string;
  model: string | null;
  serial: string | null;
  role: string | null;
}

export interface GensiteSiteDetail {
  site: GensiteSite;
  equipment: GensiteEquipment[];
  credentials: GensiteCredentialMasked[];
  latest_readings: GensiteLiveReading[];
}

export interface GensiteEquipmentInput {
  kind: string;
  vendor: string;
  model?: string;
  serial?: string;
  role?: string;
  nameplate_kw?: number;
  nameplate_kwh?: number;
  firmware_version?: string;
  notes?: string;
}

export interface GensiteCredentialInput {
  vendor: string;
  backend: string;
  base_url?: string;
  username?: string;
  secret?: string;
  api_key?: string;
  site_id_on_vendor?: string;
  extra?: Record<string, unknown>;
}

export interface GensiteCommissionRequest {
  site_code: string;
  country: string;
  kind: string;
  display_name: string;
  district?: string;
  gps_lat?: number;
  gps_lon?: number;
  ugp_project_id?: string;
  commissioned_at?: string;
  notes?: string;
  equipment: GensiteEquipmentInput[];
  credentials: GensiteCredentialInput[];
}

export interface GensiteCommissionResponse {
  site: GensiteSite;
  equipment: GensiteEquipment[];
  credentials: Array<{
    credential: GensiteCredentialMasked;
    verify: { ok: boolean; message: string };
  }>;
}

export interface GensiteVerifyResponse {
  site_code: string;
  vendor: string;
  backend: string;
  ok: boolean;
  message: string;
  discovered_site_id: string | null;
  discovered_equipment: Array<Record<string, unknown>>;
}

export async function getGensiteVendors(): Promise<GensiteVendorsResponse> {
  return request('/gensite/vendors');
}

export async function listGensiteSites(country?: string): Promise<{ sites: GensiteSite[]; count: number }> {
  const qs = country ? `?country=${encodeURIComponent(country)}` : '';
  return request(`/gensite/sites${qs}`);
}

export async function getGensiteSite(code: string): Promise<GensiteSiteDetail> {
  return request(`/gensite/sites/${encodeURIComponent(code)}`);
}

export async function getGensiteLive(code: string): Promise<{ site_code: string; readings: GensiteLiveReading[] }> {
  return request(`/gensite/sites/${encodeURIComponent(code)}/live`);
}

export async function commissionGensite(req: GensiteCommissionRequest): Promise<GensiteCommissionResponse> {
  return request('/gensite/commission', { method: 'POST', body: JSON.stringify(req) });
}

export async function verifyGensiteCredential(
  code: string,
  vendor: string,
  backend: string,
): Promise<GensiteVerifyResponse> {
  return request(
    `/gensite/sites/${encodeURIComponent(code)}/credentials/${encodeURIComponent(vendor)}/${encodeURIComponent(backend)}/verify`,
    { method: 'POST' },
  );
}

export async function rotateGensiteCredential(
  code: string,
  vendor: string,
  backend: string,
  body: Omit<GensiteCredentialInput, 'vendor' | 'backend'>,
): Promise<{ credential: GensiteCredentialMasked; verify: { ok: boolean; message: string } }> {
  return request(
    `/gensite/sites/${encodeURIComponent(code)}/credentials/${encodeURIComponent(vendor)}/${encodeURIComponent(backend)}/rotate`,
    { method: 'POST', body: JSON.stringify(body) },
  );
}

// Series + alarms

export interface GensiteSeriesPoint {
  ts: string;
  equipment_id: number;
  value: number;
}

export interface GensiteSeriesResponse {
  site_code: string;
  metric: string;
  start_utc: string;
  end_utc: string;
  bucket_seconds: number;
  points: GensiteSeriesPoint[];
}

export async function getGensiteSeries(
  code: string,
  metric: string,
  hours = 24,
): Promise<GensiteSeriesResponse> {
  return request(
    `/gensite/sites/${encodeURIComponent(code)}/series?metric=${encodeURIComponent(metric)}&hours=${hours}`,
  );
}

export interface GensiteAlarm {
  id: number;
  equipment_id: number | null;
  site_code: string;
  vendor_code: string | null;
  vendor_msg: string | null;
  severity: string;
  raised_at: string;
  cleared_at: string | null;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  ticket_id_ugp: string | null;
  vendor?: string;
  kind?: string;
  model?: string | null;
  serial?: string | null;
}

export async function listGensiteAlarms(
  code: string,
  state: 'open' | 'all' = 'open',
): Promise<{ site_code: string; state: string; count: number; alarms: GensiteAlarm[] }> {
  return request(`/gensite/sites/${encodeURIComponent(code)}/alarms?state=${state}`);
}

export async function ackGensiteAlarm(
  alarmId: number,
  note?: string,
): Promise<{ alarm: GensiteAlarm }> {
  return request(`/gensite/alarms/${alarmId}/ack`, {
    method: 'POST',
    body: JSON.stringify({ note: note ?? null }),
  });
}

export async function openUgpTicketForAlarm(
  alarmId: number,
  body: {
    category?: string;
    priority?: string;
    fault_description?: string;
    services_affected?: string;
  } = {},
): Promise<{ ticket_pg_id: number; ticket_id_ugp: string; alarm_id: number; site_code: string }> {
  return request(`/gensite/alarms/${alarmId}/open-ugp-ticket`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

// Health is at root level, not under /api
export async function getHealth() {
  const res = await fetch('/health');
  return res.json();
}

// ---------------------------------------------------------------------------
// Billing meter primacy (1Meter migration test)
// See acdb-api/billing_priority.py and
// docs/ops/1meter-billing-migration-protocol.md.
// ---------------------------------------------------------------------------

export type BillingPriority = 'sm' | '1m';

export interface BillingPrioritySummary {
  fleet_default: BillingPriority;
  valid_priorities: BillingPriority[];
  /** Counts of explicit per-account overrides, keyed by priority. */
  per_account_overrides: Partial<Record<BillingPriority, number>>;
}

export interface BillingPriorityForAccount {
  account_number: string;
  /** The explicit per-account override, or null if the account inherits the fleet default. */
  override: BillingPriority | null;
  effective_priority: BillingPriority;
  fleet_default: BillingPriority;
}

export interface BillingPriorityUpdateResult {
  status: 'ok' | 'noop';
  account_number?: string;
  previous_override?: BillingPriority | null;
  override?: BillingPriority | null;
  effective_priority?: BillingPriority;
  previous_default?: BillingPriority;
  fleet_default?: BillingPriority;
}

export async function getBillingPrioritySummary(): Promise<BillingPrioritySummary> {
  return request('/billing-priority');
}

export async function getAccountBillingPriority(
  account_number: string,
): Promise<BillingPriorityForAccount> {
  return request(`/billing-priority/${encodeURIComponent(account_number)}`);
}

export async function setAccountBillingPriority(
  account_number: string,
  priority: BillingPriority | null,
  note?: string,
): Promise<BillingPriorityUpdateResult> {
  return request(`/billing-priority/${encodeURIComponent(account_number)}`, {
    method: 'PATCH',
    body: JSON.stringify({ priority, note }),
  });
}

export async function setFleetBillingPriority(
  priority: BillingPriority,
  note?: string,
): Promise<BillingPriorityUpdateResult> {
  return request('/billing-priority', {
    method: 'PATCH',
    body: JSON.stringify({ priority, note }),
  });
}
