#!/bin/bash
# Deploy web-session Koios customer creation workaround to CC server.
#
# Changes sparkmeter_customer.py to use Koios web UI session auth (email/password
# + CSRF login) as the primary customer creation path, with API key as fallback.
# The web UI session has full user permissions and bypasses the API key bug where
# customers are created (201) but immediately inaccessible (404).
#
# Usage: bash deploy_koios_web_session.sh

set -euo pipefail

SERVER="${1:-ubuntu@cc.1pwrafrica.com}"
BACKEND_DIR="/opt/cc-portal/backend"
LOCAL_FILE="$(dirname "$0")/../../acdb-api/sparkmeter_customer.py"
REMOTE_FILE="$BACKEND_DIR/sparkmeter_customer.py"

echo "=== Deploying Koios web session workaround ==="
echo ""

# 1. Backup current file on server
echo "1. Backing up current sparkmeter_customer.py on server..."
ssh "$SERVER" "sudo cp $REMOTE_FILE ${REMOTE_FILE}.bak.\$(date +%Y%m%d-%H%M)"

# 2. Copy new file
echo "2. Copying updated sparkmeter_customer.py..."
scp "$LOCAL_FILE" "$SERVER:/tmp/sparkmeter_customer.py"
ssh "$SERVER" "sudo cp /tmp/sparkmeter_customer.py $REMOTE_FILE && sudo chown cc_api:cc_api $REMOTE_FILE && sudo chmod 644 $REMOTE_FILE"

# 3. Verify KOIOS_WEB_EMAIL/PASSWORD are in .env
echo "3. Checking KOIOS_WEB_EMAIL in /opt/1pdb/.env..."
ssh "$SERVER" "sudo -u cc_api grep -q KOIOS_WEB_EMAIL /opt/1pdb/.env && echo '   KOIOS_WEB_EMAIL: OK' || echo '   WARNING: KOIOS_WEB_EMAIL not set!'"
ssh "$SERVER" "sudo -u cc_api grep -q KOIOS_WEB_PASSWORD /opt/1pdb/.env && echo '   KOIOS_WEB_PASSWORD: OK' || echo '   WARNING: KOIOS_WEB_PASSWORD not set!'"

# 4. Restart API
echo "4. Restarting 1pdb-api service..."
ssh "$SERVER" "sudo systemctl restart 1pdb-api"
sleep 3
ssh "$SERVER" "sudo systemctl is-active 1pdb-api && echo '   Service is active' || echo '   ERROR: Service failed to start!'"

# 5. Check logs for web session init
echo ""
echo "5. Recent API logs (web session):"
ssh "$SERVER" "sudo journalctl -u 1pdb-api --since '1 min ago' --no-pager | grep -i 'koios web\|web session\|sm-customer' || echo '   (no matching log lines yet -- will appear on first customer sync)'"

echo ""
echo "=== Deployment complete ==="
echo ""
echo "To test: trigger a customer sync for 0273MAS or use the standalone script:"
echo "  cd /opt/cc-portal/backend"
echo "  sudo -u cc_api python3 scripts/ops/koios_web_create_customer.py 0273MAS 'Mashai Customer 0273' MAS --meter SERIAL"
