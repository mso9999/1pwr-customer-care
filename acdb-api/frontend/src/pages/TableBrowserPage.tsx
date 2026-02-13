import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { listRows, listColumns, type PaginatedResponse, type ColumnInfo } from '../lib/api';

export default function TableBrowserPage() {
  const { name } = useParams<{ name: string }>();
  const [data, setData] = useState<PaginatedResponse | null>(null);
  const [columns, setColumns] = useState<ColumnInfo[]>([]);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!name) return;
    listColumns(name).then(setColumns).catch(() => {});
  }, [name]);

  useEffect(() => {
    if (!name) return;
    setLoading(true);
    listRows(name, { page, limit: 50, search: search || undefined })
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [name, page, search]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    setSearch(searchInput);
  };

  // Pick display columns: fewer on mobile (first 4), more on desktop (first 8)
  const displayCols = columns.slice(0, 8).map(c => c.name);
  const mobileDisplayCols = columns.slice(0, 3).map(c => c.name);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
        <Link to="/tables" className="text-blue-600 hover:underline text-sm">&larr; Tables</Link>
        <h1 className="text-xl sm:text-2xl font-bold text-gray-800 truncate">{name}</h1>
        {data && <span className="text-xs sm:text-sm text-gray-400">{data.total.toLocaleString()} rows</span>}
      </div>

      {/* Column info */}
      {columns.length > 0 && (
        <details className="bg-white rounded-lg shadow p-3 sm:p-4">
          <summary className="cursor-pointer text-sm font-medium text-gray-600">Schema ({columns.length} columns)</summary>
          <div className="mt-2 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 text-xs">
            {columns.map(c => (
              <div key={c.name} className="p-2 bg-gray-50 rounded">
                <div className="font-medium truncate">{c.name}</div>
                <div className="text-gray-400">{c.type_name}{c.size ? `(${c.size})` : ''} {c.nullable ? '' : 'NOT NULL'}</div>
              </div>
            ))}
          </div>
        </details>
      )}

      {/* Search */}
      <form onSubmit={handleSearch} className="flex gap-2">
        <input
          value={searchInput}
          onChange={e => setSearchInput(e.target.value)}
          placeholder="Search..."
          className="flex-1 sm:flex-none sm:w-64 px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none"
        />
        <button type="submit" className="px-3 py-2 bg-gray-100 border rounded-lg text-sm hover:bg-gray-200">Search</button>
        {search && <button type="button" onClick={() => { setSearch(''); setSearchInput(''); setPage(1); }} className="px-2 py-2 text-sm text-gray-500 hover:text-red-600">Clear</button>}
      </form>

      {/* Data */}
      {loading ? (
        <div className="text-center py-8 text-gray-400">Loading...</div>
      ) : !data || data.rows.length === 0 ? (
        <div className="text-center py-8 text-gray-400">No rows found</div>
      ) : (
        <>
          {/* Desktop table */}
          <div className="hidden sm:block bg-white rounded-lg shadow overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  {displayCols.map(c => (
                    <th key={c} className="px-3 py-2 text-left font-medium text-gray-600 whitespace-nowrap text-xs">{c}</th>
                  ))}
                  {columns.length > 8 && <th className="px-3 py-2 text-gray-400 text-xs">...</th>}
                </tr>
              </thead>
              <tbody className="divide-y">
                {data.rows.map((row, i) => (
                  <tr key={i} className="hover:bg-gray-50">
                    {displayCols.map(c => (
                      <td key={c} className="px-3 py-2 max-w-[180px] truncate text-gray-700 text-xs">
                        {row[c] != null ? String(row[c]) : <span className="text-gray-300">null</span>}
                      </td>
                    ))}
                    {columns.length > 8 && <td className="px-3 py-2 text-gray-300 text-xs">...</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile card list */}
          <div className="sm:hidden space-y-2">
            {data.rows.map((row, i) => (
              <div key={i} className="bg-white rounded-lg shadow p-3 text-sm">
                {mobileDisplayCols.map(c => (
                  <div key={c} className="flex justify-between py-0.5">
                    <span className="text-gray-500 text-xs font-medium truncate mr-2">{c}</span>
                    <span className="text-gray-800 text-xs text-right truncate max-w-[60%]">
                      {row[c] != null ? String(row[c]) : <span className="text-gray-300">null</span>}
                    </span>
                  </div>
                ))}
                {columns.length > 3 && (
                  <p className="text-xs text-gray-400 mt-1 text-center">+{columns.length - 3} more fields</p>
                )}
              </div>
            ))}
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between text-sm text-gray-500">
            <span className="text-xs sm:text-sm">Page {data.page}/{data.pages}</span>
            <div className="flex gap-2">
              <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="px-3 py-1.5 border rounded disabled:opacity-30 hover:bg-gray-100 text-sm">Prev</button>
              <button disabled={page >= data.pages} onClick={() => setPage(p => p + 1)} className="px-3 py-1.5 border rounded disabled:opacity-30 hover:bg-gray-100 text-sm">Next</button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
