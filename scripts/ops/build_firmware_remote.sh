#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${BASE_DIR:-/opt/1meter-firmware}"
REPO_DIR="${REPO_DIR:-${BASE_DIR}/onepwr-aws-mesh}"
RELEASES_DIR="${RELEASES_DIR:-${BASE_DIR}/releases}"
PATCH_FILE="${PATCH_FILE:-${BASE_DIR}/patches/onepwr-aws-mesh-timeout.patch}"
ESP_IDF_DIR="${ESP_IDF_DIR:-${BASE_DIR}/esp-idf}"
IDF_TOOLS_PATH="${IDF_TOOLS_PATH:-${BASE_DIR}/.espressif}"
BUILD_LABEL="${BUILD_LABEL:-$(date -u +%Y%m%d%H%M%S)}"
SYNC_MAIN="${SYNC_MAIN:-0}"
SKIP_SUBMODULES="${SKIP_SUBMODULES:-0}"
ALLOW_DIRTY="${ALLOW_DIRTY:-0}"
OTA_APP_VERSION="${OTA_APP_VERSION:-}"
SITE_CONFIG="${SITE_CONFIG:-}"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

require_cmd git
require_cmd python3

if ! command -v idf.py >/dev/null 2>&1; then
  if [[ -f "${ESP_IDF_DIR}/export.sh" ]]; then
    export IDF_PATH="${ESP_IDF_DIR}"
    export IDF_TOOLS_PATH
    # shellcheck source=/dev/null
    set +u
    . "${ESP_IDF_DIR}/export.sh" >/dev/null
    set -u
  fi
fi

require_cmd idf.py

if [[ ! -d "${REPO_DIR}/.git" ]]; then
  echo "Firmware repo not found at ${REPO_DIR}" >&2
  exit 1
fi

cd "${REPO_DIR}"

if [[ -n "$(git status --porcelain)" ]]; then
  if [[ "${ALLOW_DIRTY}" == "1" ]]; then
    echo "Repo has local changes; continuing because ALLOW_DIRTY=1"
    git status --short
  else
    echo "Repo has local changes; refusing to build from a dirty worktree." >&2
    echo "Re-run with ALLOW_DIRTY=1 only if the dirty state is intentional." >&2
    git status --short
    exit 1
  fi
fi

if [[ "${SYNC_MAIN}" == "1" ]]; then
  git checkout main
  git pull --ff-only origin main
fi

if [[ -f "${PATCH_FILE}" ]]; then
  if git apply --check "${PATCH_FILE}" >/dev/null 2>&1; then
    git apply "${PATCH_FILE}"
    echo "Applied timeout patch from ${PATCH_FILE}"
  else
    echo "Patch ${PATCH_FILE} already applied or not applicable; continuing."
  fi
fi

if [[ -n "${OTA_APP_VERSION}" ]]; then
  python3 - <<'PY' "sdkconfig.defaults" "${OTA_APP_VERSION}"
import re
import sys
from pathlib import Path

sdkconfig_path = Path(sys.argv[1])
ota_version = sys.argv[2].strip()
match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", ota_version)
if not match:
    raise SystemExit("OTA_APP_VERSION must have format MAJOR.MINOR.BUILD")

major, minor, build = match.groups()
desired = {
    "CONFIG_GRI_OTA_DEMO_APP_VERSION_MAJOR": major,
    "CONFIG_GRI_OTA_DEMO_APP_VERSION_MINOR": minor,
    "CONFIG_GRI_OTA_DEMO_APP_VERSION_BUILD": build,
}

lines = sdkconfig_path.read_text().splitlines()
seen = set()
updated_lines = []

for line in lines:
    replaced = False
    for key, value in desired.items():
        prefix = f"{key}="
        if line.startswith(prefix):
            updated_lines.append(f"{key}={value}")
            seen.add(key)
            replaced = True
            break
    if not replaced:
        updated_lines.append(line)

for key, value in desired.items():
    if key not in seen:
        updated_lines.append(f"{key}={value}")

sdkconfig_path.write_text("\n".join(updated_lines) + "\n")
PY
  echo "Set OTA app version to ${OTA_APP_VERSION}"
fi

if [[ -n "${SITE_CONFIG}" ]]; then
  if [[ ! -f "${SITE_CONFIG}" ]]; then
    echo "SITE_CONFIG file not found: ${SITE_CONFIG}" >&2
    exit 1
  fi
  echo "Applying site config from ${SITE_CONFIG}:"
  python3 - <<'PY' "sdkconfig.defaults" "${SITE_CONFIG}"
import sys
from pathlib import Path

defaults_path = Path(sys.argv[1])
site_path = Path(sys.argv[2])

overrides = {}
for line in site_path.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    if "=" in line:
        key, value = line.split("=", 1)
        overrides[key.strip()] = value.strip()

lines = defaults_path.read_text().splitlines()
seen = set()
updated = []
for line in lines:
    replaced = False
    for key, value in overrides.items():
        if line.startswith(f"{key}="):
            updated.append(f"{key}={value}")
            seen.add(key)
            replaced = True
            break
    if not replaced:
        updated.append(line)

for key, value in overrides.items():
    if key not in seen:
        updated.append(f"{key}={value}")

defaults_path.write_text("\n".join(updated) + "\n")
for key, value in overrides.items():
    print(f"  {key}={value}")
PY
  echo "Site config applied."
fi

if [[ "${SKIP_SUBMODULES}" != "1" ]]; then
  git submodule update --init --recursive
fi

idf.py set-target esp32c3
idf.py update-dependencies
idf.py build

mkdir -p "${RELEASES_DIR}"

GIT_SHA="$(git rev-parse --short HEAD)"
RELEASE_DIR="${RELEASES_DIR}/${BUILD_LABEL}-${GIT_SHA}"
mkdir -p "${RELEASE_DIR}"

cp sdkconfig "${RELEASE_DIR}/" 2>/dev/null || true
cp build/*.bin "${RELEASE_DIR}/" 2>/dev/null || true
cp build/bootloader/*.bin "${RELEASE_DIR}/" 2>/dev/null || true
cp build/partition_table/*.bin "${RELEASE_DIR}/" 2>/dev/null || true
cp build/flasher_args.json "${RELEASE_DIR}/" 2>/dev/null || true
cp build/project_description.json "${RELEASE_DIR}/" 2>/dev/null || true

python3 - <<'PY' "${RELEASE_DIR}" "${BUILD_LABEL}" "${GIT_SHA}"
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

release_dir = Path(sys.argv[1])
build_label = sys.argv[2]
git_sha = sys.argv[3]

project_description = {}
project_description_path = release_dir / "project_description.json"
if project_description_path.exists():
    project_description = json.loads(project_description_path.read_text())

ota_version_bits = {}
router_ssid = None
router_password_set = False
sdkconfig_path = release_dir / "sdkconfig"
if sdkconfig_path.exists():
    for raw_line in sdkconfig_path.read_text().splitlines():
        line = raw_line.strip()
        if not line.startswith("CONFIG_GRI_OTA_DEMO_APP_VERSION_"):
            continue
        key, value = line.split("=", 1)
        ota_version_bits[key] = value
    for raw_line in sdkconfig_path.read_text().splitlines():
        line = raw_line.strip()
        if line.startswith('CONFIG_ROUTER_SSID='):
            router_ssid = line.split('=', 1)[1].strip('"')
        elif line.startswith('CONFIG_ROUTER_PASSWORD='):
            router_password_set = bool(line.split('=', 1)[1].strip('"'))

ota_app_version = None
major = ota_version_bits.get("CONFIG_GRI_OTA_DEMO_APP_VERSION_MAJOR")
minor = ota_version_bits.get("CONFIG_GRI_OTA_DEMO_APP_VERSION_MINOR")
build = ota_version_bits.get("CONFIG_GRI_OTA_DEMO_APP_VERSION_BUILD")
if major is not None and minor is not None and build is not None:
    ota_app_version = f"{major}.{minor}.{build}"

app_binary = None
app_candidates = [
    path for path in release_dir.glob("*.bin")
    if path.name not in {"bootloader.bin", "partition-table.bin", "ota_data_initial.bin"}
]
if app_candidates:
    app_binary = max(app_candidates, key=lambda path: path.stat().st_size).name

manifest = {
    "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "build_label": build_label,
    "git_sha": git_sha,
    "project_name": project_description.get("project_name"),
    "project_version": project_description.get("project_version"),
    "target": project_description.get("target"),
    "ota_app_version": ota_app_version,
    "router_ssid": router_ssid,
    "router_password_set": router_password_set,
    "app_binary": app_binary,
}

manifest_path = release_dir / "release-manifest.json"
manifest_path.write_text(json.dumps(manifest, indent=2))
manifest["files"] = sorted(p.name for p in release_dir.iterdir() if p.is_file())
manifest_path.write_text(json.dumps(manifest, indent=2))
print(str(release_dir))
PY

# --- WiFi SSID guard (post-build) -------------------------------------------
# Field-deployed devices apply CONFIG_ROUTER_SSID/PASSWORD unconditionally on
# every boot via app_wifi_init() - there is no NVS override path. Pushing a
# build with the wrong SSID via OTA bricks the fleet. This guard refuses to
# leave a release on disk if the embedded SSID is the build-host default
# unless ops explicitly opt-in with ALLOW_DEFAULT_WIFI=1.
EFFECTIVE_SSID="$(grep -E '^CONFIG_ROUTER_SSID=' "${RELEASE_DIR}/sdkconfig" | cut -d= -f2- | tr -d '"')"
echo "Embedded CONFIG_ROUTER_SSID: ${EFFECTIVE_SSID}"
if [[ "${EFFECTIVE_SSID}" == "DareMightyThings" && "${ALLOW_DEFAULT_WIFI:-0}" != "1" ]]; then
  echo "ERROR: build embeds the build-host default SSID 'DareMightyThings'." >&2
  echo "       Field deployments would brick on Wi-Fi after reboot." >&2
  echo "       Pass SITE_CONFIG=/opt/1meter-firmware/site-configs/<SITE>.conf to override," >&2
  echo "       or set ALLOW_DEFAULT_WIFI=1 if this is a dev / lab build." >&2
  rm -rf "${RELEASE_DIR}"
  exit 1
fi
