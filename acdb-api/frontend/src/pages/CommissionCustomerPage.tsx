import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  getCommissionData,
  executeCommission,
  type CommissionData,
} from '../lib/api';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CUSTOMER_TYPES = ['HH', 'SME', 'CHU', 'SCP', 'SCH', 'GOV', 'COM', 'IND'];
const SERVICE_PHASES = ['Single', 'Three'];
const TOTAL_STEPS = 4; // Identify, Details, Signature, Review

// ---------------------------------------------------------------------------
// GPS Capture (reused pattern from AssignMeterPage)
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
// Signature Canvas
// ---------------------------------------------------------------------------

function SignatureCanvas({ onCapture }: { onCapture: (b64: string) => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [isDrawing, setIsDrawing] = useState(false);
  const [hasContent, setHasContent] = useState(false);

  const getPos = useCallback((e: React.TouchEvent | React.MouseEvent) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    if ('touches' in e) {
      return { x: e.touches[0].clientX - rect.left, y: e.touches[0].clientY - rect.top };
    }
    return { x: (e as React.MouseEvent).clientX - rect.left, y: (e as React.MouseEvent).clientY - rect.top };
  }, []);

  const startDraw = useCallback((e: React.TouchEvent | React.MouseEvent) => {
    e.preventDefault();
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!ctx) return;
    const pos = getPos(e);
    ctx.beginPath();
    ctx.moveTo(pos.x, pos.y);
    setIsDrawing(true);
  }, [getPos]);

  const draw = useCallback((e: React.TouchEvent | React.MouseEvent) => {
    e.preventDefault();
    if (!isDrawing) return;
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!ctx) return;
    const pos = getPos(e);
    ctx.lineTo(pos.x, pos.y);
    ctx.strokeStyle = '#000';
    ctx.lineWidth = 2;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.stroke();
    setHasContent(true);
  }, [isDrawing, getPos]);

  const endDraw = useCallback(() => {
    setIsDrawing(false);
  }, []);

  const clearCanvas = () => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!ctx || !canvas) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    setHasContent(false);
  };

  const acceptSignature = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    // Convert to JPEG base64
    const dataUrl = canvas.toDataURL('image/jpeg', 0.85);
    const b64 = dataUrl.split(',')[1]; // strip data:image/jpeg;base64,
    onCapture(b64);
  };

  // Size canvas to container on mount
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const parent = canvas.parentElement;
    if (parent) {
      canvas.width = parent.clientWidth;
      canvas.height = Math.min(200, parent.clientWidth * 0.4);
    }
  }, []);

  return (
    <div className="space-y-3">
      <div className="border-2 border-gray-300 rounded-xl overflow-hidden bg-white touch-none">
        <canvas
          ref={canvasRef}
          className="w-full cursor-crosshair"
          onMouseDown={startDraw}
          onMouseMove={draw}
          onMouseUp={endDraw}
          onMouseLeave={endDraw}
          onTouchStart={startDraw}
          onTouchMove={draw}
          onTouchEnd={endDraw}
        />
      </div>
      <p className="text-xs text-gray-400 text-center">Sign above using your finger or stylus</p>
      <div className="flex gap-3">
        <button type="button" onClick={clearCanvas}
          className="flex-1 py-3 bg-gray-100 text-gray-600 rounded-xl font-medium text-sm hover:bg-gray-200 active:bg-gray-300 transition">
          Clear
        </button>
        <button type="button" onClick={acceptSignature} disabled={!hasContent}
          className="flex-1 py-3 bg-green-600 text-white rounded-xl font-semibold text-sm hover:bg-green-700 active:bg-green-800 disabled:opacity-40 transition">
          Accept Signature
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Progress indicator
// ---------------------------------------------------------------------------

function ProgressBar({ current }: { current: number }) {
  const labels = ['Identify', 'Details', 'Sign', 'Review'];
  return (
    <div className="flex items-center gap-1.5 mb-6">
      {labels.map((label, i) => (
        <div key={i} className="flex-1 flex flex-col items-center gap-1">
          <div className={`h-1.5 w-full rounded-full transition-colors duration-300 ${
            i < current ? 'bg-blue-600' : i === current ? 'bg-blue-400' : 'bg-gray-200'
          }`} />
          <span className={`text-[10px] font-medium ${i <= current ? 'text-blue-600' : 'text-gray-400'}`}>
            {label}
          </span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function CommissionCustomerPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const prefilledCustomerId = searchParams.get('customer') || '';
  const prefilledAccount = searchParams.get('account') || '';

  // Step tracking
  const [step, setStep] = useState(0);

  // Step 1 - Identify
  const [customerId, setCustomerId] = useState(prefilledCustomerId);
  const [customerData, setCustomerData] = useState<CommissionData | null>(null);
  const [lookupLoading, setLookupLoading] = useState(false);
  const [lookupError, setLookupError] = useState('');

  // Step 2 - Commission Details
  const [connectionDate, setConnectionDate] = useState(new Date().toISOString().slice(0, 10));
  const [customerType, setCustomerType] = useState('');
  const [nationalId, setNationalId] = useState('');
  const [servicePhase, setServicePhase] = useState('Single');
  const [ampacity, setAmpacity] = useState('Standard');
  const [phoneNumber, setPhoneNumber] = useState('');
  const [gpsLat, setGpsLat] = useState('');
  const [gpsLng, setGpsLng] = useState('');
  const [accountNumber, setAccountNumber] = useState(prefilledAccount);

  // Step 3 - Signature
  const [signatureB64, setSignatureB64] = useState('');

  // General UI state
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ contract_en_url: string; contract_so_url: string; sms_sent: boolean } | null>(null);

  // Auto-lookup customer when ID entered
  useEffect(() => {
    if (!customerId.trim()) {
      setCustomerData(null);
      setLookupError('');
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      setLookupLoading(true);
      setLookupError('');
      getCommissionData(customerId.trim())
        .then(data => {
          if (cancelled) return;
          setCustomerData(data);

          // Pre-fill from fetched data
          const c = data.customer;
          if (c.customer_type && !customerType) setCustomerType(c.customer_type);
          if (c.national_id && !nationalId) setNationalId(c.national_id);
          if (c.phone && !phoneNumber) setPhoneNumber(c.phone);
          if (c.gps_y && !gpsLat) setGpsLat(c.gps_y);
          if (c.gps_x && !gpsLng) setGpsLng(c.gps_x);
          if (data.account_number && !accountNumber) setAccountNumber(data.account_number);
        })
        .catch(err => { if (!cancelled) setLookupError(err.message || 'Customer not found'); })
        .finally(() => { if (!cancelled) setLookupLoading(false); });
    }, 600);
    return () => { cancelled = true; clearTimeout(timer); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customerId]);

  // Step validation
  const validateStep = (): string | null => {
    if (step === 0) {
      if (!customerId.trim()) return 'Customer ID is required';
      if (!customerData) return 'Please enter a valid Customer ID';
    }
    if (step === 1) {
      if (!connectionDate) return 'Connection date is required';
      if (!customerType) return 'Customer type is required';
      if (!nationalId.trim()) return 'National ID is required';
      if (!phoneNumber.trim()) return 'Phone number is required';
      if (!accountNumber.trim()) return 'Account number is required';
    }
    if (step === 2) {
      if (!signatureB64) return 'Please capture the customer signature';
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

  // Execute commissioning
  const handleSubmit = async () => {
    setSaving(true);
    setError('');
    try {
      const res = await executeCommission({
        customer_id: parseInt(customerId, 10),
        account_number: accountNumber.trim(),
        site_code: customerData?.customer.concession || '',
        customer_type: customerType,
        connection_date: connectionDate,
        service_phase: servicePhase,
        ampacity: ampacity,
        national_id: nationalId.trim(),
        phone_number: phoneNumber.trim(),
        first_name: customerData?.customer.first_name || '',
        last_name: customerData?.customer.last_name || '',
        gps_lat: gpsLat || undefined,
        gps_lng: gpsLng || undefined,
        customer_signature: signatureB64,
      });
      setResult(res);
    } catch (e: any) {
      setError(e.message || 'Commissioning failed');
    } finally {
      setSaving(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Render steps
  // ---------------------------------------------------------------------------

  const renderStep0 = () => (
    <div className="space-y-5">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Customer ID <span className="text-red-400">*</span>
        </label>
        <input
          type="text"
          value={customerId}
          onChange={e => setCustomerId(e.target.value)}
          placeholder="e.g. 5846"
          className="w-full px-4 py-3.5 border border-gray-300 rounded-xl text-base focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none"
        />
        {lookupLoading && <p className="text-xs text-gray-400 mt-1">Looking up customer...</p>}
        {lookupError && <p className="text-xs text-red-500 mt-1">{lookupError}</p>}
      </div>

      {customerData && (
        <div className="bg-gray-50 rounded-xl border p-4 space-y-2">
          <div className="flex justify-between">
            <span className="text-sm text-gray-500">Name</span>
            <span className="text-sm font-medium">{customerData.customer.first_name} {customerData.customer.last_name}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-sm text-gray-500">Site</span>
            <span className="text-sm font-medium">{customerData.customer.concession}</span>
          </div>
          {customerData.meter && (
            <div className="flex justify-between">
              <span className="text-sm text-gray-500">Meter</span>
              <span className="text-sm font-medium">{customerData.meter.meter_id}</span>
            </div>
          )}
          <div className="flex justify-between">
            <span className="text-sm text-gray-500">Account</span>
            <span className="text-sm font-medium">{customerData.account_number || 'N/A'}</span>
          </div>
          {customerData.customer.date_connected && (
            <div className="flex justify-between">
              <span className="text-sm text-gray-500">Connected</span>
              <span className="text-sm font-medium">{customerData.customer.date_connected}</span>
            </div>
          )}
          {customerData.existing_contracts.length > 0 && (
            <div className="flex justify-between">
              <span className="text-sm text-gray-500">Contracts on file</span>
              <span className="text-sm font-medium text-green-600">{customerData.existing_contracts.length} found</span>
            </div>
          )}
        </div>
      )}
    </div>
  );

  const renderStep1 = () => (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">Connection Date <span className="text-red-400">*</span></label>
        <input type="date" value={connectionDate} onChange={e => setConnectionDate(e.target.value)}
          className="w-full px-4 py-3.5 border border-gray-300 rounded-xl text-base focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none" />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">Customer Type <span className="text-red-400">*</span></label>
        <select value={customerType} onChange={e => setCustomerType(e.target.value)}
          className="w-full px-4 py-3.5 border border-gray-300 rounded-xl text-base bg-white focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none appearance-none">
          <option value="">Select type...</option>
          {CUSTOMER_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">National ID <span className="text-red-400">*</span></label>
        <input type="text" value={nationalId} onChange={e => setNationalId(e.target.value)} placeholder="ID or passport number"
          className="w-full px-4 py-3.5 border border-gray-300 rounded-xl text-base focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none" />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">Phone Number <span className="text-red-400">*</span></label>
        <input type="tel" value={phoneNumber} onChange={e => setPhoneNumber(e.target.value)} placeholder="+266 ..."
          className="w-full px-4 py-3.5 border border-gray-300 rounded-xl text-base focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none" />
      </div>

      <div className="flex gap-3">
        <div className="flex-1">
          <label className="block text-sm font-medium text-gray-700 mb-2">Service Phase</label>
          <select value={servicePhase} onChange={e => setServicePhase(e.target.value)}
            className="w-full px-4 py-3.5 border border-gray-300 rounded-xl text-base bg-white focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none appearance-none">
            {SERVICE_PHASES.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        <div className="flex-1">
          <label className="block text-sm font-medium text-gray-700 mb-2">Ampacity</label>
          <input type="text" value={ampacity} onChange={e => setAmpacity(e.target.value)}
            className="w-full px-4 py-3.5 border border-gray-300 rounded-xl text-base focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none" />
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">Account Number <span className="text-red-400">*</span></label>
        <input type="text" value={accountNumber} onChange={e => setAccountNumber(e.target.value)}
          className="w-full px-4 py-3.5 border border-gray-300 rounded-xl text-base focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none" />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">GPS Coordinates</label>
        <GPSCapture lat={gpsLat} lng={gpsLng} onChange={(lat, lng) => { setGpsLat(lat); setGpsLng(lng); }} />
      </div>
    </div>
  );

  const renderStep2 = () => (
    <div className="space-y-4">
      {signatureB64 ? (
        <div className="space-y-3">
          <div className="border-2 border-green-300 rounded-xl p-4 bg-green-50 flex flex-col items-center gap-2">
            <svg className="w-8 h-8 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
            <p className="text-green-700 font-medium text-sm">Signature captured</p>
            <img
              src={`data:image/jpeg;base64,${signatureB64}`}
              alt="Captured signature"
              className="max-w-[200px] max-h-[80px] border rounded"
            />
          </div>
          <button type="button" onClick={() => setSignatureB64('')}
            className="w-full py-3 bg-gray-100 text-gray-600 rounded-xl font-medium text-sm hover:bg-gray-200 active:bg-gray-300 transition">
            Re-sign
          </button>
        </div>
      ) : (
        <SignatureCanvas onCapture={setSignatureB64} />
      )}
    </div>
  );

  const renderStep3 = () => {
    if (result) {
      return (
        <div className="space-y-4">
          <div className="bg-green-50 border border-green-200 rounded-xl p-5 text-center space-y-3">
            <svg className="w-12 h-12 text-green-600 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <h3 className="text-lg font-bold text-green-800">Customer Commissioned</h3>
            <p className="text-sm text-green-700">
              {result.sms_sent
                ? 'Contract links sent to customer via SMS.'
                : 'Contracts generated. SMS delivery was not configured.'}
            </p>
          </div>

          <div className="space-y-2">
            <a href={result.contract_en_url} target="_blank" rel="noopener noreferrer"
              className="block w-full py-3 bg-blue-600 text-white rounded-xl font-medium text-center hover:bg-blue-700 transition">
              View English Contract
            </a>
            <a href={result.contract_so_url} target="_blank" rel="noopener noreferrer"
              className="block w-full py-3 bg-blue-600 text-white rounded-xl font-medium text-center hover:bg-blue-700 transition">
              View Sesotho Contract
            </a>
          </div>

          <button type="button" onClick={() => navigate(`/customers/${customerId}`)}
            className="w-full py-3 bg-gray-100 text-gray-700 rounded-xl font-medium text-sm hover:bg-gray-200 transition">
            Go to Customer Detail
          </button>
        </div>
      );
    }

    // Review before submission
    const items = [
      { label: 'Customer', value: `${customerData?.customer.first_name} ${customerData?.customer.last_name} (#${customerId})` },
      { label: 'Account', value: accountNumber },
      { label: 'Site', value: customerData?.customer.concession || '' },
      { label: 'Type', value: customerType },
      { label: 'Connection Date', value: connectionDate },
      { label: 'National ID', value: nationalId },
      { label: 'Phone', value: phoneNumber },
      { label: 'Service Phase', value: servicePhase },
      { label: 'Ampacity', value: ampacity },
    ];
    if (gpsLat && gpsLng) items.push({ label: 'GPS', value: `${gpsLat}, ${gpsLng}` });

    return (
      <div className="space-y-4">
        <p className="text-gray-500 text-sm">Review the information below. This will update the customer record and generate bilingual contracts.</p>
        <div className="bg-gray-50 rounded-xl border divide-y">
          {items.map(item => (
            <div key={item.label} className="flex justify-between items-start px-4 py-3">
              <span className="text-sm text-gray-500 shrink-0 mr-4">{item.label}</span>
              <span className="text-sm font-medium text-gray-800 text-right">{item.value}</span>
            </div>
          ))}
          <div className="flex justify-between items-center px-4 py-3">
            <span className="text-sm text-gray-500">Signature</span>
            <img src={`data:image/jpeg;base64,${signatureB64}`} alt="Signature" className="max-w-[120px] max-h-[40px] border rounded" />
          </div>
        </div>
      </div>
    );
  };

  // ---------------------------------------------------------------------------
  // Layout
  // ---------------------------------------------------------------------------

  const stepTitles = ['Identify Customer', 'Commission Details', 'Capture Signature', 'Review & Generate'];
  const stepDescs = [
    'Enter the Customer ID to look up their record',
    'Fill in commissioning details',
    'Customer signs on the tablet',
    result ? 'Commissioning complete' : 'Confirm and generate contracts',
  ];

  return (
    <div className="max-w-lg mx-auto pb-8">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <button onClick={() => navigate(-1)}
          className="p-2 -ml-2 rounded-lg hover:bg-gray-100 active:bg-gray-200 transition" aria-label="Go back">
          <svg className="w-6 h-6 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <div>
          <h1 className="text-xl font-bold text-gray-800">Commission Customer</h1>
          <p className="text-sm text-gray-400">Generate contract and connect service</p>
        </div>
      </div>

      {/* Progress */}
      <ProgressBar current={step} />

      {/* Step card */}
      <div className="bg-white rounded-2xl shadow-sm border p-5 sm:p-6 min-h-[320px]">
        <div className="mb-5">
          <h2 className="text-lg font-semibold text-gray-800">{stepTitles[step]}</h2>
          <p className="text-sm text-gray-400 mt-0.5">{stepDescs[step]}</p>
        </div>

        {step === 0 && renderStep0()}
        {step === 1 && renderStep1()}
        {step === 2 && renderStep2()}
        {step === 3 && renderStep3()}

        {error && (
          <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">{error}</div>
        )}
      </div>

      {/* Navigation */}
      {!result && (
        <div className="flex gap-3 mt-6">
          {step > 0 && (
            <button onClick={goBack}
              className="flex-1 py-4 bg-gray-100 text-gray-700 rounded-xl font-medium text-base hover:bg-gray-200 active:bg-gray-300 transition">
              Back
            </button>
          )}
          {step < TOTAL_STEPS - 1 ? (
            <button onClick={goNext}
              className="flex-1 py-4 bg-blue-600 text-white rounded-xl font-semibold text-base hover:bg-blue-700 active:bg-blue-800 transition">
              Next
            </button>
          ) : (
            <button onClick={handleSubmit} disabled={saving}
              className="flex-1 py-4 bg-green-600 text-white rounded-xl font-semibold text-base hover:bg-green-700 active:bg-green-800 disabled:opacity-50 transition">
              {saving ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="animate-spin inline-block w-5 h-5 border-2 border-white border-t-transparent rounded-full" />
                  Generating...
                </span>
              ) : 'Generate Contract & SMS'}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
