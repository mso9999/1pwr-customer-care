import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { listTables, type TableInfo } from '../lib/api';

export default function TablesPage() {
  const { t } = useTranslation(['tables', 'common']);
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listTables().then(setTables).catch(() => {}).finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-center py-16 text-gray-400">{t('tables:loading')}</div>;

  return (
    <div className="space-y-4">
      <h1 className="text-xl sm:text-2xl font-bold text-gray-800">{t('tables:title')}</h1>
      <p className="text-gray-500 text-sm">{t('tables:subtitle')}</p>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 sm:gap-3">
        {tables.map(tbl => (
          <Link key={tbl.name} to={`/tables/${tbl.name}`} className="bg-white rounded-lg shadow p-3 sm:p-4 hover:shadow-md active:bg-gray-50 transition border hover:border-blue-300">
            <div className="font-semibold text-gray-800 truncate">{tbl.name}</div>
            <div className="text-sm text-gray-500 mt-0.5">{t('tables:rowsCols', { rows: tbl.row_count.toLocaleString(), cols: tbl.column_count })}</div>
          </Link>
        ))}
      </div>
    </div>
  );
}
