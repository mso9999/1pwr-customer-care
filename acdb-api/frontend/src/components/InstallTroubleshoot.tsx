import { useMemo, useState } from 'react';

/**
 * Guided troubleshooter for a gateway install that hasn't verified (gateway
 * bound to a pole's PTB but no cloud contact). Walks the field SOP as a
 * decision tree, pre-filled with what CC already knows about the unit
 * (expected WiFi SSID, provisioning state, whether it's ever connected, and
 * whether site neighbors are online), and ends at either verified or an
 * escalation summary the tech copies to HQ.
 */

export interface TroubleshootContext {
  gateway_thing: string;
  pole_id: string;
  site: string;
  /** Expected deployment WiFi SSID from the provisioning registry. */
  expected_ssid?: string;
  /** Registry status (provisioned/online/...). */
  prov_status?: string;
  /** Whether the gateway has ever connected to the cloud (fleet/registry). */
  ever_online?: boolean;
  /** Whether any other gateway at the site is currently online (mesh neighbor). */
  neighbors_online?: boolean;
}

interface StepOption {
  label: string;
  next: string;
  tone?: 'primary' | 'ok' | 'danger' | 'muted';
}

interface Step {
  title: string;
  body: (c: TroubleshootContext) => string;
  hint?: (c: TroubleshootContext) => string | null;
  options: StepOption[];
  terminal?: 'done' | 'escalate';
}

const S = (title: string, body: Step['body'], options: StepOption[], hint?: Step['hint'], terminal?: Step['terminal']): [string, Step] =>
  [title, { title, body, options, hint, terminal }];

// The decision tree. Keyed by step id.
const STEPS: Record<string, Step> = Object.fromEntries([
  S('led',
    (c) => `Look at the gateway PCB inside the PTB on pole ${c.pole_id}. The status LED gives one short flash every ~5 seconds when the firmware is running. Is the LED flashing?`,
    [
      { label: 'Yes, flashing', next: 'network', tone: 'ok' },
      { label: 'No / not sure', next: 'power', tone: 'danger' },
    ],
    () => null),

  S('power',
    () => `No LED heartbeat means no power or not booting. Work these in order:\n\n1. Confirm the meter is live (its display is on) — the gateway draws power from the meter's L/N passthrough.\n2. Re-seat the gateway's power leads at the meter/PTB terminals.\n3. Confirm the PTB enclosure terminals are made and no fuse/link is open.\n4. If you can, power the PCB from bench USB — if it boots on USB but not in the PTB, it's the wiring/supply, not the unit.`,
    [
      { label: 'Fixed — LED flashing now', next: 'network', tone: 'ok' },
      { label: 'Still dead', next: 'escalate_power', tone: 'danger' },
    ],
    () => 'Most common install failure is power. Don\'t skip the bench-USB test if you can.') ,

  S('network',
    (c) => `The gateway joins the site's internet over WiFi (directly, or meshing through a nearby online unit). It only looks for the network it was provisioned for.${c.expected_ssid ? `\n\nThis unit expects SSID: ${c.expected_ssid}` : ''}\n\nIs the site router/Starlink online and broadcasting that SSID right now? (Check with another device — does it get internet?)`,
    [
      { label: 'Yes, network is up', next: 'credentials', tone: 'ok' },
      { label: 'No / router down', next: 'fix_router', tone: 'danger' },
      { label: 'SSID is different / changed', next: 'credentials', tone: 'primary' },
    ],
    (c) => c.expected_ssid ? null : 'CC has no deployment-SSID on record for this unit — confirm the site network name with HQ.'),

  S('fix_router',
    () => `The gateway can't reach the cloud while the site's router/backhaul is down. Fix the router/Starlink first — no amount of work on the gateway helps until the network it joins is up.`,
    [
      { label: 'Router is back up — recheck', next: 'recheck', tone: 'primary' },
    ]),

  S('credentials',
    () => `A gateway holds the WiFi credentials from its provisioning. If the site's WiFi password (or SSID) changed after this unit was provisioned, it sees the network but can't join it.\n\nHas the site's WiFi password or network name changed since this gateway was provisioned?`,
    [
      { label: 'Yes, it changed', next: 'ota_network', tone: 'danger' },
      { label: 'No / unchanged', next: 'mesh', tone: 'ok' },
      { label: 'Not sure', next: 'mesh', tone: 'muted' },
    ],
    () => 'Pushing new credentials is a safe OTA config update (cfg/network) — the unit rolls back to its old credentials if the new ones fail.'),

  S('ota_network',
    (c) => `The unit needs the current site credentials pushed to it. This is done from HQ as an OTA config update (cfg/network) — it applies on next connect, or can be re-applied on the bench.\n\nFlag it to HQ with the Thing name (${c.gateway_thing}) and the correct site SSID + password.`,
    [
      { label: 'Credentials pushed — recheck', next: 'recheck', tone: 'primary' },
      { label: 'Need HQ to do it — escalate', next: 'escalate', tone: 'muted' },
    ]),

  S('mesh',
    (c) => `Provisioned units self-organize: one connects to the router (root), the rest mesh through neighbors. Your unit may be a leaf with no online root in range.\n\nIs another gateway nearby currently online (green in CC / Fleet live)?${c.neighbors_online === false ? '\n\nCC shows no other gateway online at this site right now — the cluster\'s root/backhaul is likely the problem, not your unit.' : ''}`,
    [
      { label: 'Yes, a neighbor is online', next: 'powercycle', tone: 'ok' },
      { label: 'No neighbor online', next: 'fix_backhaul', tone: 'danger' },
      { label: 'Not sure', next: 'powercycle', tone: 'muted' },
    ],
    (c) => c.neighbors_online === false ? null : null),

  S('powercycle',
    () => `A neighbor is online, so the mesh has a path. Power-cycle your unit (off ~5 s, on) — it re-attempts the mesh join on boot. Give it a minute, then watch the CC installations board.`,
    [
      { label: 'Recheck now', next: 'recheck', tone: 'primary' },
    ]),

  S('fix_backhaul',
    () => `No neighbor is online either — the whole cluster is down, so the problem is upstream (router/root/backhaul), not your unit. Restore the cluster's root/backhaul first, then your unit should mesh up on its own.`,
    [
      { label: 'Cluster back up — recheck', next: 'recheck', tone: 'primary' },
      { label: 'Can\'t restore here — escalate', next: 'escalate', tone: 'muted' },
    ]),

  S('identity',
    (c) => `Last field check — confirm the unit is provisioned and is the unit you bound (${c.gateway_thing}). There's no serial/USB access to an installed unit, so do this over the network:\n\nPower the unit and watch CC — the Thing that comes online in the fleet/installations view is the unit in this PTB. Is it the one you bound?\n\n(If you can reach the unit over the site network, http://<unit-ip>/v1/provision/status also reports its thing_name + provisioned state.)`,
    [
      { label: 'It came online and it\'s the right unit', next: 'recheck', tone: 'ok' },
      { label: 'A different Thing came online', next: 'rebind', tone: 'danger' },
      { label: 'It won\'t come online at all', next: 'cloud', tone: 'muted' },
    ],
    () => 'A virgin/factory unit never joins the site network — it hunts the provisioning bench network and must be provisioned first. If nothing comes online when you power it, that\'s likely why.'),

  S('reprovision',
    (c) => `This unit isn't fully provisioned (factory state, or missing WiFi/TLS material). It can't work at a site until it's provisioned.\n\nTake it through the provisioning station, then redo the install binding for ${c.gateway_thing}.`,
    [
      { label: 'Understood — escalate to reprovision', next: 'escalate', tone: 'primary' },
    ]),

  S('rebind',
    (c) => `The physical unit's identity doesn't match the install record (${c.gateway_thing}). You bound the wrong unit.\n\nCorrect the install in CC: rebind the pole to the Thing name the unit actually reports.`,
    [
      { label: 'Understood — fix the binding', next: 'escalate', tone: 'primary' },
    ]),

  S('cloud',
    () => `Power, network, credentials, mesh and identity all check out, but the cloud still refuses the connection. This is the rare case — the AWS IoT Thing may be inactive, or the certificate/policy is wrong. Not fixable in the field.`,
    [
      { label: 'Escalate to HQ', next: 'escalate', tone: 'primary' },
    ]),

  S('recheck',
    (c) => `Watch the CC installations board (Provisioning → Field install) for up to ~2 minutes. A gateway that connects flips from "awaiting contact" to "verified online" on its own.\n\nDid ${c.gateway_thing} verify?`,
    [
      { label: 'Yes — verified online', next: 'done', tone: 'ok' },
      { label: 'Still awaiting contact', next: 'escalate', tone: 'danger' },
    ]),

  // Terminal states
  S('done',
    () => `Verified — the gateway is live on the cloud. The install is complete. You can assign meters to its channels (meter edit modal → pick this pole; the gateway inherits from the PTB).`,
    [],
    undefined,
    'done'),
  S('escalate_power',
    () => `The unit is dead on the bench too, or the PTB/meter supply can't power it. This needs a hardware swap or a wiring fix beyond field scope.`,
    [],
    undefined,
    'escalate'),
  S('escalate',
    () => `You've worked the field checks. Time to hand it to HQ with the details below.`,
    [],
    undefined,
    'escalate'),
]);

const TONE: Record<string, string> = {
  primary: 'bg-blue-600 text-white hover:bg-blue-700',
  ok: 'bg-green-600 text-white hover:bg-green-700',
  danger: 'bg-red-600 text-white hover:bg-red-700',
  muted: 'bg-gray-200 text-gray-700 hover:bg-gray-300',
};

interface Props {
  context: TroubleshootContext;
  onClose: () => void;
  /** Re-poll the installations board (parent) so a recheck reflects live state. */
  onRecheck?: () => void;
}

export default function InstallTroubleshoot({ context, onClose, onRecheck }: Props) {
  const [stepId, setStepId] = useState('led');
  const [path, setPath] = useState<string[]>(['led']);
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
      `Gateway install troubleshooting — needs HQ`,
      `Thing: ${context.gateway_thing}`,
      `Pole: ${context.pole_id}  ·  Site: ${context.site}`,
      context.expected_ssid ? `Expected WiFi SSID: ${context.expected_ssid}` : null,
      `Path taken: ${trail}`,
      `Time: ${new Date().toLocaleString()}`,
      ``,
      `Please attach: photo of the PTB wiring + meter terminals, and the /v1/provision/status JSON if readable.`,
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
            <div className="text-sm font-semibold text-gray-800">Troubleshoot install</div>
            <div className="text-xs text-gray-500 font-mono">{context.gateway_thing} · {context.pole_id}</div>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
        </div>

        <div className="p-5">
          {/* progress trail */}
          <div className="flex gap-1 mb-4 flex-wrap">
            {path.map((_, i) => (
              <span key={i} className={`h-1.5 rounded-full ${i === path.length - 1 ? 'bg-blue-500 w-6' : 'bg-gray-300 w-3'}`} />
            ))}
          </div>

          <p className="text-sm text-gray-800 whitespace-pre-wrap leading-relaxed">{step.body(context)}</p>
          {step.hint?.(context) && (
            <p className="mt-3 text-xs text-blue-700 bg-blue-50 border border-blue-100 rounded-lg p-2.5">{step.hint(context)}</p>
          )}

          {/* options */}
          {step.options.length > 0 && (
            <div className="mt-5 space-y-2">
              {step.options.map((o) => (
                <button
                  key={o.label}
                  onClick={() => go(o.next)}
                  className={`w-full py-2.5 px-4 rounded-xl text-sm font-medium text-left transition ${TONE[o.tone || 'primary']}`}
                >
                  {o.label}
                </button>
              ))}
            </div>
          )}

          {/* terminal: done */}
          {step.terminal === 'done' && (
            <div className="mt-5">
              <div className="p-3 bg-green-50 border border-green-200 rounded-lg text-green-800 text-sm font-medium">Install verified. You're done.</div>
              <button onClick={onClose} className="mt-3 w-full py-2.5 bg-blue-600 text-white rounded-xl text-sm font-semibold hover:bg-blue-700">Close</button>
            </div>
          )}

          {/* terminal: escalate */}
          {step.terminal === 'escalate' && (
            <div className="mt-5 space-y-3">
              <div className="text-xs font-medium text-gray-500 uppercase tracking-wide">Send this to HQ</div>
              <pre className="text-xs bg-gray-50 border border-gray-200 rounded-lg p-3 whitespace-pre-wrap font-mono text-gray-700">{escalateSummary}</pre>
              <div className="flex gap-2">
                <button onClick={copy} className="flex-1 py-2.5 bg-blue-600 text-white rounded-xl text-sm font-semibold hover:bg-blue-700">
                  {copied ? 'Copied' : 'Copy summary'}
                </button>
                <button onClick={onClose} className="flex-1 py-2.5 bg-gray-200 text-gray-700 rounded-xl text-sm font-semibold hover:bg-gray-300">Close</button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
