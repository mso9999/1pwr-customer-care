import { useState, useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { createRecord, getRecord, listRows, listSites } from '../lib/api';

// ---------------------------------------------------------------------------
// Types & constants
// ---------------------------------------------------------------------------

const CUSTOMER_TYPES = ['HH', 'SME', 'CHU', 'SCP', 'SCH', 'GOV', 'COM', 'IND'];

interface SiteOption {
  code: string;
  label: string;
}

// ---------------------------------------------------------------------------
// GPS capture (reused from NewCustomerWizard)
// ---------------------------------------------------------------------------

function GPSCapture({ lat, lng, onChange }: { lat: string; lng: string; onChange: (lat: string, lng: string) => void }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const captureGPS = () => {
    if (!navigator.geolocation) { setError('Geolocation not supported'); return; }
    setLoading(true);
    setError('');
    navigator.geolocation.getCurrentPosition(
      (pos) => { onChange(pos.coords.latitude.toFixed(6), pos.coords.longitude.toFixed(6)); setLoading(false); },
      (err) => { setError(err.message); setLoading(false); },
      { enableHighAccuracy: true, timeout: 15000 },
    );
  };

  return (
    <div className="space-y-3">
      <div className="flex gap-3">
        <div className="flex-1">
          <label className="block text-xs text-gray-500 mb-1">Latitude</label>
          <input type="text" value={lat} onChange={e => onChange(e.target.value, lng)} placeholder="-29.3..."
            className="w-full px-4 py-3 border border-gray-300 rounded-xl text-base focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none" />
        </div>
        <div className="flex-1">
          <label className="block text-xs text-gray-500 mb-1">Longitude</label>
          <input type="text" value={lng} onChange={e => onChange(lat, e.target.value)} placeholder="28.5..."
            className="w-full px-4 py-3 border border-gray-300 rounded-xl text-base focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none" />
        </div>
      </div>
      <button type="button" onClick={captureGPS} disabled={loading}
        className="w-full py-3 bg-gray-100 border-2 border-dashed border-gray-300 rounded-xl text-sm font-medium text-gray-600 hover:bg-gray-200 active:bg-gray-300 disabled:opacity-50 transition flex items-center justify-center gap-2">
        {loading ? (
          <><span className="animate-spin inline-block w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full" /> Acquiring GPS...</>
        ) : (
          <><svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg> Capture Current Location</>
        )}
      </button>
      {error && <p className="text-red-500 text-xs">{error}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Next account number helper
// ---------------------------------------------------------------------------

async function getNextAccountNumber(siteCode: string): Promise<string> {
  // Query meters table for highest account number in this community
  try {
    const resp = await listRows('meters', {
      filter_col: 'community',
      filter_val: siteCode,
      sort: 'accountnumber',
      order: 'desc',
      limit: 1,
    });
    if (resp.rows.length > 0) {
      const acct = String(resp.rows[0].accountnumber || '');
      // Account number format: NNNNXXX -- extract the numeric prefix
      const numPart = acct.replace(/[A-Za-z]+$/, '');
      const next = (parseInt(numPart, 10) || 0) + 1;
      return String(next).padStart(4, '0') + siteCode.toUpperCase();
    }
  } catch { /* ignore */ }

  // First meter for this site
  return '0001' + siteCode.toUpperCase();
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function AssignMeterPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const prefilledCustomerId = searchParams.get('customer') || '';

  // Form state
  const [customerId, setCustomerId] = useState(prefilledCustomerId);
  const [meterid, setMeterid] = useState('');
  const [community, setCommunity] = useState('');
  const [customerType, setCustomerType] = useState('');
  const [villageName, setVillageName] = useState('');
  const [latitude, setLatitude] = useState('');
  const [longitude, setLongitude] = useState('');
  const [connectDate, setConnectDate] = useState(new Date().toISOString().slice(0, 10));

  // Account number (auto-generated or overridden)
  const [accountNumber, setAccountNumber] = useState('');
  const [acctLoading, setAcctLoading] = useState(false);

  // Site options
  const [sites, setSites] = useState<SiteOption[]>([]);

  // UI state
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  // Customer info preview
  const [customerName, setCustomerName] = useState('');
  const [customerLoading, setCustomerLoading] = useState(false);

  // Load sites
  useEffect(() => {
    listSites()
      .then(d => {
        const fetched = (d.sites || []).map(s => ({
          code: s.concession,
          label: `${s.concession} (${s.customer_count} customers)`,
        })).filter(s => s.code);
        setSites(fetched);
      })
      .catch(() => {});
  }, []);

  // Auto-generate account number when community changes
  useEffect(() => {
    if (!community) { setAccountNumber(''); return; }
    let cancelled = false;
    setAcctLoading(true);
    getNextAccountNumber(community).then(acct => {
      if (!cancelled) { setAccountNumber(acct); setAcctLoading(false); }
    });
    return () => { cancelled = true; };
  }, [community]);

  // Lookup customer name when ID changes
  useEffect(() => {
    if (!customerId.trim()) { setCustomerName(''); return; }
    let cancelled = false;
    setCustomerLoading(true);
    const timer = setTimeout(() => {
      getRecord('customers', customerId.trim())
        .then(({ record }) => {
          if (!cancelled) {
            const first = record['FIRST NAME'] || '';
            const last = record['LAST NAME'] || '';
            setCustomerName(`${first} ${last}`.trim() || `Customer #${customerId}`);

            // Auto-fill community from customer's concession if not set
            const conc = String(record['Concession name'] || '');
            if (conc && !community) setCommunity(conc);

            // Auto-fill GPS if available
            const gx = String(record['GPS X'] || '');
            const gy = String(record['GPS Y'] || '');
            if (gy && !latitude) setLatitude(gy);
            if (gx && !longitude) setLongitude(gx);
          }
        })
        .catch(() => { if (!cancelled) setCustomerName(''); })
        .finally(() => { if (!cancelled) setCustomerLoading(false); });
    }, 500); // debounce
    return () => { cancelled = true; clearTimeout(timer); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customerId]);

  // Submit
  const handleSubmit = async () => {
    // Validate
    if (!customerId.trim()) { setError('Customer ID is required'); return; }
    if (!meterid.trim()) { setError('Meter ID (serial) is required'); return; }
    if (!community) { setError('Site / Community is required'); return; }
    if (!customerType) { setError('Customer type is required'); return; }
    if (!accountNumber.trim()) { setError('Account number is required'); return; }

    setSaving(true);
    setError('');
    setSuccess('');

    const now = new Date().toISOString().slice(0, 19).replace('T', ' ');

    try {
      // 1. Insert into tblmeter
      const meterData: Record<string, unknown> = {
        'meterid': meterid.trim(),
        'community': community.toUpperCase(),
        'customer id': parseInt(customerId, 10),
        'accountnumber': accountNumber.trim(),
        'customer type': customerType,
        'customer connect date': connectDate || now,
        'RECORD CREATE DATE': now,
        'RECORD CREATED BY': 'CC Portal',
      };
      if (villageName.trim()) meterData['Village name'] = villageName.trim();
      if (latitude.trim()) meterData['latitude'] = latitude.trim();
      if (longitude.trim()) meterData['longitude'] = longitude.trim();

      await createRecord('meters', meterData);

      // 2. Insert into tblaccountnumbers
      const acctData: Record<string, unknown> = {
        'accountnumber': accountNumber.trim(),
        'meterid': meterid.trim(),
        'customerid': parseInt(customerId, 10),
        'community': community.toUpperCase(),
        'opened date': connectDate || now,
        'created by': 'CC Portal',
      };

      await createRecord('accounts', acctData);

      setSuccess(`Meter ${meterid} assigned to customer ${customerId} with account ${accountNumber}`);

    } catch (e: any) {
      setError(e.message || 'Failed to assign meter');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="max-w-lg mx-auto pb-8">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <button
          onClick={() => navigate(-1)}
          className="p-2 -ml-2 rounded-lg hover:bg-gray-100 active:bg-gray-200 transition"
          aria-label="Go back"
        >
          <svg className="w-6 h-6 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <div>
          <h1 className="text-xl font-bold text-gray-800">Assign Meter</h1>
          <p className="text-sm text-gray-400">Link a meter and account to a customer</p>
        </div>
      </div>

      {/* Success banner with commission option */}
      {success && (
        <div className="mb-4 space-y-3">
          <div className="p-4 bg-green-50 border border-green-200 rounded-xl text-green-700 text-sm flex items-center gap-2">
            <svg className="w-5 h-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
            {success}
          </div>
          <div className="flex gap-3">
            <button
              onClick={() => navigate(`/commission?customer=${customerId}&account=${accountNumber}`)}
              className="flex-1 py-3.5 bg-green-600 text-white rounded-xl font-semibold text-sm hover:bg-green-700 active:bg-green-800 transition"
            >
              Commission Customer Now
            </button>
            <button
              onClick={() => navigate(`/customers/${customerId}`)}
              className="flex-1 py-3.5 bg-gray-100 text-gray-700 rounded-xl font-medium text-sm hover:bg-gray-200 active:bg-gray-300 transition"
            >
              Done
            </button>
          </div>
        </div>
      )}

      {/* Form card */}
      <div className="bg-white rounded-2xl shadow-sm border p-5 sm:p-6 space-y-5">

        {/* Customer ID */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Customer ID <span className="text-red-400">*</span>
          </label>
          <input
            type="text"
            value={customerId}
            onChange={e => setCustomerId(e.target.value)}
            placeholder="e.g. 45"
            className="w-full px-4 py-3.5 border border-gray-300 rounded-xl text-base focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none"
          />
          {customerLoading && <p className="text-xs text-gray-400 mt-1">Looking up customer...</p>}
          {customerName && !customerLoading && (
            <p className="text-xs text-green-600 mt-1 flex items-center gap-1">
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              {customerName}
            </p>
          )}
        </div>

        {/* Meter ID (serial) */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Meter Serial Number <span className="text-red-400">*</span>
          </label>
          <input
            type="text"
            value={meterid}
            onChange={e => setMeterid(e.target.value)}
            placeholder="e.g. SMRSD-26-00000000"
            className="w-full px-4 py-3.5 border border-gray-300 rounded-xl text-base focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none"
          />
        </div>

        {/* Site / Community */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Site (Community) <span className="text-red-400">*</span>
          </label>
          <select
            value={community}
            onChange={e => setCommunity(e.target.value)}
            className="w-full px-4 py-3.5 border border-gray-300 rounded-xl text-base bg-white focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none appearance-none"
          >
            <option value="">Select site...</option>
            {sites.map(s => <option key={s.code} value={s.code}>{s.label}</option>)}
          </select>
        </div>

        {/* Customer Type */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Customer Type <span className="text-red-400">*</span>
          </label>
          <select
            value={customerType}
            onChange={e => setCustomerType(e.target.value)}
            className="w-full px-4 py-3.5 border border-gray-300 rounded-xl text-base bg-white focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none appearance-none"
          >
            <option value="">Select type...</option>
            {CUSTOMER_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>

        {/* Account Number (auto-generated) */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Account Number <span className="text-red-400">*</span>
          </label>
          <div className="relative">
            <input
              type="text"
              value={accountNumber}
              onChange={e => setAccountNumber(e.target.value)}
              placeholder="Auto-generated..."
              className="w-full px-4 py-3.5 border border-gray-300 rounded-xl text-base focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none"
            />
            {acctLoading && (
              <span className="absolute right-3 top-1/2 -translate-y-1/2 animate-spin w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full" />
            )}
          </div>
          <p className="text-xs text-gray-400 mt-1">Auto-generated from site. You can override if needed.</p>
        </div>

        {/* Village Name */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">Village Name</label>
          <input
            type="text"
            value={villageName}
            onChange={e => setVillageName(e.target.value)}
            placeholder="e.g. Ha Makebe"
            className="w-full px-4 py-3.5 border border-gray-300 rounded-xl text-base focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none"
          />
        </div>

        {/* Connection Date */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">Connection Date</label>
          <input
            type="date"
            value={connectDate}
            onChange={e => setConnectDate(e.target.value)}
            className="w-full px-4 py-3.5 border border-gray-300 rounded-xl text-base focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none"
          />
        </div>

        {/* GPS */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">GPS Coordinates</label>
          <GPSCapture
            lat={latitude}
            lng={longitude}
            onChange={(lat, lng) => { setLatitude(lat); setLongitude(lng); }}
          />
        </div>

        {/* Error */}
        {error && (
          <div className="p-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">
            {error}
          </div>
        )}
      </div>

      {/* Submit button */}
      <button
        onClick={handleSubmit}
        disabled={saving}
        className="w-full mt-6 py-4 bg-blue-600 text-white rounded-xl font-semibold text-base hover:bg-blue-700 active:bg-blue-800 disabled:opacity-50 transition"
      >
        {saving ? (
          <span className="flex items-center justify-center gap-2">
            <span className="animate-spin inline-block w-5 h-5 border-2 border-white border-t-transparent rounded-full" />
            Assigning...
          </span>
        ) : 'Assign Meter'}
      </button>
    </div>
  );
}
