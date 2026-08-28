import { useMemo, useState } from 'react';

/**
 * Guided troubleshooter for a 1Meter meter that isn't reporting through its
 * gateway (or whose commissioning was blocked because the gateway can't read
 * it). The meter-side tree: gateway online → meter powered → RS-485 link →
 * Modbus address → recheck → escalate. Pre-filled with what CC knows (gateway
 * live state, meter serial, pole). Ends at reporting or a copyable HQ summary.
 */

export interface MeterTroubleContext {
  meter_serial: string;
  account_number?: string;
  gateway_thing?: string;
  pole_id?: string;
  site: string;
  /** Gateway live state from the fleet index: online | recent | offline | never | none. */
  gateway_state?: string;
  gateway_age_h?: number | null;
}

interface StepOption { label: string; next: string; tone?: 'primary' | 'ok' | 'danger' | 'muted'; }
interface Step {
  title: string;
  body: (c: MeterTroubleContext) => string;
  hint?: (c: MeterTroubleContext) => string | null;
  options: StepOption[] | ((c: MeterTroubleContext) => StepOption[]);
  terminal?: 'done' | 'escalate';
}

const S = (title: string, body: Step['body'], options: Step['options'], hint?: Step['hint'], terminal?: Step['terminal']): [string, Step] =>
  [title, { title, body, options, hint, terminal }];

const STEPS: Record<string, Step> = Object.fromEntries([
  S('gw_online',
    (c) => {
      const gw = c.gateway_thing || 'the gateway';
      if (c.gateway_state === 'online' || c.gateway_state === 'recent') {
        return `The meter reports through its gateway (${gw}), which is online — good. So the problem is between the meter and the gateway, or the meter itself.\n\nIs the meter powered — is its display on?`;
      }
      return `The meter reports through its gateway (${gw}), and that gateway is NOT online (state: ${c.gateway_state || 'unknown'}${c.gateway_age_h != null ? `, last contact ${c.gateway_age_h}h ago` : ''}).\n\nA meter can't report without its gateway. Verify the gateway first (Provisioning → Field install → Troubleshoot), then come back to the meter.\n\nOnce the gateway is online: is the meter's display on?`;
    },
    (c) => (c.gateway_state === 'online' || c.gateway_state === 'recent')
      ? [
          { label: 'Meter display is ON', next: 'rs485', tone: 'ok' },
          { label: 'Meter display is OFF', next: 'meter_power', tone: 'danger' },
        ]
      : [
          { label: 'Gateway is now online — meter display ON', next: 'rs485', tone: 'ok' },
          { label: 'Gateway is now online — meter display OFF', next: 'meter_power', tone: 'danger' },
          { label: 'Gateway still offline — troubleshoot it first', next: 'escalate_gw', tone: 'muted' },
        ],
    (c) => (c.gateway_state === 'online' || c.gateway_state === 'recent') ? null
        : 'Don\'t chase the meter until the gateway is live — nothing the meter does reaches the cloud without it.'),

  S('meter_power',
    () => `The meter's display is off — it's not powered. The gateway can't read a dead meter.\n\nCheck:\n1. Is the customer's supply live (is there power at the house)?\n2. The meter's L/N supply terminals — are they made and tight?\n3. Is the meter itself tripped/faulty?`,
    [
      { label: 'Meter powered now (display on)', next: 'rs485', tone: 'ok' },
      { label: 'Still dead', next: 'escalate', tone: 'danger' },
    ],
    () => null),

  S('rs485',
    () => `The meter talks to the gateway over RS-485 (two data wires + ground). This is the most common failure.\n\nCheck, in order:\n1. A/B polarity — swap the two data wires on the meter's RS-485 A/B terminals. (Polarity is the #1 cause.)\n2. Add/confirm the GND wire between the adapter/gateway and the meter — A/B alone is unreliable without a common ground.\n3. Confirm the data wires are on the meter's RS-485 A and B terminals (marked A/485A, B/485B), NOT the power or pulse terminals.`,
    [
      { label: 'Fixed / re-wired — recheck', next: 'modbus_addr', tone: 'primary' },
      { label: 'Wiring is definitely right', next: 'modbus_addr', tone: 'muted' },
    ],
    () => 'This is a visual/physical check inside the PTB — no laptop or serial tool needed.'),

  S('modbus_addr',
    () => `The gateway polls the meter at a specific Modbus address, which is set at the bench before installation — there's no serial/scan access to an installed meter, so this can't be re-checked in the field.\n\nIf the RS-485 wiring is confirmed right (previous step) and the meter is powered but the gateway still gets silence, the address is the likely culprit — and that's a bench fix, not a field one.`,
    [
      { label: 'Wiring confirmed right — still silent', next: 'escalate', tone: 'danger' },
      { label: 'Found a wiring issue — fixed it, recheck', next: 'recheck', tone: 'ok' },
    ],
    () => 'Modbus re-addressing needs the bench tool — an installed meter can\'t be re-scanned in the field. If it comes to that, the unit goes back to HQ.'),

  S('recheck',
    (c) => `Watch CC for the meter's first reading — the meter's "last seen" updates and it goes green on the fleet map once the gateway reports it (within a few minutes).\n\nIs meter ${c.meter_serial} reporting now?`,
    [
      { label: 'Yes — reporting', next: 'done', tone: 'ok' },
      { label: 'Still silent', next: 'escalate', tone: 'danger' },
    ]),

  S('done',
    () => `The meter is reporting through its gateway. Done.`,
    [], undefined, 'done'),
  S('escalate_gw',
    (c) => `The gateway (${c.gateway_thing || 'unknown'}) isn't online, so the meter can't report. This is a gateway-side problem — run the gateway install troubleshooter (Provisioning → Field install → Troubleshoot) or escalate.`,
    [], undefined, 'escalate'),
  S('escalate',
    () => `You've worked the meter-side checks. Hand it to HQ with the details below.`,
    [], undefined, 'escalate'),
]);

const TONE: Record<string, string> = {
  primary: 'bg-blue-600 text-white hover:bg-blue-700',
  ok: 'bg-green-600 text-white hover:bg-green-700',
  danger: 'bg-red-600 text-white hover:bg-red-700',
  muted: 'bg-gray-200 text-gray-700 hover:bg-gray-300',
};

interface Props {
  context: MeterTroubleContext;
  onClose: () => void;
  onRecheck?: () => void;
}

export default function MeterTroubleshoot({ context, onClose, onRecheck }: Props) {
  const [stepId, setStepId] = useState('gw_online');
  const [path, setPath] = useState<string[]>(['gw_online']);
  const [copied, setCopied] = useState(false);
  const step = STEPS[stepId];

  const go = (next: string) => {
    if (next === 'recheck' && onRecheck) onRecheck();
    setStepId(next);
    setPath((p) => [...p, next]);
  };

  const escalateSummary = useMemo(() => {
    const trail = path.map((id) => STEPS[id]?.title || id).join(' → ');
    return [
      `Meter not reporting — needs HQ`,
      `Meter: ${context.meter_serial}${context.account_number ? `  ·  Account: ${context.account_number}` : ''}`,
      context.gateway_thing ? `Gateway: ${context.gateway_thing} (state: ${context.gateway_state || 'unknown'})` : null,
      context.pole_id ? `Pole: ${context.pole_id}` : null,
      `Site: ${context.site}`,
      `Path taken: ${trail}`,
      `Time: ${new Date().toLocaleString()}`,
      ``,
      `Please attach: a photo of the meter terminals + RS-485 wiring inside the PTB, and whether the meter display is on.`,
    ].filter(Boolean).join('\n');
  }, [path, context]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(escalateSummary);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard unavailable */ }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl max-w-lg w-full max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <div>
            <div className="text-sm font-semibold text-gray-800">Troubleshoot meter reporting</div>
            <div className="text-xs text-gray-500 font-mono">meter {context.meter_serial}{context.gateway_thing ? ` · ${context.gateway_thing}` : ''}</div>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
        </div>

        <div className="p-5">
          <div className="flex gap-1 mb-4 flex-wrap">
            {path.map((_, i) => (
              <span key={i} className={`h-1.5 rounded-full ${i === path.length - 1 ? 'bg-blue-500 w-6' : 'bg-gray-300 w-3'}`} />
            ))}
          </div>

          <p className="text-sm text-gray-800 whitespace-pre-wrap leading-relaxed">{step.body(context)}</p>
          {step.hint?.(context) && (
            <p className="mt-3 text-xs text-blue-700 bg-blue-50 border border-blue-100 rounded-lg p-2.5">{step.hint(context)}</p>
          )}

          {(() => {
            const opts = typeof step.options === 'function' ? step.options(context) : step.options;
            return opts.length > 0 && (
              <div className="mt-5 space-y-2">
                {opts.map((o) => (
                  <button key={o.label} onClick={() => go(o.next)} className={`w-full py-2.5 px-4 rounded-xl text-sm font-medium text-left transition ${TONE[o.tone || 'primary']}`}>
                    {o.label}
                  </button>
                ))}
              </div>
            );
          })()}

          {step.terminal === 'done' && (
            <div className="mt-5">
              <div className="p-3 bg-green-50 border border-green-200 rounded-lg text-green-800 text-sm font-medium">Meter is reporting. You're done.</div>
              <button onClick={onClose} className="mt-3 w-full py-2.5 bg-blue-600 text-white rounded-xl text-sm font-semibold hover:bg-blue-700">Close</button>
            </div>
          )}

          {step.terminal === 'escalate' && (
            <div className="mt-5 space-y-3">
              <div className="text-xs font-medium text-gray-500 uppercase tracking-wide">Send this to HQ</div>
              <pre className="text-xs bg-gray-50 border border-gray-200 rounded-lg p-3 whitespace-pre-wrap font-mono text-gray-700">{escalateSummary}</pre>
              <div className="flex gap-2">
                <button onClick={copy} className="flex-1 py-2.5 bg-blue-600 text-white rounded-xl text-sm font-semibold hover:bg-blue-700">{copied ? 'Copied' : 'Copy summary'}</button>
                <button onClick={onClose} className="flex-1 py-2.5 bg-gray-200 text-gray-700 rounded-xl text-sm font-semibold hover:bg-gray-300">Close</button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
