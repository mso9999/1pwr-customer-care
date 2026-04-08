import { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  listTickets, createTicket, updateTicket, downloadTicketsExcel,
  type Ticket, type TicketsResponse,
} from '../lib/api';
import { useAuth } from '../contexts/AuthContext';

const STATUSES = ['open', 'in_progress', 'resolved', 'pending'] as const;
const PRIORITIES = ['P1', 'P2', 'P3', 'P4'] as const;
const PAGE_SIZE = 25;

function fmtDate(d: string | null | undefined): string {
  if (!d) return '--';
  try {
    const dt = new Date(d);
    return dt.toLocaleDateString('en-ZA', { year: 'numeric', month: 'short', day: 'numeric' })
      + ' ' + dt.toLocaleTimeString('en-ZA', { hour: '2-digit', minute: '2-digit' });
  } catch { return d; }
}

function fmtDateInput(d: string | null | undefined): string {
  if (!d) return '';
  try {
    return new Date(d).toISOString().slice(0, 16);
  } catch { return ''; }
}

const statusColor: Record<string, string> = {
  open: 'bg-red-100 text-red-700',
  in_progress: 'bg-yellow-100 text-yellow-700',
  resolved: 'bg-green-100 text-green-700',
  pending: 'bg-gray-100 text-gray-600',
};

const priorityColor: Record<string, string> = {
  P1: 'bg-red-600 text-white',
  P2: 'bg-orange-500 text-white',
  P3: 'bg-blue-500 text-white',
  P4: 'bg-gray-400 text-white',
};

export default function TicketsPage() {
  const { t } = useTranslation(['tickets', 'common']);
  const [data, setData] = useState<TicketsResponse | null>(null);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [filterSite, setFilterSite] = useState('');
  const [loading, setLoading] = useState(true);
  const [sites, setSites] = useState<string[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [editData, setEditData] = useState<Partial<Ticket>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [exporting, setExporting] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const { canWrite } = useAuth();

  const load = useCallback(() => {
    setLoading(true);
    listTickets({
      limit: PAGE_SIZE, offset,
      site_code: filterSite || undefined,
      status: filterStatus || undefined,
      search: search || undefined,
    })
      .then(d => {
        setData(d);
        const allSites = Array.from(new Set(d.tickets.map(tk => tk.site_code).filter(Boolean) as string[]));
        setSites(prev => {
          const merged = new Set([...prev, ...allSites]);
          return Array.from(merged).sort();
        });
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [offset, filterStatus, filterSite, search]);

  useEffect(() => { load(); }, [load]);

  const handleExport = async () => {
    setExporting(true);
    try {
      await downloadTicketsExcel({
        site_code: filterSite || undefined,
        status: filterStatus || undefined,
      });
    } catch (e: any) {
      setError(e.message);
    } finally {
      setExporting(false);
    }
  };

  const handleCreate = async () => {
    if (!editData.ticket_name && !editData.fault_description) {
      setError(t('tickets:ticketRequired'));
      return;
    }
    setSaving(true);
    try {
      await createTicket(editData);
      setShowCreate(false);
      setEditData({});
      load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleUpdate = async () => {
    if (editId === null) return;
    setSaving(true);
    try {
      await updateTicket(editId, editData);
      setEditId(null);
      setEditData({});
      load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const startEdit = (tk: Ticket) => {
    setEditId(tk.id);
    setEditData({
      ticket_name: tk.ticket_name || '',
      site_code: tk.site_code || '',
      fault_description: tk.fault_description || '',
      failure_time: tk.failure_time || '',
      services_affected: tk.services_affected || '',
      troubleshooting_steps: tk.troubleshooting_steps || '',
      cause_of_fault: tk.cause_of_fault || '',
      precautions: tk.precautions || '',
      restoration_time: tk.restoration_time || '',
      resolution_approach: tk.resolution_approach || '',
      duration: tk.duration || '',
      status: tk.status || 'open',
      priority: tk.priority || '',
      category: tk.category || '',
      reported_by: tk.reported_by || '',
      resolved_by: tk.resolved_by || '',
    });
    setShowCreate(false);
  };

  const total = data?.total ?? 0;
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const Field = ({ label, field, type = 'text', rows }: {
    label: string; field: keyof Ticket; type?: string; rows?: number;
  }) => (
    <div>
      <label className="block text-xs font-medium text-gray-600 mb-1">{label}</label>
      {rows ? (
        <textarea
          className="w-full px-3 py-2 border rounded-lg text-sm resize-y"
          rows={rows}
          value={String(editData[field] ?? '')}
          onChange={e => setEditData(p => ({ ...p, [field]: e.target.value }))}
        />
      ) : type === 'datetime-local' ? (
        <input
          type="datetime-local"
          className="w-full px-3 py-2 border rounded-lg text-sm"
          value={fmtDateInput(editData[field] as string)}
          onChange={e => setEditData(p => ({ ...p, [field]: e.target.value ? new Date(e.target.value).toISOString() : '' }))}
        />
      ) : (
        <input
          type={type}
          className="w-full px-3 py-2 border rounded-lg text-sm"
          value={String(editData[field] ?? '')}
          onChange={e => setEditData(p => ({ ...p, [field]: e.target.value }))}
        />
      )}
    </div>
  );

  const TicketForm = ({ onSubmit, submitLabel }: { onSubmit: () => void; submitLabel: string }) => (
    <div className="bg-white rounded-xl shadow-lg border p-4 sm:p-6 mb-6 space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <Field label={t('tickets:ticketName')} field="ticket_name" />
        <Field label={t('tickets:siteCode')} field="site_code" />
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">{t('tickets:priority')}</label>
          <select
            className="w-full px-3 py-2 border rounded-lg text-sm"
            value={editData.priority ?? ''}
            onChange={e => setEditData(p => ({ ...p, priority: e.target.value }))}
          >
            <option value="">--</option>
            {PRIORITIES.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">{t('tickets:status')}</label>
          <select
            className="w-full px-3 py-2 border rounded-lg text-sm"
            value={editData.status ?? 'open'}
            onChange={e => setEditData(p => ({ ...p, status: e.target.value }))}
          >
            {STATUSES.map(s => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
          </select>
        </div>
        <Field label={t('tickets:category')} field="category" />
        <Field label={t('tickets:reportedBy')} field="reported_by" />
        <Field label={t('tickets:failureTime')} field="failure_time" type="datetime-local" />
        <Field label={t('tickets:restorationTime')} field="restoration_time" type="datetime-local" />
        <Field label={t('tickets:duration')} field="duration" />
        <Field label={t('tickets:resolvedBy')} field="resolved_by" />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Field label={t('tickets:faultDescription')} field="fault_description" rows={3} />
        <Field label={t('tickets:servicesAffected')} field="services_affected" rows={3} />
        <Field label={t('tickets:troubleshootingSteps')} field="troubleshooting_steps" rows={3} />
        <Field label={t('tickets:causeOfFault')} field="cause_of_fault" rows={3} />
        <Field label={t('tickets:precautions')} field="precautions" rows={3} />
        <Field label={t('tickets:resolutionApproach')} field="resolution_approach" rows={3} />
      </div>
      <div className="flex gap-3 pt-2">
        <button
          onClick={onSubmit}
          disabled={saving}
          className="px-5 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition"
        >
          {saving ? t('common:saving') : submitLabel}
        </button>
        <button
          onClick={() => { setShowCreate(false); setEditId(null); setEditData({}); }}
          className="px-5 py-2.5 bg-gray-100 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-200 transition"
        >
          {t('common:cancel')}
        </button>
      </div>
    </div>
  );

  return (
    <div className="p-4 sm:p-6 max-w-[1400px] mx-auto">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-5">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-gray-800">{t('tickets:title')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('tickets:subtitle')}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={handleExport}
            disabled={exporting}
            className="px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50 transition flex items-center gap-1.5"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
            {exporting ? t('common:exporting') : t('tickets:downloadExcel')}
          </button>
          {canWrite && (
            <button
              onClick={() => { setShowCreate(true); setEditId(null); setEditData({ status: 'open', source: 'portal' }); }}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition flex items-center gap-1.5"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" /></svg>
              {t('tickets:newTicket')}
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg mb-4 text-sm flex justify-between">
          {error}
          <button onClick={() => setError('')} className="font-bold ml-3">&times;</button>
        </div>
      )}

      {showCreate && <TicketForm onSubmit={handleCreate} submitLabel={t('tickets:createTicket')} />}
      {editId !== null && <TicketForm onSubmit={handleUpdate} submitLabel={t('tickets:saveChanges')} />}

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-4">
        <form
          onSubmit={e => { e.preventDefault(); setSearch(searchInput); setOffset(0); }}
          className="flex gap-2 flex-1 min-w-[200px]"
        >
          <input
            type="text"
            placeholder={t('tickets:searchPlaceholder')}
            className="flex-1 px-3 py-2 border rounded-lg text-sm"
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
          />
          <button type="submit" className="px-4 py-2 bg-gray-100 rounded-lg text-sm font-medium hover:bg-gray-200 transition">
            {t('common:search')}
          </button>
        </form>
        <select
          className="px-3 py-2 border rounded-lg text-sm bg-white"
          value={filterSite}
          onChange={e => { setFilterSite(e.target.value); setOffset(0); }}
        >
          <option value="">{t('common:allSites')}</option>
          {sites.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select
          className="px-3 py-2 border rounded-lg text-sm bg-white"
          value={filterStatus}
          onChange={e => { setFilterStatus(e.target.value); setOffset(0); }}
        >
          <option value="">{t('common:allStatuses')}</option>
          {STATUSES.map(s => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
        </select>
      </div>

      {/* Desktop table */}
      <div className="hidden md:block bg-white rounded-xl shadow border overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
              <th className="px-4 py-3">{t('tickets:ticket')}</th>
              <th className="px-4 py-3">{t('tickets:site')}</th>
              <th className="px-4 py-3">{t('tickets:failureTime')}</th>
              <th className="px-4 py-3">{t('tickets:fault')}</th>
              <th className="px-4 py-3">{t('tickets:status')}</th>
              <th className="px-4 py-3">{t('tickets:priority')}</th>
              <th className="px-4 py-3">{t('tickets:duration')}</th>
              <th className="px-4 py-3">{t('tickets:actions')}</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {loading ? (
              <tr><td colSpan={8} className="px-4 py-8 text-center text-gray-400">{t('common:loading')}</td></tr>
            ) : (data?.tickets.length ?? 0) === 0 ? (
              <tr><td colSpan={8} className="px-4 py-8 text-center text-gray-400">{t('tickets:noTickets')}</td></tr>
            ) : data?.tickets.map(tk => (
              <tr key={tk.id} className="hover:bg-gray-50 cursor-pointer" onClick={() => setExpandedId(expandedId === tk.id ? null : tk.id)}>
                <td className="px-4 py-3">
                  <div className="font-medium text-gray-800">{tk.ticket_name || tk.fault_description?.slice(0, 40) || `#${tk.id}`}</div>
                  <div className="text-xs text-gray-400 mt-0.5">#{tk.id}</div>
                </td>
                <td className="px-4 py-3 font-mono text-xs">{tk.site_code || '--'}</td>
                <td className="px-4 py-3 text-xs">{fmtDate(tk.failure_time || tk.created_at)}</td>
                <td className="px-4 py-3 max-w-[250px] truncate text-gray-600">{tk.fault_description || '--'}</td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusColor[tk.status] || 'bg-gray-100'}`}>
                    {tk.status.replace('_', ' ')}
                  </span>
                </td>
                <td className="px-4 py-3">
                  {tk.priority ? (
                    <span className={`px-2 py-0.5 rounded text-xs font-bold ${priorityColor[tk.priority] || 'bg-gray-400 text-white'}`}>
                      {tk.priority}
                    </span>
                  ) : '--'}
                </td>
                <td className="px-4 py-3 text-xs">{tk.duration || '--'}</td>
                <td className="px-4 py-3" onClick={e => e.stopPropagation()}>
                  {canWrite && (
                    <button
                      onClick={() => startEdit(tk)}
                      className="text-blue-600 hover:text-blue-800 text-xs font-medium"
                    >
                      {t('common:edit')}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {/* Expanded detail row */}
        {expandedId && data?.tickets.filter(tk => tk.id === expandedId).map(tk => (
          <div key={`detail-${tk.id}`} className="border-t bg-blue-50/40 px-6 py-4">
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-4 text-sm">
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase">{t('tickets:servicesAffected')}</p>
                <p className="mt-1 text-gray-700 whitespace-pre-wrap">{tk.services_affected || '--'}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase">{t('tickets:troubleshootingSteps')}</p>
                <p className="mt-1 text-gray-700 whitespace-pre-wrap">{tk.troubleshooting_steps || '--'}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase">{t('tickets:causeOfFault')}</p>
                <p className="mt-1 text-gray-700 whitespace-pre-wrap">{tk.cause_of_fault || '--'}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase">{t('tickets:precautions')}</p>
                <p className="mt-1 text-gray-700 whitespace-pre-wrap">{tk.precautions || '--'}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase">{t('tickets:resolutionApproach')}</p>
                <p className="mt-1 text-gray-700 whitespace-pre-wrap">{tk.resolution_approach || '--'}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase">{t('tickets:restorationTime')}</p>
                <p className="mt-1 text-gray-700">{fmtDate(tk.restoration_time)}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase">{t('tickets:reportedBy')}</p>
                <p className="mt-1 text-gray-700">{tk.reported_by || '--'}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase">{t('tickets:resolvedBy')}</p>
                <p className="mt-1 text-gray-700">{tk.resolved_by || '--'}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase">{t('tickets:category')}</p>
                <p className="mt-1 text-gray-700">{tk.category || '--'}</p>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Mobile cards */}
      <div className="md:hidden space-y-3">
        {loading ? (
          <p className="text-center text-gray-400 py-8">{t('common:loading')}</p>
        ) : (data?.tickets.length ?? 0) === 0 ? (
          <p className="text-center text-gray-400 py-8">{t('tickets:noTickets')}</p>
        ) : data?.tickets.map(tk => (
          <div key={tk.id} className="bg-white rounded-xl shadow border p-4" onClick={() => setExpandedId(expandedId === tk.id ? null : tk.id)}>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="font-medium text-gray-800 text-sm truncate">
                  {tk.ticket_name || tk.fault_description?.slice(0, 40) || `#${tk.id}`}
                </p>
                <p className="text-xs text-gray-400 mt-0.5 font-mono">{tk.site_code || '--'} &middot; #{tk.id}</p>
              </div>
              <div className="flex flex-col items-end gap-1">
                <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusColor[tk.status] || 'bg-gray-100'}`}>
                  {tk.status.replace('_', ' ')}
                </span>
                {tk.priority && (
                  <span className={`px-2 py-0.5 rounded text-xs font-bold ${priorityColor[tk.priority] || 'bg-gray-400 text-white'}`}>
                    {tk.priority}
                  </span>
                )}
              </div>
            </div>
            <p className="text-xs text-gray-600 mt-2 line-clamp-2">{tk.fault_description || '--'}</p>
            <div className="flex justify-between items-center mt-2 text-xs text-gray-400">
              <span>{fmtDate(tk.failure_time || tk.created_at)}</span>
              <span>{tk.duration || '--'}</span>
            </div>
            {expandedId === tk.id && (
              <div className="mt-3 pt-3 border-t space-y-2 text-xs">
                {tk.services_affected && <div><span className="font-semibold text-gray-500">{t('tickets:servicesAffected')}:</span> <span className="text-gray-700">{tk.services_affected}</span></div>}
                {tk.troubleshooting_steps && <div><span className="font-semibold text-gray-500">{t('tickets:troubleshootingSteps')}:</span> <span className="text-gray-700">{tk.troubleshooting_steps}</span></div>}
                {tk.cause_of_fault && <div><span className="font-semibold text-gray-500">{t('tickets:causeOfFault')}:</span> <span className="text-gray-700">{tk.cause_of_fault}</span></div>}
                {tk.precautions && <div><span className="font-semibold text-gray-500">{t('tickets:precautions')}:</span> <span className="text-gray-700">{tk.precautions}</span></div>}
                {tk.resolution_approach && <div><span className="font-semibold text-gray-500">{t('tickets:resolutionApproach')}:</span> <span className="text-gray-700">{tk.resolution_approach}</span></div>}
                {tk.restoration_time && <div><span className="font-semibold text-gray-500">{t('tickets:restorationTime')}:</span> <span className="text-gray-700">{fmtDate(tk.restoration_time)}</span></div>}
                {canWrite && (
                  <button
                    onClick={e => { e.stopPropagation(); startEdit(tk); }}
                    className="mt-2 px-3 py-1.5 bg-blue-600 text-white rounded-lg text-xs font-medium"
                  >
                    {t('common:edit')}
                  </button>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Pagination */}
      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between mt-4 text-sm text-gray-500">
          <span>{t('tickets:tickets', { count: total })}</span>
          <div className="flex gap-2">
            <button
              disabled={page <= 1}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              className="px-3 py-1.5 bg-white border rounded-lg hover:bg-gray-50 disabled:opacity-40 transition"
            >
              {t('common:pagination.previous')}
            </button>
            <span className="px-3 py-1.5">{t('common:pagination.page', { page, pages: totalPages, total })}</span>
            <button
              disabled={page >= totalPages}
              onClick={() => setOffset(offset + PAGE_SIZE)}
              className="px-3 py-1.5 bg-white border rounded-lg hover:bg-gray-50 disabled:opacity-40 transition"
            >
              {t('common:pagination.next')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
