#!/usr/bin/env bash
# Pull the current baileys QR string from the WA bridge host and render a
# scannable PNG to ~/Downloads/cc_qr.png. QRs rotate ~60s; re-run until scan
# completes.
#
# Env overrides:
#   CC_HOST  default: ec2-13-245-142-186.af-south-1.compute.amazonaws.com
#   CC_KEY   default: /Users/mattmso/Dropbox/AI Projects/secrets/EOver.pem
#   OUT      default: ~/Downloads/cc_qr.png

set -euo pipefail

HOST="${CC_HOST:-ec2-13-245-142-186.af-south-1.compute.amazonaws.com}"
KEY="${CC_KEY:-/Users/mattmso/Dropbox/AI Projects/secrets/EOver.pem}"
OUT="${OUT:-$HOME/Downloads/cc_qr.png}"
TMP="$(mktemp -t whatsapp-cc-qr.XXXXXX)"
trap 'rm -f "$TMP"' EXIT

scp -q -i "$KEY" "ubuntu@$HOST:/tmp/whatsapp-cc-qr.txt" "$TMP" \
  || { echo "No QR on host (bridge may be connected or not running)"; exit 1; }

python3 - "$TMP" "$OUT" <<'PY'
import sys, qrcode
raw = open(sys.argv[1]).read().strip()
if not raw:
    sys.exit("QR file empty")
qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=14, border=4)
qr.add_data(raw); qr.make(fit=True)
img = qr.make_image(fill_color="black", back_color="white")
img.save(sys.argv[2])
print(f"Wrote {sys.argv[2]} ({img.size[0]}x{img.size[1]}) from {len(raw)}-byte QR payload")
PY

open "$OUT" 2>/dev/null || true
