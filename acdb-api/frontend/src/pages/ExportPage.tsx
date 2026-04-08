import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { listTables, exportUrl, type TableInfo } from '../lib/api';

export default function ExportPage() {
  const { t } = useTranslation(['export', 'common']);
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [selected, setSelected] = useState('');
  const [format, setFormat] = useState<'csv' | 'xlsx'>('csv');
  const [search, setSearch] = useState('');

  useEffect(() => {
    listTables().then(setTables).catch(() => {});
  }, []);

  const handleExport = () => {
    if (!selected) return;
    const url = exportUrl(selected, format, search || undefined);
    const token = localStorage.getItem('cc_token');
    fetch(url.replace(/token=[^&]*/, ''), {
      headers: { Authorization: `Bearer ${token || ''}` },
    })
      .then(res => res.blob())
      .then(blob => {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `${selected}.${format}`;
        a.click();
        URL.revokeObjectURL(a.href);
      })
      .catch(err => alert(t('export:exportFailed', { error: err.message })));
  };

  return (
    <div className="space-y-6">
      <h1 className="text-xl sm:text-2xl font-bold text-gray-800">{t('export:title')}</h1>

      <div className="bg-white rounded-lg shadow p-4 sm:p-6 max-w-lg space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">{t('export:table')}</label>
          <select
            value={selected}
            onChange={e => setSelected(e.target.value)}
            className="w-full px-3 py-2 border rounded-lg text-sm bg-white"
          >
            <option value="">{t('export:selectTable')}</option>
            {tables.map(tbl => (
              <option key={tbl.name} value={tbl.name}>{tbl.name} ({tbl.row_count.toLocaleString()} rows)</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">{t('export:format')}</label>
          <div className="flex gap-3">
            <label className="flex items-center gap-2 text-sm">
              <input type="radio" value="csv" checked={format === 'csv'} onChange={() => setFormat('csv')} /> {t('export:csv')}
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="radio" value="xlsx" checked={format === 'xlsx'} onChange={() => setFormat('xlsx')} /> {t('export:xlsx')}
            </label>
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">{t('export:filter')}</label>
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder={t('export:filterPlaceholder')}
            className="w-full px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none"
          />
        </div>

        <button
          onClick={handleExport}
          disabled={!selected}
          className="w-full py-2.5 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 transition"
        >
          {t('export:download', { format: format.toUpperCase() })}
        </button>
      </div>
    </div>
  );
}
