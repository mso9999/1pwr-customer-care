# 1Meter meter-string integration and batch validation

Use this optional, recommended test once for every received or assembled batch
before releasing the batch for field provisioning. Use only an authorized test
gateway and a dummy test customer. Never perform relay testing on an occupied
customer connection.

## Safety

- Meter addressing uses a USB-to-RS485 adapter connected to the meter, not the
  gateway. Gateways are provisioned only through the local provisioning service
  and signed OTA.
- Mains wiring and dummy-load wiring must be performed by a qualified person.
- Keep all exposed terminals enclosed. Use an RCD/breaker and a modest,
  known-good resistive load such as a protected test lamp.
- Connect only one factory-default meter at Modbus address 1 during addressing.
- Stop if serials, addresses, relay state, Thing name, or customer assignment do
  not match the labels and CC screen.

## Part 1 — assign and label unique meter addresses

1. Install Python 3 and `pyserial` on the Windows test laptop:

   `py -3 -m pip install pyserial`

2. Connect one powered DDS8888 meter to the USB-to-RS485 adapter: A to A, B to
   B, and GND to GND.
3. Run `py -3 set_meter_address.py --list-ports`.
4. Run `py -3 set_meter_address.py --port COM4` using the detected COM port.
5. Assign IDs 2, 3, 4, and so on. Reserve ID 1 for a factory-new meter.
6. Label the meter with its 12-digit serial and assigned Modbus ID.
7. Disconnect it and repeat for every meter.
8. Combine the addressed meters on one RS485 bus and run:

   `py -3 set_meter_address.py --port COM4 scan`

9. Pass only when every expected serial appears once, every address is unique,
   and no meter remains at address 1.

The full addressing SOP and the tested addressing tool are included in the
downloaded meter-validation kit.

## Part 2 — connect the sorted meter string to the gateway

1. Power everything off.
2. Daisy-chain RS485 A, B, and reference GND. Keep polarity consistent and
   avoid star wiring. Apply termination only at the two physical ends of a
   long bus.
3. Connect the addressed string to the gateway RS485 terminals.
4. Wire one selected meter through the protected dummy load so relay operation
   is visible and safe.
5. Power the meters, then the gateway.
6. In CC, reconcile provisioning. Pass only when the selected gateway reports
   the expected meter serial; resolve any conflict rather than overwriting it.
7. Confirm full firmware OTA succeeded before assigning the gateway/meter to the
   dummy customer.

## Part 3 — recommended end-to-end batch test

1. In CC, open **Provisioning → Batch validation**.
2. Select an explicitly authorized test gateway. It must be marked as a test
   unit server-side.
3. Start a validation session. CC creates a synthetic test ledger isolated
   from customer financial transactions.
4. Confirm the gateway, meter serial, Modbus address, full firmware version,
   site/mirrored Wi-Fi, and recent telemetry.
5. Apply the small starting test credit shown by CC.
6. Close the relay and switch on the protected dummy load.
7. Wait for at least two telemetry readings and a positive energy increase.
8. Let the physical load consume the small synthetic credit. Select **Observe
   latest reading** after each new telemetry interval. When the measured energy
   increment takes the synthetic balance to zero, CC automatically records an
   `open` command. Pass only when the gateway acknowledges the same command and
   meter serial, relay read-back is `0`, and the dummy load turns off.
9. Add the synthetic test payment. Pass only when CC records a `close`
   command, the matching gateway acknowledgement reports relay read-back `1`,
   and the dummy load restarts.
10. Leave the load running long enough to receive a post-reconnect reading.
11. Complete the session and record batch/shipment reference, gateway, meter,
   firmware version, command IDs, acknowledgement times, and result.

## Pass criteria

- Unique Modbus addresses and serial labels match the assembled string.
- Gateway discovers the expected meter and CC binds exactly that
  gateway → meter → dummy customer.
- Signed OTA reports success and telemetry reports the target firmware.
- Real load produces a positive energy delta.
- Synthetic zero balance produces an acknowledged relay open/read-back `0`.
- Synthetic payment produces an acknowledged relay close/read-back `1`.
- Post-payment telemetry resumes.

Failure of any item blocks batch release. Preserve the OTA update ID, Thing,
meter serial, command IDs, screenshots, and timestamps for engineering.
