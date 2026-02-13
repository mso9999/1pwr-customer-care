import { useEffect, useState } from 'react';
import { listRoles, assignRole, updateRole, removeRole, type RoleAssignment } from '../lib/api';

const ROLES = ['superadmin', 'onm_team', 'finance_team', 'generic'];

export default function AdminRolesPage() {
  const [roles, setRoles] = useState<RoleAssignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Add form
  const [newId, setNewId] = useState('');
  const [newRole, setNewRole] = useState('generic');
  const [adding, setAdding] = useState(false);

  const refresh = () => {
    setLoading(true);
    listRoles().then(setRoles).catch(e => setError(e.message)).finally(() => setLoading(false));
  };

  useEffect(refresh, []);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newId) return;
    setAdding(true);
    setError('');
    try {
      await assignRole(newId, newRole);
      setNewId('');
      setNewRole('generic');
      refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setAdding(false);
    }
  };

  const handleUpdate = async (employeeId: string, newCcRole: string) => {
    try {
      await updateRole(employeeId, newCcRole);
      refresh();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleRemove = async (employeeId: string) => {
    if (!confirm(`Remove role for ${employeeId}? They'll revert to 'generic'.`)) return;
    try {
      await removeRole(employeeId);
      refresh();
    } catch (e: any) {
      setError(e.message);
    }
  };

  return (
    <div className="space-y-4 sm:space-y-6">
      <h1 className="text-xl sm:text-2xl font-bold text-gray-800">Employee Role Management</h1>

      {error && <p className="text-red-600 text-sm bg-red-50 p-3 rounded">{error}</p>}

      {/* Add new */}
      <div className="bg-white rounded-lg shadow p-4 sm:p-5">
        <h2 className="text-sm font-medium text-gray-600 mb-3">Assign Role</h2>
        <form onSubmit={handleAdd} className="space-y-3 sm:space-y-0 sm:flex sm:gap-3 sm:flex-wrap sm:items-end">
          <div className="sm:flex-none">
            <label className="block text-xs text-gray-500 mb-1">Employee ID</label>
            <input
              value={newId}
              onChange={e => setNewId(e.target.value)}
              placeholder="e.g. EMP001"
              className="w-full sm:w-48 px-3 py-2 border rounded-lg text-sm"
              required
            />
          </div>
          <div className="sm:flex-none">
            <label className="block text-xs text-gray-500 mb-1">CC Role</label>
            <select value={newRole} onChange={e => setNewRole(e.target.value)} className="w-full sm:w-auto px-3 py-2 border rounded-lg text-sm bg-white">
              {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
          <button type="submit" disabled={adding} className="w-full sm:w-auto px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50">
            {adding ? 'Adding...' : 'Assign'}
          </button>
        </form>
      </div>

      {/* Content */}
      {loading ? (
        <div className="text-center py-8 text-gray-400">Loading...</div>
      ) : roles.length === 0 ? (
        <div className="text-center py-8 text-gray-400">No role assignments yet. All employees default to 'generic'.</div>
      ) : (
        <>
          {/* Desktop table */}
          <div className="hidden md:block bg-white rounded-lg shadow overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Employee ID</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Name</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Email</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">CC Role</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Assigned By</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {roles.map(r => (
                  <tr key={r.employee_id} className="hover:bg-gray-50">
                    <td className="px-4 py-2 font-medium">{r.employee_id}</td>
                    <td className="px-4 py-2">{r.name || <span className="text-gray-300">--</span>}</td>
                    <td className="px-4 py-2 text-gray-500">{r.email || '--'}</td>
                    <td className="px-4 py-2">
                      <select
                        value={r.cc_role}
                        onChange={e => handleUpdate(r.employee_id, e.target.value)}
                        className="px-2 py-1 border rounded text-sm bg-white"
                      >
                        {ROLES.map(role => <option key={role} value={role}>{role}</option>)}
                      </select>
                    </td>
                    <td className="px-4 py-2 text-gray-500">{r.assigned_by}</td>
                    <td className="px-4 py-2">
                      <button onClick={() => handleRemove(r.employee_id)} className="text-red-600 hover:text-red-800 text-xs">Remove</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile card list */}
          <div className="md:hidden space-y-2">
            {roles.map(r => (
              <div key={r.employee_id} className="bg-white rounded-lg shadow p-4">
                <div className="flex items-start justify-between mb-2">
                  <div>
                    <p className="font-medium text-gray-800">{r.employee_id}</p>
                    <p className="text-sm text-gray-500">{r.name || '--'}</p>
                    {r.email && <p className="text-xs text-gray-400">{r.email}</p>}
                  </div>
                  <button onClick={() => handleRemove(r.employee_id)} className="text-red-600 hover:text-red-800 text-xs shrink-0 ml-2">Remove</button>
                </div>
                <div className="flex items-center justify-between">
                  <select
                    value={r.cc_role}
                    onChange={e => handleUpdate(r.employee_id, e.target.value)}
                    className="px-2 py-1.5 border rounded text-sm bg-white"
                  >
                    {ROLES.map(role => <option key={role} value={role}>{role}</option>)}
                  </select>
                  <span className="text-xs text-gray-400">by {r.assigned_by || '--'}</span>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
