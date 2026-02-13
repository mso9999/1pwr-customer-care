import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { listTables, type TableInfo } from '../lib/api';

export default function TablesPage() {
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listTables().then(setTables).catch(() => {}).finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-center py-16 text-gray-400">Loading tables...</div>;

  return (
    <div className="space-y-4">
      <h1 className="text-xl sm:text-2xl font-bold text-gray-800">Database Tables</h1>
      <p className="text-gray-500 text-sm">All tables in the Access database. Tap to browse.</p>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 sm:gap-3">
        {tables.map(t => (
          <Link key={t.name} to={`/tables/${t.name}`} className="bg-white rounded-lg shadow p-3 sm:p-4 hover:shadow-md active:bg-gray-50 transition border hover:border-blue-300">
            <div className="font-semibold text-gray-800 truncate">{t.name}</div>
            <div className="text-sm text-gray-500 mt-0.5">{t.row_count.toLocaleString()} rows &middot; {t.column_count} columns</div>
          </Link>
        ))}
      </div>
    </div>
  );
}
