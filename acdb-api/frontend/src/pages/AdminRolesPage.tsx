import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  listRoles, assignRole, updateRole, removeRole, type RoleAssignment,
  listDepartmentMappings, addDepartmentMapping, removeDepartmentMapping,
  listPRDepartments, type DepartmentMapping, type PRDepartment,
} from '../lib/api';

const ROLES = ['superadmin', 'onm_team', 'finance_team', 'generic'];
const MAPPABLE_ROLES = ['onm_team', 'finance_team'];

export default function AdminRolesPage() {
  const { t } = useTranslation(['admin', 'common']);

  /* ---- Employee roles state ---- */
  const [roles, setRoles] = useState<RoleAssignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [newId, setNewId] = useState('');
  const [newRole, setNewRole] = useState('generic');
  const [adding, setAdding] = useState(false);

  /* ---- Department mappings state ---- */
  const [deptMappings, setDeptMappings] = useState<DepartmentMapping[]>([]);
  const [prDepts, setPrDepts] = useState<PRDepartment[]>([]);
  const [deptLoading, setDeptLoading] = useState(true);
  const [deptError, setDeptError] = useState('');
  const [newDeptKey, setNewDeptKey] = useState('');
  const [newDeptLabel, setNewDeptLabel] = useState('');
  const [newDeptRole, setNewDeptRole] = useState('onm_team');
  const [addingDept, setAddingDept] = useState(false);

  const refresh = () => {
    setLoading(true);
    listRoles().then(setRoles).catch(e => setError(e.message)).finally(() => setLoading(false));
  };

  const refreshDepts = () => {
    setDeptLoading(true);
    Promise.all([listDepartmentMappings(), listPRDepartments()])
      .then(([mappings, depts]) => { setDeptMappings(mappings); setPrDepts(depts); })
      .catch(e => setDeptError(e.message))
      .finally(() => setDeptLoading(false));
  };

  useEffect(() => { refresh(); refreshDepts(); }, []);

  /* ---- Employee role handlers ---- */
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
    } catch (e: any) { setError(e.message); }
    finally { setAdding(false); }
  };

  const handleUpdate = async (employeeId: string, newCcRole: string) => {
    try { await updateRole(employeeId, newCcRole); refresh(); }
    catch (e: any) { setError(e.message); }
  };

  const handleRemove = async (employeeId: string) => {
    if (!confirm(t('admin:removeConfirm', { name: employeeId }))) return;
    try { await removeRole(employeeId); refresh(); }
    catch (e: any) { setError(e.message); }
  };

  /* ---- Department mapping handlers ---- */
  const handleAddDept = async (e: React.FormEvent) => {
    e.preventDefault();
    const key = newDeptKey.trim();
    if (!key) return;
    setAddingDept(true);
    setDeptError('');
    try {
      await addDepartmentMapping(key, newDeptRole, newDeptLabel);
      setNewDeptKey('');
      setNewDeptLabel('');
      setNewDeptRole('onm_team');
      refreshDepts();
    } catch (e: any) { setDeptError(e.message); }
    finally { setAddingDept(false); }
  };

  const handleRemoveDept = async (key: string) => {
    if (!confirm(t('admin:deptRemoveConfirm', { key }))) return;
    try { await removeDepartmentMapping(key); refreshDepts(); }
    catch (e: any) { setDeptError(e.message); }
  };

  const handleSelectPrDept = (dept: PRDepartment) => {
    setNewDeptKey(dept.name.toLowerCase());
    setNewDeptLabel(`${dept.name} (${dept.org_name || dept.org})`);
    if (dept.code) {
      setNewDeptKey(dept.code.toLowerCase());
      setNewDeptLabel(`${dept.name} [${dept.code}]`);
    }
  };

  const existingKeys = new Set(deptMappings.map(m => m.department_key));
  const unmappedDepts = prDepts.filter(d => {
    const nameLower = d.name.toLowerCase();
    const codeLower = (d.code || '').toLowerCase();
    return !existingKeys.has(nameLower) && (!codeLower || !existingKeys.has(codeLower));
  });

  return (
    <div className="space-y-6">
      <h1 className="text-xl sm:text-2xl font-bold text-gray-800">{t('admin:title')}</h1>

      {error && <p className="text-red-600 text-sm bg-red-50 p-3 rounded">{error}</p>}

      {/* ================================================================ */}
      {/* SECTION 1: Employee Role Overrides                               */}
      {/* ================================================================ */}

      <div className="bg-white rounded-lg shadow p-4 sm:p-5">
        <h2 className="text-sm font-medium text-gray-600 mb-3">{t('admin:assignRole')}</h2>
        <form onSubmit={handleAdd} className="space-y-3 sm:space-y-0 sm:flex sm:gap-3 sm:flex-wrap sm:items-end">
          <div className="sm:flex-none">
            <label className="block text-xs text-gray-500 mb-1">{t('admin:employeeId')}</label>
            <input
              value={newId}
              onChange={e => setNewId(e.target.value)}
              placeholder="e.g. EMP001"
              className="w-full sm:w-48 px-3 py-2 border rounded-lg text-sm"
              required
            />
          </div>
          <div className="sm:flex-none">
            <label className="block text-xs text-gray-500 mb-1">{t('admin:ccRole')}</label>
            <select value={newRole} onChange={e => setNewRole(e.target.value)} className="w-full sm:w-auto px-3 py-2 border rounded-lg text-sm bg-white">
              {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
          <button type="submit" disabled={adding} className="w-full sm:w-auto px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50">
            {adding ? t('admin:adding') : t('admin:assign')}
          </button>
        </form>
      </div>

      {loading ? (
        <div className="text-center py-8 text-gray-400">{t('admin:loading')}</div>
      ) : roles.length === 0 ? (
        <div className="text-center py-8 text-gray-400">{t('admin:empty')}</div>
      ) : (
        <>
          {/* Desktop table */}
          <div className="hidden md:block bg-white rounded-lg shadow overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">{t('admin:colEmployeeId')}</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">{t('admin:colName')}</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">{t('admin:colEmail')}</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">{t('admin:colRole')}</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">{t('admin:colAssignedBy')}</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">{t('admin:colActions')}</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {roles.map(r => (
                  <tr key={r.employee_id} className="hover:bg-gray-50">
                    <td className="px-4 py-2 font-medium">{r.employee_id}</td>
                    <td className="px-4 py-2">{r.name || <span className="text-gray-300">--</span>}</td>
                    <td className="px-4 py-2 text-gray-500">{r.email || '--'}</td>
                    <td className="px-4 py-2">
                      <select value={r.cc_role} onChange={e => handleUpdate(r.employee_id, e.target.value)} className="px-2 py-1 border rounded text-sm bg-white">
                        {ROLES.map(role => <option key={role} value={role}>{role}</option>)}
                      </select>
                    </td>
                    <td className="px-4 py-2 text-gray-500">{r.assigned_by}</td>
                    <td className="px-4 py-2">
                      <button onClick={() => handleRemove(r.employee_id)} className="text-red-600 hover:text-red-800 text-xs">{t('admin:remove')}</button>
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
                  <button onClick={() => handleRemove(r.employee_id)} className="text-red-600 hover:text-red-800 text-xs shrink-0 ml-2">{t('admin:remove')}</button>
                </div>
                <div className="flex items-center justify-between">
                  <select value={r.cc_role} onChange={e => handleUpdate(r.employee_id, e.target.value)} className="px-2 py-1.5 border rounded text-sm bg-white">
                    {ROLES.map(role => <option key={role} value={role}>{role}</option>)}
                  </select>
                  <span className="text-xs text-gray-400">by {r.assigned_by || '--'}</span>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {/* ================================================================ */}
      {/* SECTION 2: Department → Role Auto-Mapping                        */}
      {/* ================================================================ */}

      <div className="border-t pt-6">
        <h2 className="text-lg font-bold text-gray-800 mb-1">{t('admin:deptMappingTitle')}</h2>
        <p className="text-sm text-gray-500 mb-4">{t('admin:deptMappingDesc')}</p>

        {deptError && <p className="text-red-600 text-sm bg-red-50 p-3 rounded mb-3">{deptError}</p>}

        {/* Add form */}
        <div className="bg-white rounded-lg shadow p-4 sm:p-5 mb-4">
          <h3 className="text-sm font-medium text-gray-600 mb-3">{t('admin:deptAddMapping')}</h3>
          <form onSubmit={handleAddDept} className="space-y-3 sm:space-y-0 sm:flex sm:gap-3 sm:flex-wrap sm:items-end">
            <div className="sm:flex-1 sm:min-w-[180px]">
              <label className="block text-xs text-gray-500 mb-1">{t('admin:deptKey')}</label>
              <input
                value={newDeptKey}
                onChange={e => setNewDeptKey(e.target.value)}
                placeholder={t('admin:deptKeyPlaceholder')}
                className="w-full px-3 py-2 border rounded-lg text-sm"
                required
              />
            </div>
            <div className="sm:flex-1 sm:min-w-[140px]">
              <label className="block text-xs text-gray-500 mb-1">{t('admin:deptLabel')}</label>
              <input
                value={newDeptLabel}
                onChange={e => setNewDeptLabel(e.target.value)}
                placeholder={t('admin:deptLabelPlaceholder')}
                className="w-full px-3 py-2 border rounded-lg text-sm"
              />
            </div>
            <div className="sm:flex-none">
              <label className="block text-xs text-gray-500 mb-1">{t('admin:deptMapsTo')}</label>
              <select value={newDeptRole} onChange={e => setNewDeptRole(e.target.value)} className="w-full sm:w-auto px-3 py-2 border rounded-lg text-sm bg-white">
                {MAPPABLE_ROLES.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
            <button type="submit" disabled={addingDept} className="w-full sm:w-auto px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50">
              {addingDept ? t('admin:adding') : t('admin:deptAdd')}
            </button>
          </form>
        </div>

        {/* PR departments quick-select */}
        {unmappedDepts.length > 0 && (
          <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 mb-4">
            <h3 className="text-sm font-semibold text-amber-800 mb-2">{t('admin:deptUnmappedTitle')}</h3>
            <p className="text-xs text-amber-700 mb-2">{t('admin:deptUnmappedDesc')}</p>
            <div className="flex flex-wrap gap-1.5">
              {unmappedDepts.map(d => (
                <button
                  key={d.id}
                  onClick={() => handleSelectPrDept(d)}
                  className="px-2.5 py-1 bg-white border border-amber-300 rounded text-xs text-amber-900 hover:bg-amber-100 transition"
                  title={`${d.name} (${d.code || '?'}) — ${d.org_name || d.org}`}
                >
                  {d.name} <span className="text-amber-500">· {d.org}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Current mappings table */}
        {deptLoading ? (
          <div className="text-center py-6 text-gray-400">{t('admin:loading')}</div>
        ) : deptMappings.length === 0 ? (
          <div className="text-center py-6 text-gray-400">{t('admin:deptEmpty')}</div>
        ) : (
          <div className="bg-white rounded-lg shadow overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">{t('admin:deptColKey')}</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">{t('admin:deptColLabel')}</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">{t('admin:deptColRole')}</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">{t('admin:colActions')}</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {deptMappings.map(m => (
                  <tr key={m.department_key} className="hover:bg-gray-50">
                    <td className="px-4 py-2 font-mono text-xs">{m.department_key}</td>
                    <td className="px-4 py-2 text-gray-600">{m.label || <span className="text-gray-300">--</span>}</td>
                    <td className="px-4 py-2">
                      <span className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${
                        m.cc_role === 'onm_team' ? 'bg-green-100 text-green-800' :
                        m.cc_role === 'finance_team' ? 'bg-blue-100 text-blue-800' :
                        'bg-gray-100 text-gray-700'
                      }`}>
                        {m.cc_role}
                      </span>
                    </td>
                    <td className="px-4 py-2">
                      <button onClick={() => handleRemoveDept(m.department_key)} className="text-red-600 hover:text-red-800 text-xs">{t('admin:remove')}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
