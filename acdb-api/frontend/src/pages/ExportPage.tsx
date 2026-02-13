import { useEffect, useState } from 'react';
import { listTables, exportUrl, type TableInfo } from '../lib/api';

export default function ExportPage() {
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
    // Download via anchor tag with auth header (open in new tab for simplicity)
    const token = localStorage.getItem('cc_token');
    // Create a hidden form or use fetch for authenticated download
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
      .catch(err => alert(`Export failed: ${err.message}`));
  };

  return (
    <div className="space-y-6">
      <h1 className="text-xl sm:text-2xl font-bold text-gray-800">Export Data</h1>

      <div className="bg-white rounded-lg shadow p-4 sm:p-6 max-w-lg space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Table</label>
          <select
            value={selected}
            onChange={e => setSelected(e.target.value)}
            className="w-full px-3 py-2 border rounded-lg text-sm bg-white"
          >
            <option value="">Select a table...</option>
            {tables.map(t => (
              <option key={t.name} value={t.name}>{t.name} ({t.row_count.toLocaleString()} rows)</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Format</label>
          <div className="flex gap-3">
            <label className="flex items-center gap-2 text-sm">
              <input type="radio" value="csv" checked={format === 'csv'} onChange={() => setFormat('csv')} /> CSV
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="radio" value="xlsx" checked={format === 'xlsx'} onChange={() => setFormat('xlsx')} /> Excel (XLSX)
            </label>
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Filter (optional)</label>
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search text to filter rows..."
            className="w-full px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none"
          />
        </div>

        <button
          onClick={handleExport}
          disabled={!selected}
          className="w-full py-2.5 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 transition"
        >
          Download {format.toUpperCase()}
        </button>
      </div>
    </div>
  );
}
