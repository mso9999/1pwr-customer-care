import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { createRecord } from '../lib/api';

// ---------------------------------------------------------------------------
// Wizard step definitions
// ---------------------------------------------------------------------------

interface FieldDef {
  key: string;
  label: string;
  type?: 'text' | 'tel' | 'date' | 'select' | 'gps';
  placeholder?: string;
  required?: boolean;
  options?: string[];
  half?: boolean;            // take half width on tablet+
}

const CUSTOMER_TYPES = ['HH', 'SME', 'CHU', 'SCP', 'SCH', 'GOV', 'COM', 'IND'];

const steps: { title: string; description: string; fields: FieldDef[] }[] = [
  {
    title: 'Personal Information',
    description: 'Customer name and contact details',
    fields: [
      { key: 'FIRST NAME', label: 'First Name', required: true, half: true },
      { key: 'LAST NAME', label: 'Last Name', required: true, half: true },
      { key: 'MIDDLE NAME', label: 'Middle Name', half: true },
      { key: 'GENDER', label: 'Gender', type: 'select', options: ['Male', 'Female'], half: true },
      { key: 'ID NUMBER', label: 'National ID Number', placeholder: 'ID / Passport number' },
      { key: 'PHONE', label: 'Phone', type: 'tel', placeholder: '+266 ...', half: true },
      { key: 'CELL PHONE 1', label: 'Cell Phone', type: 'tel', placeholder: '+266 ...', half: true },
    ],
  },
  {
    title: 'Location',
    description: 'Site, district, and GPS coordinates',
    fields: [
      { key: 'Concession name', label: 'Site (Concession)', type: 'select', options: [], required: true },
      { key: 'DISTRICT', label: 'District', placeholder: 'e.g. Mafeteng' },
      { key: 'PLOT NUMBER', label: 'Plot / Stand Number', placeholder: 'e.g. MAK 0001 HH' },
      { key: 'STREET ADDRESS', label: 'Village / Street Address', placeholder: 'Village or street name' },
      { key: 'GPS', label: 'GPS Coordinates', type: 'gps' },
    ],
  },
  {
    title: 'Service Details',
    description: 'Connection and metering information',
    fields: [
      { key: 'CUSTOMER POSITION', label: 'Customer Type', type: 'select', options: CUSTOMER_TYPES, required: true },
      { key: 'DATE SERVICE CONNECTED', label: 'Date Connected', type: 'date' },
    ],
  },
];

const TOTAL_STEPS = steps.length + 1; // +1 for review

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

  // Load dynamic site list
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

  // Validate current step
  const validateStep = (): string | null => {
    if (step >= steps.length) return null; // review step
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
    if (err) {
      setError(err);
      return;
    }
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
      // Build the data payload - strip empty strings and synthetic keys
      const data: Record<string, unknown> = {};
      const syntheticKeys = new Set(['GPS']); // GPS is split into GPS X / GPS Y
      for (const [k, v] of Object.entries(form)) {
        if (v.trim() && !syntheticKeys.has(k)) data[k] = v.trim();
      }
      // Add audit fields
      data['RECORD CREATE DATE'] = new Date().toISOString().slice(0, 19).replace('T', ' ');
      data['RECORD CREATED BY'] = 'CC Portal';
      data['COUNTRY'] = 'Lesotho';

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
    if (f.type === 'gps') {
      return (
        <div key={f.key} className="col-span-2">
          <label className="block text-sm font-medium text-gray-700 mb-2">{f.label}</label>
          <GPSCapture
            lat={form['GPS Y'] || ''}
            lng={form['GPS X'] || ''}
            onChange={(lat, lng) => {
              set('GPS Y', lat);
              set('GPS X', lng);
            }}
          />
        </div>
      );
    }

    if (f.type === 'select') {
      const opts = f.key === 'Concession name' ? sites : (f.options || []);
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
      if (f.type === 'gps') return form['GPS Y'] || form['GPS X'];
      return form[f.key]?.trim();
    });
    return (
      <div className="space-y-4">
        <p className="text-gray-500 text-sm">Review the information below and tap <strong>Create Customer</strong> to save.</p>
        <div className="bg-gray-50 rounded-xl border divide-y">
          {filledFields.map(f => (
            <div key={f.key} className="flex justify-between items-start px-4 py-3">
              <span className="text-sm text-gray-500 shrink-0 mr-4">{f.label}</span>
              <span className="text-sm font-medium text-gray-800 text-right">
                {f.type === 'gps' ? `${form['GPS Y'] || '--'}, ${form['GPS X'] || '--'}` : form[f.key]}
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
        {/* Step title */}
        <div className="mb-5">
          <h2 className="text-lg font-semibold text-gray-800">
            {isReview ? 'Review & Submit' : currentStep!.title}
          </h2>
          <p className="text-sm text-gray-400 mt-0.5">
            {isReview ? 'Confirm all details are correct' : currentStep!.description}
          </p>
        </div>

        {/* Fields */}
        {isReview ? renderReview() : (
          <div className="grid grid-cols-2 gap-4">
            {currentStep!.fields.map(renderField)}
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">
            {error}
          </div>
        )}
      </div>

      {/* Navigation buttons - large touch targets */}
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
    </div>
  );
}
