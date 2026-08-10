# 1Meter Provisioning Station — start here

This station is for **sealed, factory-virgin gateways that already contain the
factory boot firmware**. The field/depot procedure does not use USB.

The complete Windows procedure is:

**[`START_HERE_FACTORY_VIRGIN_GATEWAYS.md`](START_HERE_FACTORY_VIRGIN_GATEWAYS.md)**

## If the terminal appears to wait forever

This banner means the station is running correctly:

```text
================================================================
 1Meter Provisioning Station
   CC:      https://cc.1pwrafrica.com
   Subnet:  <your laptop network>
   Open:    http://localhost:8787
================================================================
```

It is a small local web server, so it intentionally waits after printing the
banner. Leave PowerShell open, then open `http://localhost:8787` in Chrome or
Edge. Do not wait for another terminal message.

## What happens

1. The factory boot firmware joins the recognized provisioning network.
2. The station discovers the gateway over the LAN and reads its hardware MAC.
3. CC allocates a permanent `<SITE>-GW-####` identity and certificate.
4. The station delivers that identity and the operational Wi-Fi locally.
5. CC creates a signed AWS IoT OTA job for the approved full firmware.
6. The operator keeps the unit powered and online until its OTA result is
   `SUCCEEDED`.

Identity delivery alone is not completion. Do not release a gateway until the
full-firmware OTA is confirmed.

The destination site's actual Starlink router must be powered, online, and
within range during the OTA stage. The gateway leaves the `1Meter` provisioning
network after bootstrap and must join Starlink before it can receive its AWS IoT
Job. The password is protected runtime device configuration, not compiled into
a site-specific firmware binary, and CC never stores it.

## Quick start

Requirements:

- Python 3.9+.
- CC provisioning access.
- Laptop and gateways on the approved provisioning LAN (currently
  `1Meter` / `1Meter00`).
- Internet access to CC and AWS IoT.
- Correct destination site and operational site Wi-Fi credentials.

From PowerShell in this extracted folder:

```powershell
py -3 provisioning_station.py --cc https://cc.1pwrafrica.com
```

If `py` is unavailable:

```powershell
python provisioning_station.py --cc https://cc.1pwrafrica.com
```

Then open the exact versioned URL printed after `Open:` in PowerShell, select
the deployment country, and sign in. The page header must show station version
`2026.08.09.1` or later; if it does not, close the old station, download again,
and extract into a new folder.
The country selection controls the database, canonical sites, currency, and OTA
catalog. The page checks CC’s approved OTA release
before it permits a batch. Scan, verify serial/MAC matches, choose the canonical
site and operational Wi-Fi, and confirm.

The printed factory serial is required for every selected gateway. Grey text in
the serial box is only a placeholder; type the serial printed on the unit (or
import the verified serial-to-MAC CSV). The action message beside the disabled
button lists the next missing input or CC gate. If CC reports access denied, a
CC administrator must assign the operator the O&M, Engineering, or Superadmin
role; then use **Change user / sign in again** so the new role is included in a
fresh session.

The destination-site control shows the country roster as large buttons and
also accepts a manually typed canonical code. For Benin, the configured codes
are currently `GBO` and `SAM`. Manual entry does not bypass validation: CC
checks that the code belongs to the selected country and has an eligible OTA
release before provisioning is enabled.

Do not erase, open, USB-flash, or repeat provisioning when the gateway’s state
is uncertain. Preserve the serial, MAC, assigned Thing, OTA update ID, and
screenshots, then escalate.
