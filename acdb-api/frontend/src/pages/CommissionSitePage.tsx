import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  getGensiteVendors,
  commissionGensite,
  type GensiteCommissionRequest,
  type GensiteEquipmentInput,
  type GensiteCredentialInput,
  type GensiteVendor,
  type GensiteCredentialSpec,
} from '../lib/api';

const EQUIPMENT_KINDS = ['inverter', 'bms', 'battery', 'pv_meter', 'load_meter', 'scada', 'other'];
const EQUIPMENT_ROLES = ['grid_forming', 'pv_input', 'hybrid', 'storage', 'monitor'];

export default function CommissionSitePage() {
  const navigate = useNavigate();

  const [vendors, setVendors] = useState<GensiteVendor[]>([]);
  const [cryptoOk, setCryptoOk] = useState<boolean>(true);
  const [loadErr, setLoadErr] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Site fields
  const [siteCode, setSiteCode] = useState('');
  const [country, setCountry] = useState('LS');
  const [kind, setKind] = useState('minigrid');
  const [displayName, setDisplayName] = useState('');
  const [district, setDistrict] = useState('');
  const [ugpProjectId, setUgpProjectId] = useState('');
  const [gpsLat, setGpsLat] = useState('');
  const [gpsLon, setGpsLon] = useState('');
  const [notes, setNotes] = useState('');

  // Equipment rows
  const [equipment, setEquipment] = useState<GensiteEquipmentInput[]>([
    { kind: 'inverter', vendor: 'victron', model: '', serial: '', role: 'grid_forming' },
  ]);

  // Credential rows — keyed by vendor+backend
  const [credentials, setCredentials] = useState<GensiteCredentialInput[]>([]);

  useEffect(() => {
    getGensiteVendors()
      .then(r => {
        setVendors(r.vendors);
        setCryptoOk(r.crypto_configured);
      })
      .catch(e => setLoadErr(String(e)));
  }, []);

  // Distinct vendors from equipment drive the credential slots shown.
  const requiredVendors = useMemo(() => {
    const set = new Set<string>();
    for (const e of equipment) if (e.vendor) set.add(e.vendor.toLowerCase());
    return Array.from(set);
  }, [equipment]);

  // Reconcile credentials list with required vendors.
  useEffect(() => {
    setCredentials(prev => {
      const next: GensiteCredentialInput[] = [];
      for (const v of requiredVendors) {
        const vendorDesc = vendors.find(vd => vd.vendor === v);
        const spec: GensiteCredentialSpec | undefined = vendorDesc?.credential_specs[0];
        const existing = prev.find(p => p.vendor === v);
        if (existing) { next.push(existing); continue; }
        if (spec) {
          next.push({
            vendor: v,
            backend: spec.backend,
            base_url: '',
            username: '',
            secret: '',
            api_key: '',
            site_id_on_vendor: '',
            extra: {},
          });
        }
      }
      return next;
    });
  }, [requiredVendors, vendors]);

  function updateEquipment(i: number, patch: Partial<GensiteEquipmentInput>) {
    setEquipment(prev => prev.map((e, idx) => (idx === i ? { ...e, ...patch } : e)));
  }
  function addEquipment() {
    setEquipment(prev => [...prev, { kind: 'inverter', vendor: vendors[0]?.vendor || 'victron' }]);
  }
  function removeEquipment(i: number) {
    setEquipment(prev => prev.filter((_, idx) => idx !== i));
  }

  function updateCredential(v: string, patch: Partial<GensiteCredentialInput>) {
    setCredentials(prev => prev.map(c => (c.vendor === v ? { ...c, ...patch } : c)));
  }

  async function submit() {
    setErr(null);
    setResult(null);
    if (!siteCode || !displayName) {
      setErr('Site code and display name are required.');
      return;
    }
    const body: GensiteCommissionRequest = {
      site_code: siteCode.toUpperCase().trim(),
      country: country.toUpperCase().trim(),
      kind,
      display_name: displayName.trim(),
      district: district.trim() || undefined,
      ugp_project_id: ugpProjectId.trim() || undefined,
      gps_lat: gpsLat ? parseFloat(gpsLat) : undefined,
      gps_lon: gpsLon ? parseFloat(gpsLon) : undefined,
      notes: notes.trim() || undefined,
      equipment: equipment.map(e => ({
        ...e,
        vendor: e.vendor.toLowerCase(),
        nameplate_kw: e.nameplate_kw ? Number(e.nameplate_kw) : undefined,
        nameplate_kwh: e.nameplate_kwh ? Number(e.nameplate_kwh) : undefined,
      })),
      credentials: credentials.map(c => ({
        ...c,
        vendor: c.vendor.toLowerCase(),
        backend: c.backend.toLowerCase(),
      })),
    };

    try {
      setSubmitting(true);
      const resp = await commissionGensite(body);
      const verifies = resp.credentials
        .map(v => `${v.credential.vendor}/${v.credential.backend}: ${v.verify.ok ? '✅' : '❌'} ${v.verify.message}`)
        .join('\n');
      setResult(`Commissioned ${resp.site.code}. Equipment: ${resp.equipment.length}. ${resp.credentials.length ? '\nCredentials:\n' + verifies : ''}`);
      // Navigate to the dashboard for the new site after a short beat
      setTimeout(() => navigate(`/gensite/${encodeURIComponent(resp.site.code)}`), 1500);
    } catch (e) {
      setErr(String(e));
    } finally {
      setSubmitting(false);
    }
  }

  if (loadErr) return <div className="p-6 text-sm text-red-600">{loadErr}</div>;

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Commission generation site</h1>
        <p className="text-sm text-gray-500">
          Declares installed equipment and stores vendor backend credentials (encrypted at rest).
        </p>
      </div>

      {!cryptoOk && (
        <div className="border border-red-300 bg-red-50 rounded-lg p-3 text-sm text-red-700">
          <strong>Credential encryption not configured.</strong> Set <code>CC_CREDENTIAL_ENCRYPTION_KEY</code>
          {' '}in <code>/opt/1pdb/.env</code> before commissioning. Details:{' '}
          <code>docs/ops/gensite-credentials.md</code>.
        </div>
      )}

      {/* Site section */}
      <section className="bg-white border rounded-xl p-4 space-y-3">
        <h2 className="font-semibold">Site</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Site code (UGP / country_config)"><input value={siteCode} onChange={e => setSiteCode(e.target.value)} className={inputCls} placeholder="MAK, LSB, GBO, PIH01…" /></Field>
          <Field label="Country"><select value={country} onChange={e => setCountry(e.target.value)} className={inputCls}><option>LS</option><option>BN</option><option>ZM</option></select></Field>
          <Field label="Display name"><input value={displayName} onChange={e => setDisplayName(e.target.value)} className={inputCls} placeholder="Ha Makebe" /></Field>
          <Field label="Kind"><select value={kind} onChange={e => setKind(e.target.value)} className={inputCls}><option value="minigrid">Minigrid</option><option value="health_center">Health centre</option><option value="other">Other</option></select></Field>
          <Field label="District"><input value={district} onChange={e => setDistrict(e.target.value)} className={inputCls} /></Field>
          <Field label="UGP project ID"><input value={ugpProjectId} onChange={e => setUgpProjectId(e.target.value)} className={inputCls} placeholder="(optional)" /></Field>
          <Field label="GPS lat"><input value={gpsLat} onChange={e => setGpsLat(e.target.value)} className={inputCls} /></Field>
          <Field label="GPS lon"><input value={gpsLon} onChange={e => setGpsLon(e.target.value)} className={inputCls} /></Field>
        </div>
        <Field label="Notes"><textarea value={notes} onChange={e => setNotes(e.target.value)} className={inputCls} rows={2} /></Field>
      </section>

      {/* Equipment */}
      <section className="bg-white border rounded-xl p-4 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">Installed equipment</h2>
          <button type="button" onClick={addEquipment} className="text-sm px-3 py-1 bg-gray-100 hover:bg-gray-200 rounded-lg">+ Add equipment</button>
        </div>
        {equipment.map((e, i) => (
          <div key={i} className="grid gap-2 sm:grid-cols-6 border-t pt-3 first:border-t-0 first:pt-0">
            <Field label="Kind"><select value={e.kind} onChange={x => updateEquipment(i, { kind: x.target.value })} className={inputCls}>{EQUIPMENT_KINDS.map(k => <option key={k}>{k}</option>)}</select></Field>
            <Field label="Vendor"><select value={e.vendor} onChange={x => updateEquipment(i, { vendor: x.target.value })} className={inputCls}>{vendors.map(v => <option key={v.vendor} value={v.vendor}>{v.display_name}</option>)}</select></Field>
            <Field label="Model"><input value={e.model || ''} onChange={x => updateEquipment(i, { model: x.target.value })} className={inputCls} /></Field>
            <Field label="Serial"><input value={e.serial || ''} onChange={x => updateEquipment(i, { serial: x.target.value })} className={inputCls} /></Field>
            <Field label="Role"><select value={e.role || ''} onChange={x => updateEquipment(i, { role: x.target.value })} className={inputCls}><option value="">—</option>{EQUIPMENT_ROLES.map(r => <option key={r}>{r}</option>)}</select></Field>
            <Field label="kW"><input value={e.nameplate_kw?.toString() || ''} onChange={x => updateEquipment(i, { nameplate_kw: x.target.value === '' ? undefined : Number(x.target.value) })} className={inputCls} /></Field>
            {equipment.length > 1 && (
              <div className="sm:col-span-6 text-right">
                <button type="button" onClick={() => removeEquipment(i)} className="text-xs text-red-600 hover:underline">Remove</button>
              </div>
            )}
          </div>
        ))}
      </section>

      {/* Credentials */}
      <section className="bg-white border rounded-xl p-4 space-y-4">
        <h2 className="font-semibold">Vendor credentials</h2>
        {credentials.length === 0 && <p className="text-sm text-gray-500">Add equipment above to see the credential fields for its vendor.</p>}
        {credentials.map(c => {
          const vendorDesc = vendors.find(v => v.vendor === c.vendor);
          const spec = vendorDesc?.credential_specs.find(s => s.backend === c.backend);
          return (
            <div key={c.vendor} className="border-t pt-3 first:border-t-0 first:pt-0">
              <div className="flex items-baseline justify-between">
                <h3 className="font-medium">{vendorDesc?.display_name || c.vendor}</h3>
                <span className="text-xs text-gray-500">{c.backend} · {vendorDesc?.implementation_status}</span>
              </div>
              {spec?.notes && <p className="text-xs text-gray-500 mt-1">{spec.notes}</p>}
              <div className="grid gap-2 sm:grid-cols-2 mt-2">
                {spec?.plain_fields.includes('username') && (
                  <Field label="Username / email"><input value={c.username || ''} onChange={e => updateCredential(c.vendor, { username: e.target.value })} className={inputCls} autoComplete="off" /></Field>
                )}
                {spec?.secret_fields.includes('secret') && (
                  <Field label="Password / portal secret"><input type="password" value={c.secret || ''} onChange={e => updateCredential(c.vendor, { secret: e.target.value })} className={inputCls} autoComplete="new-password" /></Field>
                )}
                {spec?.secret_fields.includes('api_key') && (
                  <Field label="API key / token / appSecret"><input type="password" value={c.api_key || ''} onChange={e => updateCredential(c.vendor, { api_key: e.target.value })} className={inputCls} autoComplete="new-password" /></Field>
                )}
                {spec?.plain_fields.includes('site_id_on_vendor') && (
                  <Field label="Vendor site / station ID"><input value={c.site_id_on_vendor || ''} onChange={e => updateCredential(c.vendor, { site_id_on_vendor: e.target.value })} className={inputCls} placeholder="(blank to auto-discover)" /></Field>
                )}
                {spec?.plain_fields.includes('base_url') && (
                  <Field label="Base URL (override)"><input value={c.base_url || ''} onChange={e => updateCredential(c.vendor, { base_url: e.target.value })} className={inputCls} placeholder="(blank to use adapter default)" /></Field>
                )}
                {spec?.extra_fields.map(f => (
                  <Field key={f} label={`${f} (extra)`}>
                    <input
                      value={String((c.extra?.[f] as string) || '')}
                      onChange={e => updateCredential(c.vendor, { extra: { ...(c.extra || {}), [f]: e.target.value } })}
                      className={inputCls}
                    />
                  </Field>
                ))}
              </div>
            </div>
          );
        })}
      </section>

      {err && <div className="border border-red-300 bg-red-50 rounded-lg p-3 text-sm text-red-700 whitespace-pre-wrap">{err}</div>}
      {result && <div className="border border-green-300 bg-green-50 rounded-lg p-3 text-sm text-green-800 whitespace-pre-wrap">{result}</div>}

      <div className="flex justify-end gap-2">
        <button type="button" onClick={() => navigate('/gensite')} className="px-4 py-2 bg-gray-100 hover:bg-gray-200 rounded-lg text-sm">Cancel</button>
        <button type="button" disabled={submitting || !cryptoOk} onClick={submit} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">
          {submitting ? 'Commissioning…' : 'Commission site'}
        </button>
      </div>
    </div>
  );
}

const inputCls = 'w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none';

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs text-gray-500 mb-1">{label}</label>
      {children}
    </div>
  );
}
