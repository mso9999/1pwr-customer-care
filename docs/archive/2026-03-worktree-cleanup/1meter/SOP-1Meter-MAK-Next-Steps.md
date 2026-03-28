# SOP: 1Meter MAK Field Visit

## Purpose

This SOP gives the field team the current MAK action plan based on Motlatsi's
field report plus live AWS IoT, DynamoDB, and `1PDB` checks from the morning of
`2026-03-16`.

Today's goal is to confirm the true installed serials and Thing Names, restore
stable telemetry where needed, and avoid unnecessary device churn while the MAK
connection is weak.

## Current Status

- Work at MAK is not yet complete.
- Motlatsi reports the following field changes already happened:
  - PCB at `0025MAK` was replaced, moving from `OneMeter15` hardware to
    `OneMeter16`
  - the `0026MAK` repair now involves board `23021847`
  - serial monitor confirmations after reflash were:
    - `23022684 -> OneMeter14`
    - `23022628 -> OneMeter18`
    - `23022667 -> OneMeter44`
  - `23022646` Thing Name was changed from `ExampleThing` to `OneMeter5`
  - `OneMeter13` and `OneMeter14` cert folders were recreated locally
  - cert folders were not uploaded to S3 for backup
  - a new OTA attempt was created: gateway succeeded, `23022696` queued, the
    rest in progress
  - OTA download needs stable connection and the site connection was weak
- Live cloud checks now confirm:
  - `23022696 / OneMeter16`: AWS thing, cert, and group membership exist, but
    there have still been zero DynamoDB publishes under `OneMeter16`
  - `23021847 / OneMeter14`: last cloud publish was about
    `2026-03-15 22:57 SAST`
  - `23022646 / OneMeter5`: last cloud publish was about
    `2026-03-15 22:45 SAST`
  - `23022628 / OneMeter18`, `23022673 / OneMeter13`, and `23022667 /
    OneMeter44` are alive today
  - `0026MAK` is still mapped in CC to `23022684`, so if `23021847` is now the
    real installed board, CC will need a remote remap after physical
    confirmation
- `s3://1pwr-device-certs/` is not a usable cert source of truth right now. It
  currently contains only a stale `device-map.json`.

## Immediate Priorities

1. Get the first successful cloud publish from `23022696` under `OneMeter16`.
2. Confirm the physical serial currently installed at `0026MAK`.
3. Check `23022646` and the `0026MAK` board together, since both stopped around
   `22:xx` last night.
4. Leave currently healthy boards mostly untouched unless the console or cloud
   identity contradicts what is expected.

## Certs And Spares

1. Use the local cert folders already on the field laptop.
2. Do not rely on S3 for the field bundles today.
3. Before flashing anything, confirm these bundles are present locally:
   - `onemeter13`
   - `onemeter14`
   - `onemeter18`
   - `onemeter44`
   - `onemeter16` if `23022696` is staying on `OneMeter16`
4. Protect the newly recreated `onemeter13` and `onemeter14` folders.
5. If the `onemeter16` bundle is not available locally, do not substitute
   `onemeter15`. Stop and escalate before reflashing `23022696`.
6. Bring:
   - spare PCB for `23022696`
   - spare DDS8888
   - labels or marker for enclosure labeling
   - known-good USB cable and laptop power

## Field Execution Order

### 1. `0025MAK` / `23022696` / intended Thing `OneMeter16`

Goal:

- Bring the already replaced board online under `OneMeter16`.

Steps:

1. Record the physical serial printed on the installed board.
2. Open the serial monitor and confirm the device boots as `OneMeter16`.
3. If a reflash is needed, use:

   ```bash
   python auto_flash.py --cert-folder onemeter16 --port COMXX
   ```

4. Confirm the cert bundle in use is `onemeter16`, not the old `onemeter15`
   bundle.
5. Watch for:
   - Wi-Fi association
   - MQTT connect success
   - first publish
6. Reboot or power-cycle once and wait for a fresh cloud publish before
   leaving.
7. Ignore the queued OTA until the board is stably publishing.
8. If it still does not publish, capture the exact serial-console failure and
   send it back before moving on.

### 2. `0026MAK` / intended Thing `OneMeter14`

Goal:

- Confirm whether the installed board is really `23021847` or `23022684`.

Steps:

1. Record the physical serial on the board currently installed at `0026MAK`.
2. Confirm the serial monitor shows `OneMeter14`.
3. Only reflash if the current local identity is wrong or corrupted. If needed,
   use:

   ```bash
   python auto_flash.py --cert-folder onemeter14 --port COMXX
   ```

4. Compare the physical serial with the live cloud serial:
   - cloud currently shows `23021847`
5. If the installed board is `23021847`, keep that identity, label it clearly,
   and report that back so CC can be remapped remotely.
6. If the installed board is `23022684` but cloud still only shows `23021847`,
   stop and escalate. That is a serial or identity mismatch, not a normal CC
   ingestion problem.
7. Confirm a fresh publish after reboot.

### 3. `0119MAK` / `23022646` / `OneMeter5`

Goal:

- Confirm the renamed board is alive and not part of a shared local outage.

Steps:

1. Confirm the console identity is `OneMeter5`.
2. Confirm the board has power and stable local connectivity.
3. Reboot and confirm a fresh cloud publish.
4. Because `23022646` and the `0026MAK` board stopped near the same time last
   night, treat repeated joint failure as a local comms or power issue first.

### 4. Leave the healthy boards mostly untouched

- `23022628 / OneMeter18`: Motlatsi already confirmed the console identity and
  live cloud data still looks healthy. Do not reflash unless the local console
  now disagrees.
- `23022673 / OneMeter13`: live cloud data still looks healthy. Do not replace
  hardware just because low load makes the percent deviation noisy.
- `23022667 / OneMeter44`: Motlatsi already confirmed the console identity and
  the current OTA job succeeded. Touch it only last and only if needed.
- `23022613`: non-customer node. If encountered, record the physical serial and
  leave it for later cleanup instead of creating more identity churn today.

## Verification Checklist

After each device you touch, verify all of the following:

1. Physical serial is recorded.
2. Serial monitor shows the intended Thing Name.
3. Device reports fresh telemetry after reboot.
4. The enclosure is labeled with:
   - serial
   - Thing Name
   - account or role
5. If the device does not publish, capture the exact console output before
   leaving that location.
6. Note the exact cert folder used for that device.

## Remote Follow-Up After Visit

1. Report whether `0026MAK` should be remapped in CC from `23022684` to
   `23021847`.
2. Send the exact cert folders used on site for secure backup. S3 is not
   currently a reliable cert source of truth.
3. If S3 is used for backup after the visit, populate the actual cert bundles:

   ```bash
   cd onepwr-aws-mesh/main/certs
   aws s3 sync . s3://1pwr-device-certs/certs/ --region us-east-1
   ```

4. Only after stable telemetry and confirmed identities should the next OTA
   cleanup be attempted.

## Success Criteria For Today

1. `23022696` publishes under `OneMeter16` at least once.
2. The physical serial at `0026MAK` is confirmed and matches or explains the
   cloud identity.
3. `23022646` and the `0026MAK` board are confirmed alive, or a concrete local
   failure is captured.
4. Healthy boards are not churned unnecessarily.
5. The actual cert bundles used in the field are preserved for backup.

## Operational Notes

- The best comparison meters to rely on after cleanup should still be:
  - `0005MAK`
  - `0119MAK`
  - `0045MAK` with low-load caution
- `0025MAK` and `0026MAK` should rejoin the clean validation set only after
  stable telemetry is confirmed.
- OTA is technically proven, but MAK connectivity is still weak enough that it
  should not drive today's field decisions.
