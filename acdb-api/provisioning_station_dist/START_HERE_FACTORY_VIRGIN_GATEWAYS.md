# Start here: sealed factory-virgin 1Meter gateways

## Purpose

This is the field/depot procedure for bringing sealed 1Meter gateways from their
factory boot firmware to approved full operational firmware.

It is written for an operator using Windows, such as Comfort in Benin. Follow it
in order. Do not skip the verification gates.

## The most important rule

**Do not use USB. Do not open the gateway. Do not erase or flash it locally.**

A factory-virgin gateway is not blank. The manufacturer has already installed
the factory boot firmware. That firmware knows the approved provisioning
network, exposes the local provisioning service, and contains the OTA client
needed to receive the full signed firmware.

The complete field workflow is:

```text
factory boot firmware
        ↓ joins recognized provisioning LAN
laptop station discovers MAC/serial
        ↓
CC creates permanent Thing + certificate
        ↓ station delivers identity + operational Wi-Fi locally
gateway connects to AWS IoT
        ↓
CC queues signed full-firmware OTA
        ↓
AWS IoT Job = SUCCEEDED for that gateway
        ↓
telemetry verified, then release for installation
```

Delivering the identity/certificate is only an intermediate step. A gateway is
not finished until its full-firmware OTA is `SUCCEEDED`.

---

## What Comfort’s screenshot means

The screenshot shows:

```text
1Meter Provisioning Station
CC:      https://cc.1pwrafrica.com
Subnet:  10.63.249.0/24
Open:    http://localhost:8787
```

This is a successful start. The program has not frozen and it is not supposed
to exit. It is waiting because it is serving the provisioning page.

**Next action:** leave the PowerShell/VS Code terminal open and open this address
in Chrome or Edge:

```text
http://localhost:8787
```

Do not run the Python file again. Do not close the terminal until the batch is
finished.

The subnet `10.63.249.0/24` is correct only if the laptop and factory gateways
are on that provisioning LAN. The scan result is the practical test.

---

## Definitions

- **Factory-virgin:** sealed gateway with factory boot firmware, but without its
  final CC Thing identity, unique device certificate, destination-site Wi-Fi,
  or confirmed full-firmware OTA.
- **Provisioning LAN:** the network the factory boot firmware is programmed to
  recognize. The approved default is currently:
  - SSID: `1Meter`
  - password: `1Meter00`
- **Operational/site Wi-Fi:** the internet-connected network the gateway will
  use after provisioning at its destination: normally that site's unique
  Starlink router. This is entered in the station and must be broadcasting
  within range before the gateway reboots.
- **Thing name:** the gateway’s permanent AWS/CC identity, allocated by CC as
  `<SITE>-GW-####`. It does not become the customer account number later.
- **Bootstrap delivery:** the local transfer of Thing identity, certificate,
  and site Wi-Fi to the gateway. This is not a firmware flash.
- **OTA promotion:** CC creates an AWS IoT signed firmware job for the newly
  provisioned Thing. This is how the full operational firmware is installed.

---

## Before starting

### People and access

You need:

- a CC employee login with Provisioning access;
- the correct destination site code from CC;
- the approved operational Wi-Fi SSID and password for that destination;
- the actual destination Starlink router powered, online, and within range of
  the gateways during the OTA stage;
- an escalation contact in Engineering in case CC’s OTA readiness check fails.

For Benin, select the actual canonical site shown by CC (for example `GBO` or
`SAM`, when applicable). Do not invent `BEN`, `BN`, or another code.

### Equipment

- Windows laptop with Python 3.9 or later;
- Chrome or Edge;
- an access point broadcasting the approved provisioning credentials;
- internet connectivity on that provisioning network, or a second connection
  that lets the laptop reach `https://cc.1pwrafrica.com`;
- the sealed gateways and their printed factory serial numbers;
- the factory serial-to-MAC list for the shipment, if supplied.

### Network requirements

The provisioning access point must:

- broadcast the exact SSID/password recognized by the factory firmware;
- give the laptop and gateways addresses on the same LAN;
- allow local device-to-device traffic on TCP port 80;
- allow internet access to CC;
- allow the provisioned gateways outbound access to AWS IoT on TCP 8883.

Guest Wi-Fi/client-isolation mode must be off. Client isolation prevents the
laptop from seeing the gateways even when all devices show as connected.

Two Wi-Fi networks are involved:

1. The factory gateway first joins the shared `1Meter` provisioning network so
   the laptop can discover and bootstrap it.
2. After bootstrap it leaves that network and joins the selected site's unique
   Starlink network so it can reach AWS IoT and download full firmware.

The destination Starlink network therefore must be powered and within range
during provisioning. If Comfort is provisioning at the deployment site, use the
real site Starlink router. A central depot cannot fully provision a unit for a
remote site unless that destination network is available there under an
approved procedure; a queued OTA is not a completed gateway.

### Optional HQ mirrored-network OTA validation

If the actual destination Starlink router is not available at HQ, the operator
may optionally validate the canary using a controlled Wi-Fi access point that
mirrors the destination credentials. Skip this section when using the real site
Starlink.

The mirror must reproduce the site network exactly:

1. Use a controlled spare router/access point that supports 2.4 GHz Wi-Fi.
2. Set its SSID to the destination Starlink SSID exactly, including
   capitalization, spaces, and punctuation.
3. Set the identical WPA2 password. Do not put the password in CC, chat,
   screenshots, tickets, or documentation.
4. Give the mirror internet access.
5. Disable captive portal, guest isolation, and client isolation.
6. Allow DNS/NTP and outbound AWS IoT MQTT on TCP 8883.
7. Connect a phone/laptop using the mirrored credentials and confirm internet
   access, then disconnect that test device.
8. Avoid broadcasting both the real and mirrored SSID in the same test area.
9. Keep the mirror powered and within range through bootstrap, reboot, and OTA.
10. After the OTA succeeds, power down and label the mirror as test-only.

This validates that the gateway can accept the destination credentials, reboot
onto a network using them, reach AWS IoT, and install the signed OTA. It does
not validate the actual Starlink router, site RF conditions, or the site's
internet path. The gateway still requires a real-site connectivity check during
installation.

### Start with a canary

For a new location or shipment:

1. In CC, select the country and open **Provisioning → Country readiness**.
2. Confirm the site roster and immutable OTA candidate gates are green.
3. If the release says **candidate ready**, select exactly one gateway in the
   station. CC records it as the authorized test unit.
4. Provision that one gateway.
5. Confirm its OTA job is `SUCCEEDED`.
6. Confirm it reports online with the expected full firmware.
7. Run the recommended physical **Batch validation**, or record an explicit
   validation waiver.
8. Engineering/superadmin approves the immutable release in **OTA canary**.
9. Then do a small controlled batch.
10. Scale up only after the small batch passes.

Do not start with all units in a shipment.

---

## Part A — prepare the laptop

1. In CC, select the intended country and open **Provisioning → Country
   readiness**. Do not use another country's site code or database.
2. Continue to **Guide & download** only when the approved site exists and its
   OTA gate identifies the expected target firmware.
3. The OTA box may show:
   - **candidate ready** — select exactly one first-canary gateway;
   - **ready** — the immutable release has already passed canary approval and
     controlled batches are permitted.
4. Download **provisioning-station.zip**.
5. In Windows File Explorer, right-click the ZIP and choose **Extract All**.
6. Open the extracted `provisioning-station` folder.

Do not run the script while it is still inside the ZIP preview.

### Confirm Python

Open PowerShell in the extracted folder:

- click the File Explorer address bar;
- type `powershell`;
- press Enter.

Run:

```powershell
py -3 --version
```

Expected: `Python 3.9` or later.

If Windows says `py` is not recognized, try:

```powershell
python --version
```

If neither command works, install Python and select **Add Python to PATH**.

---

## Part B — prepare the network and gateways

1. Configure/power the approved provisioning access point.
2. Connect the laptop to that network.
3. Confirm the laptop can open:

   ```text
   https://cc.1pwrafrica.com
   ```

4. Power the destination site's Starlink router and verify the exact SSID and
   password on a phone or laptop.
5. Confirm the Starlink router has internet.
6. Place the first sealed gateway within range of both networks.
7. Power the gateway.
8. Wait two minutes for boot and provisioning-network association.

Do not connect a USB cable and do not open the enclosure.

If working with a small batch, power only the gateways intended for that batch.
Keep their printed serial numbers visible.

---

## Part C — start the station

From PowerShell in the extracted folder:

```powershell
py -3 provisioning_station.py --cc https://cc.1pwrafrica.com
```

If `py` is unavailable:

```powershell
python provisioning_station.py --cc https://cc.1pwrafrica.com
```

Expected:

```text
================================================================
 1Meter Provisioning Station
   CC:      https://cc.1pwrafrica.com
   Subnet:  <local network>
   Open:    http://localhost:8787
================================================================
```

The blinking cursor below this banner is normal. Leave PowerShell open.

Open in Chrome/Edge:

```text
http://localhost:8787
```

Select the deployment country (**Benin**, **Zambia**, or **Lesotho**) and sign
in with the CC employee account. This selection controls the database,
canonical sites, currency, and OTA catalog. If the expected site is absent,
stop; do not select another country as a workaround.

### OTA readiness gate

At the top of the station page, confirm:

```text
Full-firmware OTA readiness: ready
```

It must also show the approved target firmware version.

If it says **not ready** or **check failed**, stop. Do not provision a batch.
Capture the message and send it to Engineering. CC may be missing the approved
S3 artifact/version, signing profile access, OTA service role, or IAM permission.

---

## Part D — discover and identify the gateways

1. Review the subnet field.
2. Click **Scan for gateways**.
3. Wait for the scan to finish.

For each intended unit, verify:

- an IP address is shown;
- a real PCB MAC is shown, not `unknown`;
- status is `virgin`;
- the unit belongs to the shipment being processed.

### Match the printed serial to the MAC

Preferred method:

1. Click **Import serial→MAC CSV**.
2. Select the factory shipment list.
3. Confirm the station reports the expected number of pairs.
4. Confirm each discovered MAC receives the correct printed serial.

The importer accepts a normal `serial,mac` CSV and the manufacturer’s
`AP: ... STA: ...` format; the station uses the STA MAC.

If no list is available, identify one powered unit at a time, or use
**Identify by elimination**:

1. Power off exactly one discovered gateway.
2. Click **I just powered off one unit — check which disappeared**.
3. Enter that gateway’s printed serial next to the MAC that disappeared.
4. Save it and power the unit back on.

Never guess a MAC-to-serial match.

### If scan finds zero gateways

Check in this order:

1. The gateway is powered and has had two minutes to boot.
2. The access point uses the exact approved provisioning SSID/password.
3. The laptop is on the same LAN.
4. Guest/client isolation is disabled.
5. The subnet shown by the station matches the laptop’s provisioning-network
   address. On Windows, `ipconfig` shows the laptop IPv4 address.
6. Windows Firewall is not blocking Python on private networks.
7. Retry with one gateway close to the access point.

If other gateways scan correctly but one never appears, quarantine that unit
and record its factory serial. Do not open or erase it.

### If the MAC is `unknown`

- Wait 30 seconds and scan again.
- Confirm the unit is running the cleared factory build that reports `pcb_mac`.
- Try one unit at a time.
- Do not select or provision a row with an unknown MAC; the MAC is CC’s durable
  registry key.

---

## Part E — assign the destination and provision

1. Select only the verified virgin units.
2. Choose the actual destination site from the CC dropdown.
3. Enter the destination site's unique Starlink Wi-Fi SSID exactly.
4. Enter that Starlink Wi-Fi password exactly.
5. Check capitalization, spaces, punctuation, and keyboard layout.
6. Review the count and serial/MAC mappings.
7. Click **Confirm & provision selected**.
8. Read the confirmation dialog and confirm.

The printed factory serial is mandatory for each selected row. The faint
**enter printed serial** text is an instruction, not a completed value. If the
button remains disabled, read the **action required** line immediately below
it. It identifies the missing input, rejected CC role, or OTA-readiness gate.
Use **Retry CC checks** after a network interruption. If an administrator has
changed your CC role, use **Change user / sign in again** to obtain a fresh
session before retrying.

The station now performs two distinct operations:

### Stage 1 — identity/network bootstrap

CC:

- allocates a permanent `<SITE>-GW-####` Thing;
- creates a unique active certificate;
- records the PCB MAC, factory serial/label, site, and operator;
- returns the encrypted identity material to the local station.

The station sends the bootstrap to the gateway’s local HTTP endpoint. The
gateway stores it and reboots onto the operational Wi-Fi.

The Starlink password becomes protected runtime configuration in device NVS. It
is not a separate firmware binary compiled with the site's password. Normal
application OTA preserves it; CC tracks the non-secret SSID/config version and
firmware release separately and never stores the password.

`done (rebooted — confirm via Re-scan)` is normal. The device can reboot before
its final HTTP response reaches the laptop.

### Stage 2 — full-firmware OTA

After successful bootstrap delivery, the station asks CC to:

- target the new Thing(s);
- use the immutable approved firmware object/version;
- sign it with the active 1PWR OTA signing profile;
- create an AWS IoT OTA update and Job;
- show the update ID and per-gateway job status.

For a candidate-only release, the station permits exactly one gateway, marks it
as the authorized test unit in both CC and the device registry, and supplies
the required canary confirmation. Batch scheduling remains locked until an
authorized reviewer records the successful canary evidence in CC.

Keep the gateways powered. Do not disconnect the access point or internet.

---

## Part F — verify full firmware

Keep CC’s guided Provisioning page open at **Provision and monitor the OTA**.
CC auto-detects the newest update for the selected site; confirm its update ID
exactly matches the station. The portal refreshes every five seconds and shows:

- one overall completion bar;
- queued, in-progress, succeeded, and failed counts;
- one status row per gateway;
- target firmware version and OTA update ID.

The station also offers **Check OTA status** and automatic refresh. Both views
read the same AWS IoT update. Do not continue if the IDs differ.

Common statuses:

- `QUEUED`: the AWS job exists; the gateway has not started it yet.
- `IN_PROGRESS`: the gateway is downloading/verifying/installing.
- `SUCCEEDED`: that gateway accepted and installed the signed firmware.
- `FAILED`, `REJECTED`, `CANCELED`, or `REMOVED`: stop and escalate.

Important distinctions:

- `CREATE_COMPLETE` means AWS successfully created the OTA update. It does not
  mean the gateway installed it.
- Bootstrap `done` means identity/network delivery succeeded. It does not mean
  the full firmware was installed.
- The release gate is **each Thing’s job status = `SUCCEEDED`**.
- The CC progress bar must reach 100% with zero failed gateways.

After all OTA jobs succeed:

1. In CC, open **Provisioned meters**.
2. Confirm each Thing, MAC, factory serial/label, and site.
3. Refresh/reconcile after telemetry arrives.
4. Confirm the gateway reports online and the reported firmware matches the
   target release.
5. Export the station CSV and save it with the batch record.

Do not release the batch if any selected gateway lacks an OTA update ID or does
not reach `SUCCEEDED`.

---

## Part G — installation and customer commissioning

Provisioning can happen before a customer account is known.

At installation:

1. Mount and power the gateway according to the installation SOP.
2. Confirm it can reach the operational Wi-Fi.
3. Wire/connect the meter as specified.
4. Wait for telemetry.
5. In CC, run **Reconcile from telemetry**.
6. Confirm the meter serial is acquired against the expected gateway.
7. In the customer commissioning/assign-meter flow, link that meter to the
   correct customer account.

The permanent `<SITE>-GW-####` gateway identity does not change when a customer
is assigned.

---

## Stop conditions

Stop and escalate if any of these occurs:

- CC OTA readiness is neither `candidate ready` for exactly one canary nor
  `ready` for a controlled batch;
- the approved target firmware version is blank or unexpected;
- no MAC appears, or a row shows `unknown`;
- the printed serial does not match the factory manifest;
- CC reports that the MAC or Thing is already claimed;
- identity was allocated but bootstrap delivery failed;
- the operational Wi-Fi credentials are uncertain;
- the OTA update was not created;
- an OTA job fails/rejects/cancels;
- a job remains queued/in progress beyond the agreed rollout window;
- the gateway reports an unexpected firmware version;
- the gateway’s prior provisioning state is uncertain.

Do not “try again” by creating another Thing. Do not erase or reflash. Repeated
provisioning can create duplicate identities/certificates and break the
MAC-to-Thing record.

---

## Evidence to capture for support

For every incident, send:

- country and destination site;
- operator name and local time;
- gateway printed factory serial;
- PCB MAC;
- IP address shown in the scan;
- assigned Thing name;
- target firmware version;
- OTA update ID;
- AWS IoT Job status shown by the station;
- screenshot of the complete error;
- PowerShell text from the station start through the error;
- access-point SSID (never send the password in chat);
- whether other gateways on the same LAN succeeded.

This lets Engineering distinguish discovery, bootstrap, cloud identity, network,
signing, and OTA-agent failures without opening the gateway.

---

## Troubleshooting by symptom

### “The Python script does nothing for hours”

If it shows `Open: http://localhost:8787`, it is running correctly. Open that
address in a browser and leave the terminal open.

### Browser cannot open localhost:8787

1. Confirm the PowerShell window is still open.
2. Use `http://`, not `https://`.
3. Use `localhost`, not the gateway IP.
4. Check whether another copy is already using port 8787.
5. Close duplicate copies and start one station.

### CC sign-in fails

- Confirm the laptop has internet.
- Confirm `https://cc.1pwrafrica.com` opens directly.
- Confirm the employee credentials and provisioning role.
- Do not enter customer-app credentials.

### A unit shows provisioned instead of virgin

Do not select it. Look it up by MAC/Thing in CC. It may already have a permanent
identity even if it was placed with an unprocessed batch.

### Bootstrap delivery fails after CC allocated a Thing

Do not allocate a second identity. Preserve the assigned Thing and MAC. Confirm
the gateway is still reachable on the original provisioning IP, then escalate
for a controlled retry/recovery against the same registry entry.

### OTA remains QUEUED

Check:

- the gateway rebooted onto the operational Wi-Fi;
- that Wi-Fi password was entered correctly;
- the network has internet and outbound TCP 8883;
- the Thing certificate/policy is active;
- the gateway remains powered.

### OTA is IN_PROGRESS for a long time

Keep power and internet stable. Capture the update ID and Thing. Engineering
should inspect the IoT Job execution and OTA diagnostics before any retry.

### OTA fails or is rejected

Stop. Do not create a second update blindly. Likely categories include
anti-rollback version, signing trust, artifact/version mismatch, policy, or
download interruption. Engineering needs the update ID and Thing.

---

## Batch record

Retain one row per gateway:

| Field | Required |
|---|---|
| Date/time | yes |
| Country/site | yes |
| Operator | yes |
| Factory serial | yes |
| PCB MAC | yes |
| Assigned Thing | yes |
| Bootstrap result | yes |
| Target firmware | yes |
| OTA update ID | yes |
| Per-Thing OTA result | must be `SUCCEEDED` |
| Telemetry/full-version verification | yes |
| Exception/ticket reference | if applicable |

The station’s **Export CSV** includes identity/bootstrap status and the OTA
update ID. Attach screenshots or a ticket reference for exceptions.

---

## Engineering/admin preflight (not for the field operator)

Before an operator can provision, CC deployment must explicitly select one
immutable approved release:

- `ONEMETER_OTA_APP_KEY`
- `ONEMETER_OTA_APP_VERSION_ID`
- `ONEMETER_OTA_TARGET_VERSION`
- `ONEMETER_FACTORY_BASELINE_VERSION` (currently `1.1.56`)

CC also uses:

- bucket `1pwr-ota-firmware`;
- signing profile `1PWR_OTA_ESP32_v2`;
- OTA role `arn:aws:iam::758201218523:role/1pwr-ota-service-role`.

The CC host role needs IoT OTA/Jobs read-create permissions, S3 object-version
access, Signer access, and permission to pass only the OTA service role.

The approved full firmware must:

- have an OTA app version strictly above the factory boot version;
- fit the OTA application partition;
- contain the signing certificate matching the active signing profile;
- preserve runtime identity/TLS/Wi-Fi NVS across the OTA;
- pass a single-gateway canary before batch use.

If no immutable approved artifact is configured, CC intentionally fails closed.
