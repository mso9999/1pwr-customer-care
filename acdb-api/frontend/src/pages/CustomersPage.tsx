import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { listRows, type PaginatedResponse } from '../lib/api';
import { useAuth } from '../contexts/AuthContext';

export default function CustomersPage() {
  const [data, setData] = useState<PaginatedResponse | null>(null);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [filterSite, setFilterSite] = useState('');
  const [sites, setSites] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const { canWrite } = useAuth();

  useEffect(() => {
    fetch('/api/sites').then(r => r.json()).then(d => {
      setSites((d.sites || []).map((s: any) => s.concession));
    }).catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    listRows('tblcustomer', {
      page,
      limit: 50,
      search: search || undefined,
      filter_col: filterSite ? 'Concession name' : undefined,
      filter_val: filterSite || undefined,
    })
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [page, search, filterSite]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    setSearch(searchInput);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl sm:text-2xl font-bold text-gray-800">Customers</h1>
        {canWrite && (
          <div className="flex gap-2">
            <Link to="/assign-meter" className="px-4 py-2.5 bg-emerald-600 text-white rounded-xl text-sm font-medium hover:bg-emerald-700 active:bg-emerald-800 transition flex items-center gap-1.5">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
              Assign Meter
            </Link>
            <Link to="/customers/new" className="px-4 py-2.5 bg-blue-600 text-white rounded-xl text-sm font-medium hover:bg-blue-700 active:bg-blue-800 transition flex items-center gap-1.5">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" /></svg>
              Add Customer
            </Link>
          </div>
        )}
      </div>

      {/* Filters */}
      <div className="space-y-2 sm:space-y-0 sm:flex sm:gap-3 sm:flex-wrap">
        <form onSubmit={handleSearch} className="flex gap-2">
          <input
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
            placeholder="Search name, ID, plot..."
            className="flex-1 sm:w-64 px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none"
          />
          <button type="submit" className="px-3 py-2 bg-gray-100 border rounded-lg text-sm hover:bg-gray-200 whitespace-nowrap">Search</button>
        </form>
        <select
          value={filterSite}
          onChange={e => { setFilterSite(e.target.value); setPage(1); }}
          className="w-full sm:w-auto px-3 py-2 border rounded-lg text-sm bg-white"
        >
          <option value="">All Sites</option>
          {sites.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      {/* Content */}
      {loading ? (
        <div className="text-center py-8 text-gray-400">Loading...</div>
      ) : !data || data.rows.length === 0 ? (
        <div className="text-center py-8 text-gray-400">No customers found</div>
      ) : (
        <>
          {/* Desktop table */}
          <div className="hidden md:block bg-white rounded-lg shadow overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Customer ID</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Name</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Phone</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Site</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">District</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {data.rows.map((row, i) => {
                  const cid = String(row['CUSTOMER ID'] || '');
                  const name = [row['FIRST NAME'], row['LAST NAME']].filter(Boolean).join(' ');
                  const phone = String(row['PHONE'] || row['CELL PHONE 1'] || '');
                  const site = String(row['Concession name'] || '');
                  const district = String(row['DISTRICT'] || '');
                  const terminated = row['DATE SERVICE TERMINATED'];
                  return (
                    <tr key={i} className="hover:bg-gray-50">
                      <td className="px-4 py-2">
                        <Link to={`/customers/${cid}`} className="text-blue-600 hover:underline font-medium">{cid}</Link>
                      </td>
                      <td className="px-4 py-2">{name}</td>
                      <td className="px-4 py-2 text-gray-500">{phone}</td>
                      <td className="px-4 py-2">{site}</td>
                      <td className="px-4 py-2 text-gray-500">{district}</td>
                      <td className="px-4 py-2">
                        {terminated ? (
                          <span className="px-2 py-0.5 bg-red-100 text-red-700 text-xs rounded-full">Terminated</span>
                        ) : (
                          <span className="px-2 py-0.5 bg-green-100 text-green-700 text-xs rounded-full">Active</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Mobile card list */}
          <div className="md:hidden space-y-2">
            {data.rows.map((row, i) => {
              const cid = String(row['CUSTOMER ID'] || '');
              const name = [row['FIRST NAME'], row['LAST NAME']].filter(Boolean).join(' ');
              const phone = String(row['PHONE'] || row['CELL PHONE 1'] || '');
              const site = String(row['Concession name'] || '');
              const terminated = row['DATE SERVICE TERMINATED'];
              return (
                <Link key={i} to={`/customers/${cid}`} className="block bg-white rounded-lg shadow p-4 active:bg-gray-50">
                  <div className="flex items-start justify-between">
                    <div className="min-w-0">
                      <p className="font-medium text-blue-700 text-sm">{cid}</p>
                      <p className="text-gray-800 font-medium truncate">{name || '--'}</p>
                      <p className="text-gray-500 text-sm">{phone}</p>
                    </div>
                    <div className="flex flex-col items-end gap-1 ml-3 shrink-0">
                      {terminated ? (
                        <span className="px-2 py-0.5 bg-red-100 text-red-700 text-xs rounded-full">Terminated</span>
                      ) : (
                        <span className="px-2 py-0.5 bg-green-100 text-green-700 text-xs rounded-full">Active</span>
                      )}
                      <span className="text-xs text-gray-400">{site}</span>
                    </div>
                  </div>
                </Link>
              );
            })}
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between text-sm text-gray-500">
            <span className="text-xs sm:text-sm">Page {data.page}/{data.pages} ({data.total.toLocaleString()})</span>
            <div className="flex gap-2">
              <button
                disabled={page <= 1}
                onClick={() => setPage(p => p - 1)}
                className="px-3 py-1.5 border rounded disabled:opacity-30 hover:bg-gray-100 text-sm"
              >Prev</button>
              <button
                disabled={page >= data.pages}
                onClick={() => setPage(p => p + 1)}
                className="px-3 py-1.5 border rounded disabled:opacity-30 hover:bg-gray-100 text-sm"
              >Next</button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
