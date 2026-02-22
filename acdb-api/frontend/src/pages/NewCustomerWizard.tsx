import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { createRecord, listUGPConnections, type UGPConnection } from '../lib/api';

// ---------------------------------------------------------------------------
// Wizard step definitions
// ---------------------------------------------------------------------------

interface FieldDef {
  key: string;
  label: string;
  type?: 'text' | 'tel' | 'date' | 'select' | 'gps' | 'ugp_picker';
  placeholder?: string;
  required?: boolean;
  options?: string[];
  half?: boolean;
}

const CUSTOMER_TYPES = ['HH', 'SME', 'CHU', 'SCP', 'SCH', 'GOV', 'COM', 'IND'];

const steps: { title: string; description: string; fields: FieldDef[] }[] = [
  {
    title: 'Personal Information',
    description: 'Customer name and contact details',
    fields: [
      { key: 'first_name', label: 'First Name', required: true, half: true },
      { key: 'last_name', label: 'Last Name', required: true, half: true },
      { key: 'middle_name', label: 'Middle Name', half: true },
      { key: 'gender', label: 'Gender', type: 'select', options: ['Male', 'Female'], half: true },
      { key: 'national_id', label: 'National ID Number', placeholder: 'ID / Passport number' },
      { key: 'phone', label: 'Phone', type: 'tel', placeholder: '+266 ...', half: true },
      { key: 'cell_phone_1', label: 'Cell Phone', type: 'tel', placeholder: '+266 ...', half: true },
    ],
  },
  {
    title: 'Location',
    description: 'Pick from uGridPlan or enter manually',
    fields: [
      { key: '_ugp_picker', label: 'Import from uGridPlan', type: 'ugp_picker' },
      { key: 'community', label: 'Site (Concession)', type: 'select', options: [], required: true },
      { key: 'district', label: 'District', placeholder: 'e.g. Mafeteng' },
      { key: 'plot_number', label: 'Plot / Stand Number', placeholder: 'e.g. MAK 0001 HH' },
      { key: 'street_address', label: 'Village / Street Address', placeholder: 'Village or street name' },
      { key: 'GPS', label: 'GPS Coordinates', type: 'gps' },
    ],
  },
  {
    title: 'Service Details',
    description: 'Connection and metering information',
    fields: [
      { key: 'customer_position', label: 'Customer Type', type: 'select', options: CUSTOMER_TYPES, required: true },
      { key: 'date_service_connected', label: 'Date Connected', type: 'date' },
    ],
  },
];

const TOTAL_STEPS = steps.length + 1;

// ---------------------------------------------------------------------------
// uGridPlan Connection Picker Modal
// ---------------------------------------------------------------------------

interface UGPPickerProps {
  sites: string[];
  onSelect: (conn: UGPConnection, site: string) => void;
  onClose: () => void;
}

function UGPConnectionPicker({ sites, onSelect, onClose }: UGPPickerProps) {
  const [site, setSite] = useState('');
  const [connections, setConnections] = useState<UGPConnection[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');

  useEffect(() => {
    if (!site) { setConnections([]); return; }
    setLoading(true);
    setError('');
    listUGPConnections(site)
      .then(d => setConnections(d.connections || []))
      .catch(e => setError(e.message || 'Failed to load connections'))
      .finally(() => setLoading(false));
  }, [site]);

  const filtered = useMemo(() => {
    if (!search.trim()) return connections;
    const q = search.toLowerCase();
    return connections.filter(c =>
      c.survey_id.toLowerCase().includes(q) ||
      c.customer_type.toLowerCase().includes(q) ||
      c.customer_code.toLowerCase().includes(q) ||
      c.status.toLowerCase().includes(q)
    );
  }, [connections, search]);

  const unassigned = useMemo(
    () => filtered.filter(c => !c.customer_code),
    [filtered],
  );
  const assigned = useMemo(
    () => filtered.filter(c => !!c.customer_code),
    [filtered],
  );

  const renderRow = (c: UGPConnection) => {
    const hasGps = c.gps_lat != null && c.gps_lon != null;
    return (
      <button
        key={c.survey_id}
        onClick={() => onSelect(c, site)}
        className="w-full text-left px-4 py-3 hover:bg-blue-50 active:bg-blue-100 transition flex items-center gap-3"
      >
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-800 truncate">{c.survey_id}</p>
          <div className="flex gap-2 mt-0.5 text-xs text-gray-500">
            {c.customer_type && <span className="px-1.5 py-0.5 bg-gray-100 rounded">{c.customer_type}</span>}
            {c.customer_code && <span className="text-blue-600">Code: {c.customer_code}</span>}
            {hasGps && (
              <span className="text-green-600">
                {c.gps_lat!.toFixed(4)}, {c.gps_lon!.toFixed(4)}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {c.status && (
            <span className={`px-2 py-0.5 text-xs rounded-full font-medium ${
              c.status.toLowerCase().includes('commission')
                ? 'bg-green-100 text-green-700'
                : 'bg-gray-100 text-gray-600'
            }`}>
              {c.status}
            </span>
          )}
          <svg className="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
        </div>
      </button>
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="bg-white w-full sm:max-w-lg sm:rounded-2xl rounded-t-2xl shadow-xl max-h-[85vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-5 pt-5 pb-3 border-b shrink-0">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <svg className="w-5 h-5 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
              </svg>
              <h3 className="text-lg font-semibold text-gray-800">uGridPlan Connections</h3>
            </div>
            <button onClick={onClose} className="p-1.5 hover:bg-gray-100 rounded-lg transition">
              <svg className="w-5 h-5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          {/* Site selector */}
          <select
            value={site}
            onChange={e => { setSite(e.target.value); setSearch(''); }}
            className="w-full px-3 py-2.5 border border-gray-300 rounded-xl text-sm bg-white focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none appearance-none"
          >
            <option value="">Select a site...</option>
            {sites.map(s => <option key={s} value={s}>{s}</option>)}
          </select>

          {/* Search within connections */}
          {connections.length > 0 && (
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Filter by Survey ID, type, code..."
              className="w-full mt-2 px-3 py-2 border border-gray-200 rounded-xl text-sm focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none"
            />
          )}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto">
          {!site ? (
            <div className="text-center py-12 text-gray-400 text-sm">
              Select a site to browse connections
            </div>
          ) : loading ? (
            <div className="text-center py-12 text-gray-400 text-sm flex items-center justify-center gap-2">
              <span className="animate-spin inline-block w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full" />
              Loading connections...
            </div>
          ) : error ? (
            <div className="p-4">
              <div className="p-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">{error}</div>
            </div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-12 text-gray-400 text-sm">
              {search ? 'No matching connections' : 'No connections found for this site'}
            </div>
          ) : (
            <div>
              {/* Unassigned connections first */}
              {unassigned.length > 0 && (
                <div>
                  <div className="px-4 py-2 bg-green-50 border-b">
                    <p className="text-xs font-semibold text-green-700 uppercase tracking-wide">
                      Available ({unassigned.length})
                    </p>
                  </div>
                  <div className="divide-y">{unassigned.map(renderRow)}</div>
                </div>
              )}
              {/* Already-assigned connections */}
              {assigned.length > 0 && (
                <div>
                  <div className="px-4 py-2 bg-gray-50 border-b border-t">
                    <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
                      Already Assigned ({assigned.length})
                    </p>
                  </div>
                  <div className="divide-y opacity-60">{assigned.map(renderRow)}</div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer count */}
        {site && !loading && connections.length > 0 && (
          <div className="px-4 py-2 border-t bg-gray-50 text-xs text-gray-500 text-center shrink-0">
            {connections.length} connection{connections.length !== 1 ? 's' : ''} total
            {search && ` · ${filtered.length} matching`}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// GPS capture component
// ---------------------------------------------------------------------------

function GPSCapture({ lat, lng, onChange }: { lat: string; lng: string; onChange: (lat: string, lng: string) => void }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const captureGPS = () => {
    if (!navigator.geolocation) {
      setError('Geolocation not supported');
      return;
    }
    setLoading(true);
    setError('');
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        onChange(pos.coords.latitude.toFixed(6), pos.coords.longitude.toFixed(6));
        setLoading(false);
      },
      (err) => {
        setError(err.message);
        setLoading(false);
      },
      { enableHighAccuracy: true, timeout: 15000 },
    );
  };

  return (
    <div className="space-y-3">
      <div className="flex gap-3">
        <div className="flex-1">
          <label className="block text-xs text-gray-500 mb-1">Latitude</label>
          <input
            type="text"
            value={lat}
            onChange={e => onChange(e.target.value, lng)}
            placeholder="-29.3..."
            className="w-full px-4 py-3 border border-gray-300 rounded-xl text-base focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none"
          />
        </div>
        <div className="flex-1">
          <label className="block text-xs text-gray-500 mb-1">Longitude</label>
          <input
            type="text"
            value={lng}
            onChange={e => onChange(lat, e.target.value)}
            placeholder="28.5..."
            className="w-full px-4 py-3 border border-gray-300 rounded-xl text-base focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none"
          />
        </div>
      </div>
      <button
        type="button"
        onClick={captureGPS}
        disabled={loading}
        className="w-full py-3 bg-gray-100 border-2 border-dashed border-gray-300 rounded-xl text-sm font-medium text-gray-600 hover:bg-gray-200 active:bg-gray-300 disabled:opacity-50 transition flex items-center justify-center gap-2"
      >
        {loading ? (
          <>
            <span className="animate-spin inline-block w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full" />
            Acquiring GPS...
          </>
        ) : (
          <>
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            Capture Current Location
          </>
        )}
      </button>
      {error && <p className="text-red-500 text-xs">{error}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Progress indicator
// ---------------------------------------------------------------------------

function ProgressBar({ current, total }: { current: number; total: number }) {
  return (
    <div className="flex items-center gap-1.5 mb-6">
      {Array.from({ length: total }, (_, i) => (
        <div key={i} className="flex-1 flex flex-col items-center gap-1">
          <div
            className={`h-1.5 w-full rounded-full transition-colors duration-300 ${
              i < current ? 'bg-blue-600' : i === current ? 'bg-blue-400' : 'bg-gray-200'
            }`}
          />
          <span className={`text-[10px] font-medium ${i <= current ? 'text-blue-600' : 'text-gray-400'}`}>
            {i < steps.length ? i + 1 : 'Review'}
          </span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main wizard component
// ---------------------------------------------------------------------------

export default function NewCustomerWizard() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [form, setForm] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [sites, setSites] = useState<string[]>([]);
  const [showUGPPicker, setShowUGPPicker] = useState(false);
  const [ugpLinked, setUgpLinked] = useState('');

  useEffect(() => {
    fetch('/api/sites')
      .then(r => r.json())
      .then(d => {
        const fetched = (d.sites || []).map((s: any) => s.concession).filter(Boolean);
        if (fetched.length > 0) setSites(fetched);
      })
      .catch(() => {});
  }, []);

  const set = (key: string, value: string) => setForm(prev => ({ ...prev, [key]: value }));

  const handleUGPSelect = (conn: UGPConnection, site: string) => {
    setShowUGPPicker(false);

    set('community', site);
    if (conn.survey_id) set('plot_number', conn.survey_id);
    if (conn.customer_type) set('customer_position', conn.customer_type);
    if (conn.gps_lat != null) set('gps_lat', String(conn.gps_lat));
    if (conn.gps_lon != null) set('gps_lon', String(conn.gps_lon));

    setUgpLinked(conn.survey_id);
  };

  const validateStep = (): string | null => {
    if (step >= steps.length) return null;
    const s = steps[step];
    for (const f of s.fields) {
      if (f.required && !form[f.key]?.trim()) {
        return `${f.label} is required`;
      }
    }
    return null;
  };

  const goNext = () => {
    const err = validateStep();
    if (err) { setError(err); return; }
    setError('');
    setStep(s => Math.min(s + 1, TOTAL_STEPS - 1));
  };

  const goBack = () => {
    setError('');
    setStep(s => Math.max(s - 1, 0));
  };

  const handleSubmit = async () => {
    setSaving(true);
    setError('');
    try {
      const data: Record<string, unknown> = {};
      const syntheticKeys = new Set(['GPS', '_ugp_picker']);
      for (const [k, v] of Object.entries(form)) {
        if (v.trim() && !syntheticKeys.has(k)) data[k] = v.trim();
      }
      data['created_by'] = 'CC Portal';
      data['country'] = 'Lesotho';

      await createRecord('customers', data);
      navigate('/customers', { replace: true });
    } catch (e: any) {
      setError(e.message || 'Failed to create customer');
    } finally {
      setSaving(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Render a single field
  // ---------------------------------------------------------------------------

  const renderField = (f: FieldDef) => {
    if (f.type === 'ugp_picker') {
      return (
        <div key={f.key} className="col-span-2">
          {ugpLinked ? (
            <div className="flex items-center justify-between p-3 bg-blue-50 border border-blue-200 rounded-xl">
              <div className="flex items-center gap-2 min-w-0">
                <svg className="w-5 h-5 text-blue-600 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
                </svg>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-blue-800 truncate">Linked to {ugpLinked}</p>
                  <p className="text-xs text-blue-600">Site, GPS, and type populated from uGridPlan</p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setShowUGPPicker(true)}
                className="text-xs text-blue-700 font-medium hover:underline shrink-0 ml-2"
              >
                Change
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setShowUGPPicker(true)}
              className="w-full py-3.5 bg-blue-50 border-2 border-dashed border-blue-300 rounded-xl text-sm font-medium text-blue-700 hover:bg-blue-100 active:bg-blue-200 transition flex items-center justify-center gap-2"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
              </svg>
              Import from uGridPlan
            </button>
          )}
        </div>
      );
    }

    if (f.type === 'gps') {
      return (
        <div key={f.key} className="col-span-2">
          <label className="block text-sm font-medium text-gray-700 mb-2">{f.label}</label>
          <GPSCapture
            lat={form['gps_lat'] || ''}
            lng={form['gps_lon'] || ''}
            onChange={(lat, lng) => {
              set('gps_lat', lat);
              set('gps_lon', lng);
            }}
          />
        </div>
      );
    }

    if (f.type === 'select') {
      const opts = f.key === 'community' ? sites : (f.options || []);
      return (
        <div key={f.key} className={f.half ? '' : 'col-span-2 sm:col-span-1'}>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            {f.label} {f.required && <span className="text-red-400">*</span>}
          </label>
          <select
            value={form[f.key] || ''}
            onChange={e => set(f.key, e.target.value)}
            className="w-full px-4 py-3.5 border border-gray-300 rounded-xl text-base bg-white focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none appearance-none"
          >
            <option value="">Select...</option>
            {opts.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
        </div>
      );
    }

    return (
      <div key={f.key} className={f.half ? '' : 'col-span-2 sm:col-span-1'}>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          {f.label} {f.required && <span className="text-red-400">*</span>}
        </label>
        <input
          type={f.type || 'text'}
          value={form[f.key] || ''}
          onChange={e => set(f.key, e.target.value)}
          placeholder={f.placeholder}
          className="w-full px-4 py-3.5 border border-gray-300 rounded-xl text-base focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none"
        />
      </div>
    );
  };

  // ---------------------------------------------------------------------------
  // Review step
  // ---------------------------------------------------------------------------

  const renderReview = () => {
    const filledFields = steps.flatMap(s => s.fields).filter(f => {
      if (f.type === 'ugp_picker') return false;
      if (f.type === 'gps') return form['gps_lat'] || form['gps_lon'];
      return form[f.key]?.trim();
    });
    return (
      <div className="space-y-4">
        <p className="text-gray-500 text-sm">Review the information below and tap <strong>Create Customer</strong> to save.</p>

        {ugpLinked && (
          <div className="flex items-center gap-2 p-3 bg-blue-50 border border-blue-200 rounded-xl text-sm text-blue-800">
            <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
            </svg>
            Linked to uGridPlan: <strong>{ugpLinked}</strong>
          </div>
        )}

        <div className="bg-gray-50 rounded-xl border divide-y">
          {filledFields.map(f => (
            <div key={f.key} className="flex justify-between items-start px-4 py-3">
              <span className="text-sm text-gray-500 shrink-0 mr-4">{f.label}</span>
              <span className="text-sm font-medium text-gray-800 text-right">
                {f.type === 'gps' ? `${form['gps_lat'] || '--'}, ${form['gps_lon'] || '--'}` : form[f.key]}
              </span>
            </div>
          ))}
          {filledFields.length === 0 && (
            <div className="px-4 py-6 text-center text-gray-400 text-sm">No information entered yet.</div>
          )}
        </div>
      </div>
    );
  };

  // ---------------------------------------------------------------------------
  // Layout
  // ---------------------------------------------------------------------------

  const isReview = step === steps.length;
  const currentStep = isReview ? null : steps[step];

  return (
    <div className="max-w-lg mx-auto pb-8">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <button
          onClick={() => navigate('/customers')}
          className="p-2 -ml-2 rounded-lg hover:bg-gray-100 active:bg-gray-200 transition"
          aria-label="Back to customers"
        >
          <svg className="w-6 h-6 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <h1 className="text-xl font-bold text-gray-800">New Customer</h1>
      </div>

      {/* Progress */}
      <ProgressBar current={step} total={TOTAL_STEPS} />

      {/* Step content card */}
      <div className="bg-white rounded-2xl shadow-sm border p-5 sm:p-6 min-h-[320px]">
        <div className="mb-5">
          <h2 className="text-lg font-semibold text-gray-800">
            {isReview ? 'Review & Submit' : currentStep!.title}
          </h2>
          <p className="text-sm text-gray-400 mt-0.5">
            {isReview ? 'Confirm all details are correct' : currentStep!.description}
          </p>
        </div>

        {isReview ? renderReview() : (
          <div className="grid grid-cols-2 gap-4">
            {currentStep!.fields.map(renderField)}
          </div>
        )}

        {error && (
          <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">
            {error}
          </div>
        )}
      </div>

      {/* Navigation buttons */}
      <div className="flex gap-3 mt-6">
        {step > 0 && (
          <button
            onClick={goBack}
            className="flex-1 py-4 bg-gray-100 text-gray-700 rounded-xl font-medium text-base hover:bg-gray-200 active:bg-gray-300 transition"
          >
            Back
          </button>
        )}
        {isReview ? (
          <button
            onClick={handleSubmit}
            disabled={saving}
            className="flex-1 py-4 bg-blue-600 text-white rounded-xl font-semibold text-base hover:bg-blue-700 active:bg-blue-800 disabled:opacity-50 transition"
          >
            {saving ? (
              <span className="flex items-center justify-center gap-2">
                <span className="animate-spin inline-block w-5 h-5 border-2 border-white border-t-transparent rounded-full" />
                Creating...
              </span>
            ) : 'Create Customer'}
          </button>
        ) : (
          <button
            onClick={goNext}
            className="flex-1 py-4 bg-blue-600 text-white rounded-xl font-semibold text-base hover:bg-blue-700 active:bg-blue-800 transition"
          >
            Next
          </button>
        )}
      </div>

      {/* uGridPlan picker modal */}
      {showUGPPicker && (
        <UGPConnectionPicker
          sites={sites}
          onSelect={handleUGPSelect}
          onClose={() => setShowUGPPicker(false)}
        />
      )}
    </div>
  );
}
