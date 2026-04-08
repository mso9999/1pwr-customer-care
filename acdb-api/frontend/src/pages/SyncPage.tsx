import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  listSyncSites, addSyncSite, getSyncPreview, executeSyncSite, getSyncStatus,
  discoverProjects,
} from '../lib/api';
import type { SiteProject, SyncPreview, SyncStatus } from '../lib/api';
import { useAuth } from '../contexts/AuthContext';

export default function SyncPage() {
  const { t } = useTranslation(['sync', 'common']);
  const { user } = useAuth();
  const isSuperadmin = user?.role === 'superadmin';

  const [sites, setSites] = useState<SiteProject[]>([]);
  const [status, setStatus] = useState<SyncStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Add site form
  const [showAddForm, setShowAddForm] = useState(false);
  const [newSiteCode, setNewSiteCode] = useState('');
  const [newProjectId, setNewProjectId] = useState('');
  const [newSiteName, setNewSiteName] = useState('');
  const [adding, setAdding] = useState(false);

  // Discover
  const [discovering, setDiscovering] = useState(false);

  // Preview state
  const [preview, setPreview] = useState<SyncPreview | null>(null);
  const [previewSite, setPreviewSite] = useState('');
  const [previewing, setPreviewing] = useState(false);
  const [syncing, setSyncing] = useState('');
  const [syncResult, setSyncResult] = useState<string>('');

  const fetchData = async () => {
    setLoading(true);
    setError('');
    try {
      const [sitesData, statusData] = await Promise.all([
        listSyncSites(),
        getSyncStatus(),
      ]);
      setSites(sitesData.sites);
      setStatus(statusData);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchData(); }, []);

  const handleAddSite = async () => {
    if (!newSiteCode || !newProjectId) return;
    setAdding(true);
    try {
      await addSyncSite(newSiteCode.toUpperCase(), newProjectId, newSiteName);
      setShowAddForm(false);
      setNewSiteCode('');
      setNewProjectId('');
      setNewSiteName('');
      fetchData();
    } catch (e: any) {
      alert(`Failed: ${e.message}`);
    } finally {
      setAdding(false);
    }
  };

  const handlePreview = async (siteCode: string) => {
    setPreviewing(true);
    setPreviewSite(siteCode);
    setPreview(null);
    setSyncResult('');
    try {
      const data = await getSyncPreview(siteCode);
      setPreview(data);
    } catch (e: any) {
      alert(`Preview failed: ${e.message}`);
    } finally {
      setPreviewing(false);
    }
  };

  const handleSync = async (siteCode: string) => {
    if (!confirm(`Execute sync for ${siteCode}? This will update both uGridPLAN and the CC data records.`)) return;
    setSyncing(siteCode);
    setSyncResult('');
    try {
      const result = await executeSyncSite(siteCode);
      setSyncResult(
        `Sync complete: ${result.matched} matched, ${result.cache_written} saved in CC, ${result.ugp_updated} pushed to uGridPLAN`
      );
      setPreview(null);
      fetchData();
    } catch (e: any) {
      setSyncResult(`Sync failed: ${e.message}`);
    } finally {
      setSyncing('');
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="animate-spin w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full" />
      </div>
    );
  }

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-6">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-gray-800">{t('sync:title')}</h1>
          <p className="text-sm text-gray-500 mt-1">
            {t('sync:subtitle')}
          </p>
        </div>
        {isSuperadmin && (
          <div className="flex gap-2">
            <button
              onClick={async () => {
                setDiscovering(true);
                setSyncResult('');
                try {
                  const r = await discoverProjects();
                  setSyncResult(
                    `Discovered ${r.discovered} uGridPLAN projects: ${r.matched_count} matched to sites` +
                    (r.unmatched_count > 0 ? `, ${r.unmatched_count} unmatched` : '')
                  );
                  fetchData();
                } catch (e: any) {
                  setSyncResult(`Discovery failed: ${e.message}`);
                } finally {
                  setDiscovering(false);
                }
              }}
              disabled={discovering}
              className="px-4 py-2 bg-amber-600 text-white text-sm rounded-lg hover:bg-amber-700 transition disabled:opacity-50"
            >
              {discovering ? t('sync:discovering') : t('sync:discoverProjects')}
            </button>
            <button
              onClick={() => setShowAddForm(!showAddForm)}
              className="px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 transition"
            >
              {showAddForm ? t('sync:cancel') : t('sync:addSite')}
            </button>
          </div>
        )}
      </div>

      {error && <div className="bg-red-50 text-red-700 px-4 py-3 rounded-lg mb-4 text-sm">{error}</div>}
      {syncResult && (
        <div className={`px-4 py-3 rounded-lg mb-4 text-sm ${syncResult.includes('failed') ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700'}`}>
          {syncResult}
        </div>
      )}

      {/* Add site form */}
      {showAddForm && (
        <div className="bg-white rounded-xl shadow p-4 sm:p-6 mb-6 border border-blue-200">
          <h3 className="font-semibold text-gray-700 mb-3">{t('sync:addMapping.title')}</h3>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-3">
            <input
              type="text"
              placeholder={t('sync:addMapping.siteCode')}
              value={newSiteCode}
              onChange={e => setNewSiteCode(e.target.value)}
              className="border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-300 focus:outline-none"
            />
            <input
              type="text"
              placeholder={t('sync:addMapping.projectName')}
              value={newProjectId}
              onChange={e => setNewProjectId(e.target.value)}
              className="border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-300 focus:outline-none"
            />
            <input
              type="text"
              placeholder={t('sync:addMapping.siteName')}
              value={newSiteName}
              onChange={e => setNewSiteName(e.target.value)}
              className="border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-300 focus:outline-none"
            />
          </div>
          <button
            onClick={handleAddSite}
            disabled={adding || !newSiteCode || !newProjectId}
            className="px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            {adding ? t('sync:addMapping.saving') : t('sync:addMapping.save')}
          </button>
        </div>
      )}

      {/* Status overview cards */}
      {status && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
          <div className="bg-white rounded-xl shadow p-4 border-l-4 border-blue-500">
            <p className="text-xs text-gray-500 uppercase">{t('sync:stats.configuredSites')}</p>
            <p className="text-2xl font-bold text-gray-800 mt-1">{status.sites.length}</p>
          </div>
          <div className="bg-white rounded-xl shadow p-4 border-l-4 border-green-500">
            <p className="text-xs text-gray-500 uppercase">{t('sync:stats.syncedCustomers')}</p>
            <p className="text-2xl font-bold text-gray-800 mt-1">{status.total_synced}</p>
          </div>
          <div className="bg-white rounded-xl shadow p-4 border-l-4 border-purple-500">
            <p className="text-xs text-gray-500 uppercase">{t('sync:stats.withType')}</p>
            <p className="text-2xl font-bold text-gray-800 mt-1">
              {status.type_distribution.reduce((a, d) => a + d.count, 0)}
            </p>
          </div>
          <div className="bg-white rounded-xl shadow p-4 border-l-4 border-amber-500">
            <p className="text-xs text-gray-500 uppercase">{t('sync:stats.customerTypes')}</p>
            <p className="text-2xl font-bold text-gray-800 mt-1">{status.type_distribution.length}</p>
          </div>
        </div>
      )}

      {/* Type distribution */}
      {status && status.type_distribution.length > 0 && (
        <div className="bg-white rounded-xl shadow p-4 mb-6">
          <h3 className="font-semibold text-gray-700 mb-2 text-sm">{t('sync:typeDistribution')}</h3>
          <div className="flex flex-wrap gap-2">
            {status.type_distribution.map(d => (
              <span key={d.type} className="px-3 py-1 bg-blue-50 text-blue-700 rounded-full text-sm font-medium">
                {d.type}: {d.count}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Sites table */}
      <div className="bg-white rounded-xl shadow overflow-hidden mb-6">
        <div className="px-4 py-3 border-b bg-gray-50">
          <h3 className="font-semibold text-gray-700">{t('sync:table.title')}</h3>
        </div>
        {sites.length === 0 ? (
          <div className="text-center py-8 text-gray-400">
            {t('sync:empty')}
          </div>
        ) : (
          <>
            {/* Desktop table */}
            <div className="hidden md:block">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 border-b">
                  <tr>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('sync:table.site')}</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('sync:table.projectName')}</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('sync:table.synced')}</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('sync:table.lastSync')}</th>
                    <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">{t('sync:table.actions')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {sites.map(s => (
                    <tr key={s.site_code} className="hover:bg-gray-50">
                      <td className="px-4 py-3">
                        <span className="font-semibold text-gray-800">{s.site_code}</span>
                        {s.site_name && <span className="text-gray-400 ml-2 text-xs">{s.site_name}</span>}
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-gray-500 truncate max-w-[200px]">{s.project_id}</td>
                      <td className="px-4 py-3 text-gray-700">{s.synced_count ?? 0}</td>
                      <td className="px-4 py-3 text-gray-500 text-xs">
                        {s.last_sync ? new Date(s.last_sync + 'Z').toLocaleString() : t('sync:table.never')}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className="flex justify-end gap-2">
                          <button
                            onClick={() => handlePreview(s.site_code)}
                            disabled={previewing && previewSite === s.site_code}
                            className="px-3 py-1.5 text-xs border rounded-lg hover:bg-gray-100 disabled:opacity-50"
                          >
                            {previewing && previewSite === s.site_code ? t('sync:table.loading') : t('sync:table.preview')}
                          </button>
                          <button
                            onClick={() => handleSync(s.site_code)}
                            disabled={syncing === s.site_code}
                            className="px-3 py-1.5 text-xs bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50"
                          >
                            {syncing === s.site_code ? t('sync:table.syncing') : t('sync:table.sync')}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Mobile cards */}
            <div className="md:hidden divide-y">
              {sites.map(s => (
                <div key={s.site_code} className="p-4">
                  <div className="flex items-center justify-between mb-2">
                    <div>
                      <span className="font-semibold text-gray-800">{s.site_code}</span>
                      {s.site_name && <span className="text-gray-400 ml-2 text-xs">{s.site_name}</span>}
                    </div>
                    <span className="text-xs text-gray-500">{t('sync:table.syncedCount', { count: s.synced_count ?? 0 })}</span>
                  </div>
                  <p className="font-mono text-xs text-gray-400 truncate mb-2">{s.project_id}</p>
                  <div className="flex gap-2">
                    <button
                      onClick={() => handlePreview(s.site_code)}
                      disabled={previewing && previewSite === s.site_code}
                      className="flex-1 px-3 py-2 text-xs border rounded-lg disabled:opacity-50"
                    >
                      {previewing && previewSite === s.site_code ? t('sync:table.loading') : t('sync:table.preview')}
                    </button>
                    <button
                      onClick={() => handleSync(s.site_code)}
                      disabled={syncing === s.site_code}
                      className="flex-1 px-3 py-2 text-xs bg-green-600 text-white rounded-lg disabled:opacity-50"
                    >
                      {syncing === s.site_code ? t('sync:table.syncing') : t('sync:table.sync')}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Preview results */}
      {preview && (
        <div className="bg-white rounded-xl shadow border border-blue-200 p-4 sm:p-6 mb-6">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-4">
            <h3 className="font-bold text-gray-800 text-lg">
              {t('sync:preview.title', { site: preview.site })}
            </h3>
            <button
              onClick={() => setPreview(null)}
              className="text-sm text-gray-500 hover:text-gray-700"
            >
              {t('sync:preview.close')}
            </button>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-4">
            <div className="bg-blue-50 rounded-lg p-3 text-center">
              <p className="text-2xl font-bold text-blue-700">{preview.ugp_connection_count}</p>
              <p className="text-xs text-blue-600">{t('sync:preview.ugpConnections')}</p>
            </div>
            <div className="bg-green-50 rounded-lg p-3 text-center">
              <p className="text-2xl font-bold text-green-700">{preview.cc_customer_count}</p>
              <p className="text-xs text-green-600">{t('sync:preview.ccCustomers')}</p>
            </div>
            <div className="bg-emerald-50 rounded-lg p-3 text-center">
              <p className="text-2xl font-bold text-emerald-700">{preview.matched_count}</p>
              <p className="text-xs text-emerald-600">{t('sync:preview.matched')}</p>
            </div>
            <div className="bg-amber-50 rounded-lg p-3 text-center">
              <p className="text-2xl font-bold text-amber-700">{preview.unmatched_ugp_count}</p>
              <p className="text-xs text-amber-600">{t('sync:preview.unmatchedUgp')}</p>
            </div>
            <div className="bg-red-50 rounded-lg p-3 text-center">
              <p className="text-2xl font-bold text-red-700">{preview.unmatched_cc_count}</p>
              <p className="text-xs text-red-600">{t('sync:preview.unmatchedCc')}</p>
            </div>
          </div>

          {/* Matched records */}
          {preview.matched.length > 0 && (
            <div className="mb-4">
              <h4 className="text-sm font-semibold text-gray-600 mb-2">{t('sync:preview.matchedRecords')} ({preview.matched.length})</h4>
              <div className="overflow-x-auto max-h-64 overflow-y-auto border rounded-lg">
                <table className="min-w-full text-xs">
                  <thead className="bg-gray-50 border-b sticky top-0">
                    <tr>
                      <th className="px-3 py-2 text-left">{t('sync:preview.surveyId')}</th>
                      <th className="px-3 py-2 text-left">{t('sync:preview.customerId')}</th>
                      <th className="px-3 py-2 text-left">{t('sync:preview.method')}</th>
                      <th className="px-3 py-2 text-left">{t('sync:preview.type')}</th>
                      <th className="px-3 py-2 text-left">{t('sync:preview.nameCc')}</th>
                      <th className="px-3 py-2 text-left">{t('sync:preview.meter')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {preview.matched.map((m, i) => (
                      <tr key={i} className="hover:bg-gray-50">
                        <td className="px-3 py-1.5 font-mono">{m.survey_id}</td>
                        <td className="px-3 py-1.5 font-mono">{m.customer_id}</td>
                        <td className="px-3 py-1.5">
                          <span className="px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded text-xs">{m.match_method}</span>
                        </td>
                        <td className="px-3 py-1.5 font-semibold">{m.customer_type || '—'}</td>
                        <td className="px-3 py-1.5">{m.cc_name}</td>
                        <td className="px-3 py-1.5 font-mono">{m.meter_serial || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <button
            onClick={() => handleSync(preview.site)}
            disabled={syncing === preview.site || preview.matched_count === 0}
            className="px-5 py-2.5 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 font-medium"
          >
            {syncing === preview.site ? t('sync:preview.syncing') : t('sync:preview.applySync', { count: preview.matched_count })}
          </button>
        </div>
      )}
    </div>
  );
}
