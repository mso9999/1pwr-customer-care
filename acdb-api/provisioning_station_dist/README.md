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

Then open `http://localhost:8787`, select the deployment country, and sign in.
The country selection controls the database, canonical sites, currency, and OTA
catalog. The page checks CC’s approved OTA release
before it permits a batch. Scan, verify serial/MAC matches, choose the canonical
site and operational Wi-Fi, and confirm.

Do not erase, open, USB-flash, or repeat provisioning when the gateway’s state
is uncertain. Preserve the serial, MAC, assigned Thing, OTA update ID, and
screenshots, then escalate.
