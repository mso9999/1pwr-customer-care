import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  getRecord, updateRecord, deleteRecord, getCustomerContracts, decommissionCustomer,
  getFinancingProducts, createFinancingAgreement,
  type CommissionContract, type FinancingProduct,
} from '../lib/api';
import { useAuth } from '../contexts/AuthContext';
import SignatureCapture from '../components/SignatureCapture';

// ---------------------------------------------------------------------------
// Extend Credit Wizard (4-step modal)
// ---------------------------------------------------------------------------

function ExtendCreditWizard({ accountNumber, onClose, onCreated }: {
  accountNumber: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [step, setStep] = useState(1);
  const [products, setProducts] = useState<FinancingProduct[]>([]);
  const [selectedProduct, setSelectedProduct] = useState<FinancingProduct | null>(null);

  const [form, setForm] = useState({
    description: '',
    principal: 0,
    interest_amount: 0,
    setup_fee: 0,
    repayment_fraction: 0.2,
    penalty_rate: 0,
    penalty_grace_days: 30,
    penalty_interval_days: 30,
  });

  const [signatureB64, setSignatureB64] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<any>(null);

  useEffect(() => {
    getFinancingProducts().then(setProducts).catch(() => {});
  }, []);

  const selectProduct = (p: FinancingProduct) => {
    setSelectedProduct(p);
    setForm({
      description: p.name,
      principal: p.default_principal,
      interest_amount: +(p.default_principal * p.default_interest_rate).toFixed(2),
      setup_fee: p.default_setup_fee,
      repayment_fraction: p.default_repayment_fraction,
      penalty_rate: p.default_penalty_rate,
      penalty_grace_days: p.default_penalty_grace_days,
      penalty_interval_days: p.default_penalty_interval_days,
    });
    setStep(2);
  };

  const totalOwed = form.principal + form.interest_amount + form.setup_fee;

  const handleSubmit = async () => {
    setSubmitting(true);
    setError('');
    try {
      const res = await createFinancingAgreement({
        account_number: accountNumber,
        product_id: selectedProduct?.id,
        description: form.description,
        principal: form.principal,
        interest_amount: form.interest_amount,
        setup_fee: form.setup_fee,
        total_owed: totalOwed,
        repayment_fraction: form.repayment_fraction,
        penalty_rate: form.penalty_rate,
        penalty_grace_days: form.penalty_grace_days,
        penalty_interval_days: form.penalty_interval_days,
        customer_signature_b64: signatureB64,
      });
      setResult(res);
      setStep(5);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  const field = (label: string, key: keyof typeof form, step = 'any') => (
    <label className="block">
      <span className="text-sm text-gray-600">{label}</span>
      <input type="number" step={step}
        className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:ring-2 focus:ring-blue-400 focus:outline-none"
        value={form[key]}
        onChange={e => setForm({ ...form, [key]: Number(e.target.value) })}
      />
    </label>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="sticky top-0 bg-white border-b px-6 py-4 rounded-t-2xl flex items-center justify-between">
          <div>
            <h3 className="text-lg font-bold text-gray-800">Extend Credit</h3>
            <p className="text-xs text-gray-500">Account: {accountNumber}</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-2xl leading-none">&times;</button>
        </div>

        {/* Progress */}
        <div className="px-6 pt-4">
          <div className="flex gap-1">
            {['Product', 'Terms', 'Signature', 'Review'].map((label, i) => (
              <div key={label} className="flex-1">
                <div className={`h-1.5 rounded-full ${step > i + 1 ? 'bg-green-500' : step === i + 1 ? 'bg-blue-500' : 'bg-gray-200'}`} />
                <p className={`text-xs mt-1 ${step === i + 1 ? 'text-blue-600 font-medium' : 'text-gray-400'}`}>{label}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="px-6 py-4 space-y-4">
          {error && <div className="bg-red-50 text-red-700 text-sm rounded-lg p-3">{error}</div>}

          {/* Step 1: Product selection */}
          {step === 1 && (
            <div className="space-y-3">
              <p className="text-sm text-gray-600">Select a financing product template or create a custom agreement.</p>
              {products.filter(p => p.is_active).map(p => (
                <button key={p.id} onClick={() => selectProduct(p)}
                  className="w-full text-left p-4 border rounded-xl hover:bg-blue-50 hover:border-blue-300 transition">
                  <div className="font-medium text-gray-800">{p.name}</div>
                  <div className="text-xs text-gray-500 mt-1">
                    Principal: M {p.default_principal.toFixed(2)} · Interest: {(p.default_interest_rate * 100).toFixed(1)}% · Repayment: {(p.default_repayment_fraction * 100).toFixed(0)}%
                  </div>
                </button>
              ))}
              <button onClick={() => setStep(2)}
                className="w-full p-4 border-2 border-dashed rounded-xl text-gray-500 hover:text-gray-700 hover:border-gray-400 transition text-sm">
                + Custom Agreement (no template)
              </button>
            </div>
          )}

          {/* Step 2: Terms */}
          {step === 2 && (
            <div className="space-y-3">
              <label className="block">
                <span className="text-sm text-gray-600">Description / Asset</span>
                <input type="text"
                  className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:ring-2 focus:ring-blue-400 focus:outline-none"
                  value={form.description}
                  onChange={e => setForm({ ...form, description: e.target.value })}
                  placeholder="e.g. Readyboard, Refrigerator"
                />
              </label>
              <div className="grid grid-cols-2 gap-3">
                {field('Principal (M)', 'principal')}
                {field('Interest (M)', 'interest_amount')}
                {field('Setup Fee (M)', 'setup_fee')}
                {field('Repayment Fraction', 'repayment_fraction', '0.01')}
                {field('Penalty Rate', 'penalty_rate', '0.01')}
                {field('Grace Days', 'penalty_grace_days', '1')}
              </div>
              <div className="bg-blue-50 rounded-lg p-3 text-sm">
                <span className="text-gray-600">Total Owed: </span>
                <span className="font-bold text-blue-700">M {totalOwed.toFixed(2)}</span>
              </div>
              <div className="flex gap-3 pt-2">
                <button onClick={() => setStep(1)} className="flex-1 py-2.5 bg-gray-100 text-gray-600 rounded-lg text-sm hover:bg-gray-200">Back</button>
                <button onClick={() => setStep(3)} disabled={!form.description || form.principal <= 0}
                  className="flex-1 py-2.5 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50">Next: Signature</button>
              </div>
            </div>
          )}

          {/* Step 3: Signature */}
          {step === 3 && (
            <div className="space-y-3">
              <p className="text-sm text-gray-600">Customer signature confirming acceptance of financing terms. Draw it here or upload a JPEG image.</p>
              {signatureB64 ? (
                <div className="space-y-3">
                  <div className="bg-green-50 border border-green-200 rounded-xl p-4 text-center">
                    <img src={`data:image/jpeg;base64,${signatureB64}`} alt="Signature" className="max-h-24 mx-auto" />
                    <p className="text-xs text-green-700 mt-2">Signature captured</p>
                  </div>
                  <button onClick={() => setSignatureB64('')} className="w-full py-2 text-sm text-gray-500 hover:text-gray-700">Replace Signature</button>
                </div>
              ) : (
                <SignatureCapture onCapture={setSignatureB64} />
              )}
              <div className="flex gap-3 pt-2">
                <button onClick={() => setStep(2)} className="flex-1 py-2.5 bg-gray-100 text-gray-600 rounded-lg text-sm hover:bg-gray-200">Back</button>
                <button onClick={() => setStep(4)} disabled={!signatureB64}
                  className="flex-1 py-2.5 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50">Next: Review</button>
              </div>
            </div>
          )}

          {/* Step 4: Review */}
          {step === 4 && (
            <div className="space-y-3">
              <div className="bg-gray-50 rounded-lg p-4 space-y-2 text-sm">
                <div className="flex justify-between"><span className="text-gray-500">Account</span><span className="font-medium">{accountNumber}</span></div>
                <div className="flex justify-between"><span className="text-gray-500">Description</span><span className="font-medium">{form.description}</span></div>
                <div className="flex justify-between"><span className="text-gray-500">Principal</span><span>M {form.principal.toFixed(2)}</span></div>
                <div className="flex justify-between"><span className="text-gray-500">Interest</span><span>M {form.interest_amount.toFixed(2)}</span></div>
                <div className="flex justify-between"><span className="text-gray-500">Setup Fee</span><span>M {form.setup_fee.toFixed(2)}</span></div>
                <div className="flex justify-between border-t pt-2"><span className="text-gray-500 font-bold">Total Owed</span><span className="font-bold text-blue-700">M {totalOwed.toFixed(2)}</span></div>
                <div className="flex justify-between"><span className="text-gray-500">Repayment</span><span>{(form.repayment_fraction * 100).toFixed(0)}% of each payment</span></div>
                <div className="flex justify-between"><span className="text-gray-500">Penalty</span><span>{(form.penalty_rate * 100).toFixed(1)}% after {form.penalty_grace_days} days</span></div>
              </div>
              <div className="flex gap-3 pt-2">
                <button onClick={() => setStep(3)} className="flex-1 py-2.5 bg-gray-100 text-gray-600 rounded-lg text-sm hover:bg-gray-200">Back</button>
                <button onClick={handleSubmit} disabled={submitting}
                  className="flex-1 py-2.5 bg-green-600 text-white rounded-lg text-sm font-semibold hover:bg-green-700 disabled:opacity-50">
                  {submitting ? 'Creating...' : 'Create Agreement'}
                </button>
              </div>
            </div>
          )}

          {/* Step 5: Success */}
          {step === 5 && result && (
            <div className="space-y-4 text-center">
              <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mx-auto">
                <svg className="w-8 h-8 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <h4 className="text-lg font-bold text-gray-800">Agreement Created</h4>
              <p className="text-sm text-gray-500">Agreement #{result.id} for M {result.total_owed?.toFixed(2)}</p>
              {result.contracts?.en_url && (
                <a href={result.contracts.en_url} target="_blank" rel="noopener noreferrer"
                  className="inline-block px-4 py-2 bg-blue-50 text-blue-700 rounded-lg text-sm hover:bg-blue-100">
                  Download Contract (EN)
                </a>
              )}
              {result.contracts?.so_url && (
                <a href={result.contracts.so_url} target="_blank" rel="noopener noreferrer"
                  className="inline-block px-4 py-2 bg-blue-50 text-blue-700 rounded-lg text-sm hover:bg-blue-100 ml-2">
                  Download Contract (SO)
                </a>
              )}
              <button onClick={() => { onCreated(); onClose(); }}
                className="w-full py-2.5 bg-gray-100 text-gray-700 rounded-lg text-sm hover:bg-gray-200 mt-2">Done</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function CustomerDetailPage() {
  const { id: urlParam } = useParams<{ id: string }>();
  const [record, setRecord] = useState<Record<string, unknown> | null>(null);
  const [pgId, setPgId] = useState<string>('');
  const [accountNumber, setAccountNumber] = useState<string>('');
  const [editing, setEditing] = useState(false);
  const [formData, setFormData] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [contracts, setContracts] = useState<CommissionContract[]>([]);
  const [accountNumbers, setAccountNumbers] = useState<string[]>([]);
  const [decommissioning, setDecommissioning] = useState(false);
  const [showCreditWizard, setShowCreditWizard] = useState(false);
  const { canWrite, canWriteCustomers, isSuperadmin, user } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (!urlParam) return;

    const isAccountNumber = /^[0-9]{3,4}[A-Z]{2,4}$/i.test(urlParam);

    if (isAccountNumber) {
      setAccountNumber(urlParam.toUpperCase());
      fetch(`/api/customers/by-account/${encodeURIComponent(urlParam)}`, {
        headers: { 'Authorization': `Bearer ${localStorage.getItem('cc_token') || ''}` },
      })
        .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
        .then(data => {
          const cust = data.customer;
          const accts: string[] = cust.account_numbers || [];
          setAccountNumbers(accts);
          if (!accts.includes(urlParam.toUpperCase())) accts.push(urlParam.toUpperCase());

          const legacyId = String(cust.customer_id_legacy || '');
          if (legacyId) {
            return getRecord('customers', legacyId).then(({ record: r }) => {
              setRecord(r);
              setPgId(String(r['id'] ?? legacyId));
              const fd: Record<string, string> = {};
              for (const [k, v] of Object.entries(r)) fd[k] = v != null ? String(v) : '';
              setFormData(fd);
            });
          }
        })
        .catch((e) => setError(e.message));

      getCustomerContracts(urlParam.toUpperCase())
        .then(({ contracts: c }) => setContracts(c))
        .catch(() => {});
    } else {
      getRecord('customers', urlParam)
        .then(({ record: r }) => {
          setRecord(r);
          setPgId(String(r['id'] ?? urlParam));
          const fd: Record<string, string> = {};
          for (const [k, v] of Object.entries(r)) fd[k] = v != null ? String(v) : '';
          setFormData(fd);
        })
        .catch((e) => setError(e.message));

      fetch(`/api/customers/by-id/${encodeURIComponent(urlParam)}`, {
        headers: { 'Authorization': `Bearer ${localStorage.getItem('cc_token') || ''}` },
      })
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (data?.customer) {
            setAccountNumbers(data.customer.account_numbers || []);
            if (data.customer.account_numbers?.[0]) {
              setAccountNumber(data.customer.account_numbers[0]);
            }
          }
        })
        .catch(() => {});

      getCustomerContracts(parseInt(urlParam, 10))
        .then(({ contracts: c }) => setContracts(c))
        .catch(() => {});
    }
  }, [urlParam]);

  const recordId = pgId || urlParam || '';

  const handleSave = async () => {
    if (!recordId) return;
    setSaving(true);
    setError('');
    try {
      const changes: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(formData)) {
        if (record && String(record[k] ?? '') !== v) changes[k] = v;
      }
      if (Object.keys(changes).length === 0) { setEditing(false); return; }
      await updateRecord('customers', recordId, changes);
      setEditing(false);
      const { record: r } = await getRecord('customers', recordId);
      setRecord(r);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!recordId || !confirm('Are you sure you want to delete this customer?')) return;
    try {
      await deleteRecord('customers', recordId);
      navigate('/customers');
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleDecommission = async () => {
    const legacyId = record?.['customer_id_legacy'];
    if (!legacyId) return;
    const displayName = accountNumber || urlParam;
    const msg =
      'Decommission customer ' + displayName + '?\n\n' +
      'This sets DATE SERVICE TERMINATED to today.\n' +
      'All meter, account, and transaction history is preserved.';
    if (!confirm(msg)) return;
    setDecommissioning(true);
    setError('');
    try {
      const result = await decommissionCustomer(Number(legacyId));
      alert(`Customer ${displayName} decommissioned (terminated ${result.terminated_date}). All records preserved.`);
      const { record: r } = await getRecord('customers', recordId);
      setRecord(r);
      const fd: Record<string, string> = {};
      for (const [k, v] of Object.entries(r)) fd[k] = v != null ? String(v) : '';
      setFormData(fd);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setDecommissioning(false);
    }
  };

  if (error && !record) return <div className="text-center py-8 text-red-500">{error}</div>;
  if (!record) return <div className="text-center py-8 text-gray-400">Loading...</div>;

  const fields = Object.keys(record);
  const displayTitle = accountNumber || urlParam || '';
  const connectedVal = record['date_service_connected'];
  const terminatedVal = record['date_service_terminated'];
  const isConnected = connectedVal != null && String(connectedVal).trim() !== '';
  const isTerminated = terminatedVal != null && String(terminatedVal).trim() !== '';
  const isCommissioned = isConnected && !isTerminated;

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
        <div className="flex items-center gap-3 min-w-0">
          <h1 className="text-xl sm:text-2xl font-bold text-gray-800 truncate">Customer: {displayTitle}</h1>
          {isTerminated && (
            <span className="px-2 py-0.5 bg-red-100 text-red-700 text-xs font-medium rounded-full shrink-0">Terminated</span>
          )}
          {isCommissioned && (
            <span className="px-2 py-0.5 bg-green-100 text-green-700 text-xs font-medium rounded-full shrink-0">Active</span>
          )}
        </div>
        <div className="flex flex-wrap gap-2 shrink-0">
          {!editing && (
            <>
              <button
                onClick={() => navigate(`/customer-data?account=${accountNumbers[0] || accountNumber || urlParam}`)}
                className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700"
              >View Data</button>
              {canWriteCustomers && (
                <button onClick={() => setEditing(true)} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700">Edit</button>
              )}
              {isCommissioned ? (
                <>
                  {canWrite && (
                    <button
                      onClick={() => setShowCreditWizard(true)}
                      className="px-4 py-2 bg-amber-600 text-white rounded-lg text-sm hover:bg-amber-700"
                    >
                      Extend Credit
                    </button>
                  )}
                  {canWriteCustomers && (
                    <button
                      onClick={handleDecommission}
                      disabled={decommissioning}
                      className="px-4 py-2 bg-red-600 text-white rounded-lg text-sm hover:bg-red-700 disabled:opacity-50"
                    >
                      {decommissioning ? 'Decommissioning...' : 'Decommission'}
                    </button>
                  )}
                </>
              ) : (
                canWriteCustomers && (
                <>
                  <button onClick={() => navigate(`/assign-meter?customer=${accountNumber || urlParam}`)} className="px-4 py-2 bg-emerald-600 text-white rounded-lg text-sm hover:bg-emerald-700">Assign Meter</button>
                  <button onClick={() => navigate(`/commission?customer=${accountNumber || urlParam}`)} className="px-4 py-2 bg-amber-600 text-white rounded-lg text-sm hover:bg-amber-700">Commission</button>
                </>
                )
              )}
            </>
          )}
          {editing && (
            <>
              <button onClick={handleSave} disabled={saving} className="px-4 py-2 bg-green-600 text-white rounded-lg text-sm hover:bg-green-700 disabled:opacity-50">
                {saving ? 'Saving...' : 'Save'}
              </button>
              <button onClick={() => setEditing(false)} className="px-4 py-2 bg-gray-200 rounded-lg text-sm hover:bg-gray-300">Cancel</button>
            </>
          )}
          {(isSuperadmin || user?.role === 'onm_team') && (
            <button onClick={handleDelete} className="px-4 py-2 bg-red-100 text-red-700 rounded-lg text-sm hover:bg-red-200">Delete</button>
          )}
        </div>
      </div>

      {error && <p className="text-red-600 text-sm bg-red-50 p-2 rounded">{error}</p>}

      {accountNumbers.length > 0 && (
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Account Numbers</h2>
          <div className="flex flex-wrap gap-2">
            {accountNumbers.map(acct => (
              <button
                key={acct}
                onClick={() => navigate(`/customer-data?account=${acct}`)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-blue-50 text-blue-700 rounded-lg text-sm font-mono font-medium hover:bg-blue-100 transition"
              >
                {acct}
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7l5 5m0 0l-5 5m5-5H6" />
                </svg>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="bg-white rounded-lg shadow">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-px bg-gray-200">
          {fields.map(field => (
            <div key={field} className="bg-white p-3 sm:p-4">
              <label className="block text-xs font-medium text-gray-500 uppercase tracking-wide mb-1 truncate">{field}</label>
              {editing ? (
                <input
                  value={formData[field] || ''}
                  onChange={e => setFormData(prev => ({ ...prev, [field]: e.target.value }))}
                  className="w-full px-2 py-1.5 border rounded text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                />
              ) : (
                <p className="text-sm text-gray-800 break-words">{record[field] != null ? String(record[field]) : <span className="text-gray-300">--</span>}</p>
              )}
            </div>
          ))}
        </div>
      </div>

      {contracts.length > 0 && (
        <div className="bg-white rounded-lg shadow p-4 sm:p-5">
          <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wide mb-3">Contracts</h2>
          <div className="space-y-2">
            {contracts.map((c, i) => (
              <a
                key={i}
                href={c.url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg hover:bg-gray-100 transition"
              >
                <svg className="w-5 h-5 text-red-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                </svg>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-800 truncate">{c.filename}</p>
                  <p className="text-xs text-gray-400">{c.lang === 'en' ? 'English' : 'Sesotho'}</p>
                </div>
                <svg className="w-4 h-4 text-gray-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                </svg>
              </a>
            ))}
          </div>
        </div>
      )}

      {showCreditWizard && accountNumber && (
        <ExtendCreditWizard
          accountNumber={accountNumber}
          onClose={() => setShowCreditWizard(false)}
          onCreated={() => {
            getCustomerContracts(accountNumber)
              .then(({ contracts: c }) => setContracts(c))
              .catch(() => {});
          }}
        />
      )}
    </div>
  );
}
