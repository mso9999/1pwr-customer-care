import { useEffect, useState, useCallback } from 'react';
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
        const allSites = Array.from(new Set(d.tickets.map(t => t.site_code).filter(Boolean) as string[]));
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
      setError('Ticket name or fault description is required');
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

  const startEdit = (t: Ticket) => {
    setEditId(t.id);
    setEditData({
      ticket_name: t.ticket_name || '',
      site_code: t.site_code || '',
      fault_description: t.fault_description || '',
      failure_time: t.failure_time || '',
      services_affected: t.services_affected || '',
      troubleshooting_steps: t.troubleshooting_steps || '',
      cause_of_fault: t.cause_of_fault || '',
      precautions: t.precautions || '',
      restoration_time: t.restoration_time || '',
      resolution_approach: t.resolution_approach || '',
      duration: t.duration || '',
      status: t.status || 'open',
      priority: t.priority || '',
      category: t.category || '',
      reported_by: t.reported_by || '',
      resolved_by: t.resolved_by || '',
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
        <Field label="Ticket Name (Site / Asset)" field="ticket_name" />
        <Field label="Site Code" field="site_code" />
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Priority</label>
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
          <label className="block text-xs font-medium text-gray-600 mb-1">Status</label>
          <select
            className="w-full px-3 py-2 border rounded-lg text-sm"
            value={editData.status ?? 'open'}
            onChange={e => setEditData(p => ({ ...p, status: e.target.value }))}
          >
            {STATUSES.map(s => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
          </select>
        </div>
        <Field label="Category" field="category" />
        <Field label="Reported By" field="reported_by" />
        <Field label="Failure Time" field="failure_time" type="datetime-local" />
        <Field label="Restoration Time" field="restoration_time" type="datetime-local" />
        <Field label="Duration" field="duration" />
        <Field label="Resolved By" field="resolved_by" />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Field label="Fault Description" field="fault_description" rows={3} />
        <Field label="Service(s) Affected" field="services_affected" rows={3} />
        <Field label="Troubleshooting Steps" field="troubleshooting_steps" rows={3} />
        <Field label="Cause of Fault" field="cause_of_fault" rows={3} />
        <Field label="Precautions" field="precautions" rows={3} />
        <Field label="Resolution Approach" field="resolution_approach" rows={3} />
      </div>
      <div className="flex gap-3 pt-2">
        <button
          onClick={onSubmit}
          disabled={saving}
          className="px-5 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition"
        >
          {saving ? 'Saving...' : submitLabel}
        </button>
        <button
          onClick={() => { setShowCreate(false); setEditId(null); setEditData({}); }}
          className="px-5 py-2.5 bg-gray-100 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-200 transition"
        >
          Cancel
        </button>
      </div>
    </div>
  );

  return (
    <div className="p-4 sm:p-6 max-w-[1400px] mx-auto">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-5">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-gray-800">Maintenance Log</h1>
          <p className="text-sm text-gray-500 mt-0.5">O&M Corrective (Fault) Maintenance Tickets</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={handleExport}
            disabled={exporting}
            className="px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50 transition flex items-center gap-1.5"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
            {exporting ? 'Exporting...' : 'Download Excel'}
          </button>
          {canWrite && (
            <button
              onClick={() => { setShowCreate(true); setEditId(null); setEditData({ status: 'open', source: 'portal' }); }}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition flex items-center gap-1.5"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" /></svg>
              New Ticket
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

      {showCreate && <TicketForm onSubmit={handleCreate} submitLabel="Create Ticket" />}
      {editId !== null && <TicketForm onSubmit={handleUpdate} submitLabel="Save Changes" />}

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-4">
        <form
          onSubmit={e => { e.preventDefault(); setSearch(searchInput); setOffset(0); }}
          className="flex gap-2 flex-1 min-w-[200px]"
        >
          <input
            type="text"
            placeholder="Search tickets..."
            className="flex-1 px-3 py-2 border rounded-lg text-sm"
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
          />
          <button type="submit" className="px-4 py-2 bg-gray-100 rounded-lg text-sm font-medium hover:bg-gray-200 transition">
            Search
          </button>
        </form>
        <select
          className="px-3 py-2 border rounded-lg text-sm bg-white"
          value={filterSite}
          onChange={e => { setFilterSite(e.target.value); setOffset(0); }}
        >
          <option value="">All Sites</option>
          {sites.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select
          className="px-3 py-2 border rounded-lg text-sm bg-white"
          value={filterStatus}
          onChange={e => { setFilterStatus(e.target.value); setOffset(0); }}
        >
          <option value="">All Statuses</option>
          {STATUSES.map(s => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
        </select>
      </div>

      {/* Desktop table */}
      <div className="hidden md:block bg-white rounded-xl shadow border overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
              <th className="px-4 py-3">Ticket</th>
              <th className="px-4 py-3">Site</th>
              <th className="px-4 py-3">Failure Time</th>
              <th className="px-4 py-3">Fault</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Priority</th>
              <th className="px-4 py-3">Duration</th>
              <th className="px-4 py-3">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {loading ? (
              <tr><td colSpan={8} className="px-4 py-8 text-center text-gray-400">Loading...</td></tr>
            ) : (data?.tickets.length ?? 0) === 0 ? (
              <tr><td colSpan={8} className="px-4 py-8 text-center text-gray-400">No tickets found</td></tr>
            ) : data?.tickets.map(t => (
              <tr key={t.id} className="hover:bg-gray-50 cursor-pointer" onClick={() => setExpandedId(expandedId === t.id ? null : t.id)}>
                <td className="px-4 py-3">
                  <div className="font-medium text-gray-800">{t.ticket_name || t.fault_description?.slice(0, 40) || `#${t.id}`}</div>
                  <div className="text-xs text-gray-400 mt-0.5">#{t.id}</div>
                </td>
                <td className="px-4 py-3 font-mono text-xs">{t.site_code || '--'}</td>
                <td className="px-4 py-3 text-xs">{fmtDate(t.failure_time || t.created_at)}</td>
                <td className="px-4 py-3 max-w-[250px] truncate text-gray-600">{t.fault_description || '--'}</td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusColor[t.status] || 'bg-gray-100'}`}>
                    {t.status.replace('_', ' ')}
                  </span>
                </td>
                <td className="px-4 py-3">
                  {t.priority ? (
                    <span className={`px-2 py-0.5 rounded text-xs font-bold ${priorityColor[t.priority] || 'bg-gray-400 text-white'}`}>
                      {t.priority}
                    </span>
                  ) : '--'}
                </td>
                <td className="px-4 py-3 text-xs">{t.duration || '--'}</td>
                <td className="px-4 py-3" onClick={e => e.stopPropagation()}>
                  {canWrite && (
                    <button
                      onClick={() => startEdit(t)}
                      className="text-blue-600 hover:text-blue-800 text-xs font-medium"
                    >
                      Edit
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {/* Expanded detail row */}
        {expandedId && data?.tickets.filter(t => t.id === expandedId).map(t => (
          <div key={`detail-${t.id}`} className="border-t bg-blue-50/40 px-6 py-4">
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-4 text-sm">
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase">Services Affected</p>
                <p className="mt-1 text-gray-700 whitespace-pre-wrap">{t.services_affected || '--'}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase">Troubleshooting Steps</p>
                <p className="mt-1 text-gray-700 whitespace-pre-wrap">{t.troubleshooting_steps || '--'}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase">Cause of Fault</p>
                <p className="mt-1 text-gray-700 whitespace-pre-wrap">{t.cause_of_fault || '--'}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase">Precautions</p>
                <p className="mt-1 text-gray-700 whitespace-pre-wrap">{t.precautions || '--'}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase">Resolution Approach</p>
                <p className="mt-1 text-gray-700 whitespace-pre-wrap">{t.resolution_approach || '--'}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase">Restoration Time</p>
                <p className="mt-1 text-gray-700">{fmtDate(t.restoration_time)}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase">Reported By</p>
                <p className="mt-1 text-gray-700">{t.reported_by || '--'}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase">Resolved By</p>
                <p className="mt-1 text-gray-700">{t.resolved_by || '--'}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase">Category</p>
                <p className="mt-1 text-gray-700">{t.category || '--'}</p>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Mobile cards */}
      <div className="md:hidden space-y-3">
        {loading ? (
          <p className="text-center text-gray-400 py-8">Loading...</p>
        ) : (data?.tickets.length ?? 0) === 0 ? (
          <p className="text-center text-gray-400 py-8">No tickets found</p>
        ) : data?.tickets.map(t => (
          <div key={t.id} className="bg-white rounded-xl shadow border p-4" onClick={() => setExpandedId(expandedId === t.id ? null : t.id)}>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="font-medium text-gray-800 text-sm truncate">
                  {t.ticket_name || t.fault_description?.slice(0, 40) || `#${t.id}`}
                </p>
                <p className="text-xs text-gray-400 mt-0.5 font-mono">{t.site_code || '--'} &middot; #{t.id}</p>
              </div>
              <div className="flex flex-col items-end gap-1">
                <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusColor[t.status] || 'bg-gray-100'}`}>
                  {t.status.replace('_', ' ')}
                </span>
                {t.priority && (
                  <span className={`px-2 py-0.5 rounded text-xs font-bold ${priorityColor[t.priority] || 'bg-gray-400 text-white'}`}>
                    {t.priority}
                  </span>
                )}
              </div>
            </div>
            <p className="text-xs text-gray-600 mt-2 line-clamp-2">{t.fault_description || '--'}</p>
            <div className="flex justify-between items-center mt-2 text-xs text-gray-400">
              <span>{fmtDate(t.failure_time || t.created_at)}</span>
              <span>{t.duration || '--'}</span>
            </div>
            {expandedId === t.id && (
              <div className="mt-3 pt-3 border-t space-y-2 text-xs">
                {t.services_affected && <div><span className="font-semibold text-gray-500">Services Affected:</span> <span className="text-gray-700">{t.services_affected}</span></div>}
                {t.troubleshooting_steps && <div><span className="font-semibold text-gray-500">Troubleshooting:</span> <span className="text-gray-700">{t.troubleshooting_steps}</span></div>}
                {t.cause_of_fault && <div><span className="font-semibold text-gray-500">Cause:</span> <span className="text-gray-700">{t.cause_of_fault}</span></div>}
                {t.precautions && <div><span className="font-semibold text-gray-500">Precautions:</span> <span className="text-gray-700">{t.precautions}</span></div>}
                {t.resolution_approach && <div><span className="font-semibold text-gray-500">Resolution:</span> <span className="text-gray-700">{t.resolution_approach}</span></div>}
                {t.restoration_time && <div><span className="font-semibold text-gray-500">Restored:</span> <span className="text-gray-700">{fmtDate(t.restoration_time)}</span></div>}
                {canWrite && (
                  <button
                    onClick={e => { e.stopPropagation(); startEdit(t); }}
                    className="mt-2 px-3 py-1.5 bg-blue-600 text-white rounded-lg text-xs font-medium"
                  >
                    Edit
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
          <span>{total} ticket{total !== 1 ? 's' : ''}</span>
          <div className="flex gap-2">
            <button
              disabled={page <= 1}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              className="px-3 py-1.5 bg-white border rounded-lg hover:bg-gray-50 disabled:opacity-40 transition"
            >
              Previous
            </button>
            <span className="px-3 py-1.5">Page {page} / {totalPages}</span>
            <button
              disabled={page >= totalPages}
              onClick={() => setOffset(offset + PAGE_SIZE)}
              className="px-3 py-1.5 bg-white border rounded-lg hover:bg-gray-50 disabled:opacity-40 transition"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
