import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { getRecord, updateRecord, deleteRecord, getCustomerContracts, getCustomerWithAccounts, decommissionCustomer, type CommissionContract } from '../lib/api';
import { useAuth } from '../contexts/AuthContext';

export default function CustomerDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [record, setRecord] = useState<Record<string, unknown> | null>(null);
  const [editing, setEditing] = useState(false);
  const [formData, setFormData] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [contracts, setContracts] = useState<CommissionContract[]>([]);
  const [accountNumbers, setAccountNumbers] = useState<string[]>([]);
  const [decommissioning, setDecommissioning] = useState(false);
  const { canWrite, isSuperadmin, user } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (!id) return;
    getRecord('customers', id)
      .then(({ record: r }) => {
        setRecord(r);
        const fd: Record<string, string> = {};
        for (const [k, v] of Object.entries(r)) {
          fd[k] = v != null ? String(v) : '';
        }
        setFormData(fd);
      })
      .catch((e) => setError(e.message));
    // Resolve account numbers from all sources
    getCustomerWithAccounts(id)
      .then(({ customer }) => setAccountNumbers(customer.account_numbers || []))
      .catch(() => {});
    // Also fetch contracts
    getCustomerContracts(parseInt(id, 10))
      .then(({ contracts: c }) => setContracts(c))
      .catch(() => {});
  }, [id]);

  const handleSave = async () => {
    if (!id) return;
    setSaving(true);
    setError('');
    try {
      // Only send changed fields
      const changes: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(formData)) {
        if (record && String(record[k] ?? '') !== v) {
          changes[k] = v;
        }
      }
      if (Object.keys(changes).length === 0) {
        setEditing(false);
        return;
      }
      await updateRecord('customers', id, changes);
      setEditing(false);
      // Refresh
      const { record: r } = await getRecord('customers', id);
      setRecord(r);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!id || !confirm('Are you sure you want to delete this customer?')) return;
    try {
      await deleteRecord('customers', id);
      navigate('/customers');
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleDecommission = async () => {
    if (!id) return;
    const msg =
      'Decommission customer ' + id + '?\n\n' +
      'This sets DATE SERVICE TERMINATED to today.\n' +
      'All meter, account, and transaction history is preserved.';
    if (!confirm(msg)) return;
    setDecommissioning(true);
    setError('');
    try {
      const result = await decommissionCustomer(parseInt(id, 10));
      alert(`Customer ${id} decommissioned (terminated ${result.terminated_date}). All records preserved.`);
      // Refresh customer record to reflect new terminated status
      const { record: r } = await getRecord('customers', id);
      setRecord(r);
      const fd: Record<string, string> = {};
      for (const [k, v] of Object.entries(r)) {
        fd[k] = v != null ? String(v) : '';
      }
      setFormData(fd);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setDecommissioning(false);
    }
  };

  if (error && !record) return <div className="text-center py-8 text-red-500">{error}</div>;
  if (!record) return <div className="text-center py-8 text-gray-400">Loading...</div>;

  const fields = Object.keys(record);

  // Determine commissioning status from customer record
  const connectedVal = record['DATE SERVICE CONNECTED'];
  const terminatedVal = record['DATE SERVICE TERMINATED'];
  const isConnected = connectedVal != null && String(connectedVal).trim() !== '';
  const isTerminated = terminatedVal != null && String(terminatedVal).trim() !== '';
  const isCommissioned = isConnected && !isTerminated;

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
        <div className="flex items-center gap-3 min-w-0">
          <h1 className="text-xl sm:text-2xl font-bold text-gray-800 truncate">Customer: {id}</h1>
          {isTerminated && (
            <span className="px-2 py-0.5 bg-red-100 text-red-700 text-xs font-medium rounded-full shrink-0">Terminated</span>
          )}
          {isCommissioned && (
            <span className="px-2 py-0.5 bg-green-100 text-green-700 text-xs font-medium rounded-full shrink-0">Active</span>
          )}
        </div>
        <div className="flex flex-wrap gap-2 shrink-0">
          {canWrite && !editing && (
            <>
              <button onClick={() => setEditing(true)} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700">Edit</button>
              <button
                onClick={() => navigate(`/customer-data?account=${accountNumbers[0] || id}`)}
                className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700"
              >View Data</button>
              {isCommissioned ? (
                <button
                  onClick={handleDecommission}
                  disabled={decommissioning}
                  className="px-4 py-2 bg-red-600 text-white rounded-lg text-sm hover:bg-red-700 disabled:opacity-50"
                >
                  {decommissioning ? 'Decommissioning...' : 'Decommission'}
                </button>
              ) : (
                <>
                  <button onClick={() => navigate(`/assign-meter?customer=${id}`)} className="px-4 py-2 bg-emerald-600 text-white rounded-lg text-sm hover:bg-emerald-700">Assign Meter</button>
                  <button onClick={() => navigate(`/commission?customer=${id}`)} className="px-4 py-2 bg-amber-600 text-white rounded-lg text-sm hover:bg-amber-700">Commission</button>
                </>
              )}
            </>
          )}
          {editing && (
            <>
              <button onClick={handleSave} disabled={saving} className="px-4 py-2 bg-green-600 text-white rounded-lg text-sm hover:bg-green-700 disabled:opacity-50">
                {saving ? 'Saving...' : 'Save'}
              </button>
              <button onClick={() => setEditing(false)} className="px-4 py-2 bg-gray-200 rounded-lg text-sm hover:bg-gray-300">Cancel</button>
            </>
          )}
          {(isSuperadmin || user?.role === 'onm_team') && (
            <button onClick={handleDelete} className="px-4 py-2 bg-red-100 text-red-700 rounded-lg text-sm hover:bg-red-200">Delete</button>
          )}
        </div>
      </div>

      {error && <p className="text-red-600 text-sm bg-red-50 p-2 rounded">{error}</p>}

      {/* Account numbers */}
      {accountNumbers.length > 0 && (
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Account Numbers</h2>
          <div className="flex flex-wrap gap-2">
            {accountNumbers.map(acct => (
              <button
                key={acct}
                onClick={() => navigate(`/customer-data?account=${acct}`)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-blue-50 text-blue-700 rounded-lg text-sm font-mono font-medium hover:bg-blue-100 transition"
              >
                {acct}
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7l5 5m0 0l-5 5m5-5H6" />
                </svg>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="bg-white rounded-lg shadow">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-px bg-gray-200">
          {fields.map(field => (
            <div key={field} className="bg-white p-3 sm:p-4">
              <label className="block text-xs font-medium text-gray-500 uppercase tracking-wide mb-1 truncate">{field}</label>
              {editing ? (
                <input
                  value={formData[field] || ''}
                  onChange={e => setFormData(prev => ({ ...prev, [field]: e.target.value }))}
                  className="w-full px-2 py-1.5 border rounded text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                />
              ) : (
                <p className="text-sm text-gray-800 break-words">{record[field] != null ? String(record[field]) : <span className="text-gray-300">--</span>}</p>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Contracts section */}
      {contracts.length > 0 && (
        <div className="bg-white rounded-lg shadow p-4 sm:p-5">
          <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wide mb-3">Contracts</h2>
          <div className="space-y-2">
            {contracts.map((c, i) => (
              <a
                key={i}
                href={c.url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg hover:bg-gray-100 transition"
              >
                <svg className="w-5 h-5 text-red-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                </svg>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-800 truncate">{c.filename}</p>
                  <p className="text-xs text-gray-400">{c.lang === 'en' ? 'English' : 'Sesotho'}</p>
                </div>
                <svg className="w-4 h-4 text-gray-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                </svg>
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
