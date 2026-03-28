#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${BASE_DIR:-/opt/1meter-firmware}"
RELEASES_DIR="${RELEASES_DIR:-${BASE_DIR}/releases}"
RELEASE_DIR="${RELEASE_DIR:-}"
AWS_REGION="${AWS_REGION:-us-east-1}"
S3_BUCKET="${S3_BUCKET:-}"
S3_PREFIX="${S3_PREFIX:-firmware-releases}"
DRY_RUN="${DRY_RUN:-0}"
INCLUDE_SDKCONFIG="${INCLUDE_SDKCONFIG:-0}"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

require_cmd python3

if [[ -z "${S3_BUCKET}" ]]; then
  echo "Set S3_BUCKET to the destination bucket name." >&2
  exit 1
fi

if [[ -z "${RELEASE_DIR}" ]]; then
  RELEASE_DIR="$(python3 - <<'PY' "${RELEASES_DIR}"
import sys
from pathlib import Path

releases_dir = Path(sys.argv[1])
if not releases_dir.exists():
    raise SystemExit(f"Releases directory not found: {releases_dir}")

candidates = [path for path in releases_dir.iterdir() if path.is_dir()]
if not candidates:
    raise SystemExit(f"No release directories found under {releases_dir}")

latest = max(candidates, key=lambda path: path.stat().st_mtime)
print(str(latest))
PY
)"
fi

if [[ ! -d "${RELEASE_DIR}" ]]; then
  echo "Release directory not found: ${RELEASE_DIR}" >&2
  exit 1
fi

if [[ ! -f "${RELEASE_DIR}/release-manifest.json" ]]; then
  echo "Release manifest missing in ${RELEASE_DIR}" >&2
  exit 1
fi

plan_file="$(mktemp)"
trap 'rm -f "${plan_file}"' EXIT

python3 - <<'PY' "${RELEASE_DIR}" "${S3_BUCKET}" "${S3_PREFIX}" "${INCLUDE_SDKCONFIG}" > "${plan_file}"
import json
import sys
from pathlib import Path

release_dir = Path(sys.argv[1])
s3_bucket = sys.argv[2]
s3_prefix = sys.argv[3].strip("/")
include_sdkconfig = sys.argv[4] == "1"

manifest = json.loads((release_dir / "release-manifest.json").read_text())
release_name = release_dir.name
ota_app_version = manifest.get("ota_app_version")
project_name = manifest.get("project_name")
project_version = manifest.get("project_version")
app_binary = manifest.get("app_binary")

project_description_path = release_dir / "project_description.json"
if project_description_path.exists():
    project_description = json.loads(project_description_path.read_text())
    project_name = project_name or project_description.get("project_name")
    project_version = project_version or project_description.get("project_version")

if not ota_app_version:
    sdkconfig_path = release_dir / "sdkconfig"
    ota_bits = {}
    if sdkconfig_path.exists():
        for raw_line in sdkconfig_path.read_text().splitlines():
            line = raw_line.strip()
            if not line.startswith("CONFIG_GRI_OTA_DEMO_APP_VERSION_"):
                continue
            key, value = line.split("=", 1)
            ota_bits[key] = value
    major = ota_bits.get("CONFIG_GRI_OTA_DEMO_APP_VERSION_MAJOR")
    minor = ota_bits.get("CONFIG_GRI_OTA_DEMO_APP_VERSION_MINOR")
    build = ota_bits.get("CONFIG_GRI_OTA_DEMO_APP_VERSION_BUILD")
    if major is not None and minor is not None and build is not None:
        ota_app_version = f"{major}.{minor}.{build}"

if not app_binary:
    candidates = [
        path for path in release_dir.glob("*.bin")
        if path.name not in {"bootloader.bin", "partition-table.bin", "ota_data_initial.bin"}
    ]
    if candidates:
        app_binary = max(candidates, key=lambda path: path.stat().st_size).name

if not app_binary:
    raise SystemExit("Unable to determine application binary for release publish")

if ota_app_version:
    key_prefix = f"{s3_prefix}/v{ota_app_version}/{release_name}" if s3_prefix else f"v{ota_app_version}/{release_name}"
else:
    key_prefix = f"{s3_prefix}/{release_name}" if s3_prefix else release_name

safe_artifact_names = [
    app_binary,
    "bootloader.bin",
    "partition-table.bin",
    "ota_data_initial.bin",
    "flasher_args.json",
    "project_description.json",
    "release-manifest.json",
]

if include_sdkconfig:
    safe_artifact_names.append("sdkconfig")

artifacts = []
for name in safe_artifact_names:
    local_path = release_dir / name
    if not local_path.exists():
        continue
    artifacts.append(
        {
            "name": name,
            "local_path": str(local_path),
            "s3_key": f"{key_prefix}/{name}",
            "size_bytes": local_path.stat().st_size,
        }
    )

if not artifacts:
    raise SystemExit("No publishable artifacts found")

publish_manifest_key = f"{key_prefix}/s3-publish-manifest.json"

plan = {
    "release_dir": str(release_dir),
    "release_name": release_name,
    "bucket": s3_bucket,
    "key_prefix": key_prefix,
    "publish_manifest_key": publish_manifest_key,
    "project_name": project_name,
    "project_version": project_version,
    "ota_app_version": ota_app_version,
    "app_binary": app_binary,
    "app_s3_key": f"{key_prefix}/{app_binary}",
    "artifacts": artifacts,
}

print(json.dumps(plan, indent=2))
PY

if [[ "${DRY_RUN}" == "1" ]]; then
  publish_manifest_path="${RELEASE_DIR}/s3-publish-manifest.json"
  python3 - <<'PY' "${plan_file}" "${publish_manifest_path}" "${AWS_REGION}"
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

plan = json.loads(Path(sys.argv[1]).read_text())
publish_manifest_path = Path(sys.argv[2])
aws_region = sys.argv[3]

publish_manifest = {
    "dry_run": True,
    "published_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "aws_region": aws_region,
    "bucket": plan["bucket"],
    "key_prefix": plan["key_prefix"],
    "publish_manifest_key": plan["publish_manifest_key"],
    "release_dir": plan["release_dir"],
    "release_name": plan["release_name"],
    "project_name": plan.get("project_name"),
    "project_version": plan.get("project_version"),
    "ota_app_version": plan.get("ota_app_version"),
    "app_binary": plan["app_binary"],
    "app_s3_key": plan["app_s3_key"],
    "artifacts": [
        {
            "name": artifact["name"],
            "s3_key": artifact["s3_key"],
            "size_bytes": artifact["size_bytes"],
        }
        for artifact in plan["artifacts"]
    ],
}

publish_manifest_path.write_text(json.dumps(publish_manifest, indent=2))
PY
  echo "Dry run: publish plan"
  python3 -m json.tool "${plan_file}"
  exit 0
fi

require_cmd aws
aws sts get-caller-identity >/dev/null

while IFS=$'\t' read -r local_path s3_key; do
  aws s3 cp "${local_path}" "s3://${S3_BUCKET}/${s3_key}" \
    --region "${AWS_REGION}" \
    --only-show-errors
done < <(
  python3 - <<'PY' "${plan_file}"
import json
import sys

plan = json.loads(open(sys.argv[1]).read())
for artifact in plan["artifacts"]:
    print(f'{artifact["local_path"]}\t{artifact["s3_key"]}')
PY
)

publish_manifest_path="${RELEASE_DIR}/s3-publish-manifest.json"

python3 - <<'PY' "${plan_file}" "${publish_manifest_path}" "${AWS_REGION}"
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

plan = json.loads(Path(sys.argv[1]).read_text())
publish_manifest_path = Path(sys.argv[2])
aws_region = sys.argv[3]

artifacts = []
app_s3_version = None

for artifact in plan["artifacts"]:
    head = json.loads(
        subprocess.check_output(
            [
                "aws",
                "s3api",
                "head-object",
                "--bucket",
                plan["bucket"],
                "--key",
                artifact["s3_key"],
                "--region",
                aws_region,
                "--output",
                "json",
            ],
            text=True,
        )
    )
    version_id = head.get("VersionId")
    artifacts.append(
        {
            "name": artifact["name"],
            "s3_key": artifact["s3_key"],
            "size_bytes": artifact["size_bytes"],
            "version_id": version_id,
        }
    )
    if artifact["name"] == plan["app_binary"]:
        app_s3_version = version_id

publish_manifest = {
    "published_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "aws_region": aws_region,
    "bucket": plan["bucket"],
    "key_prefix": plan["key_prefix"],
    "publish_manifest_key": plan["publish_manifest_key"],
    "release_dir": plan["release_dir"],
    "release_name": plan["release_name"],
    "project_name": plan.get("project_name"),
    "project_version": plan.get("project_version"),
    "ota_app_version": plan.get("ota_app_version"),
    "app_binary": plan["app_binary"],
    "app_s3_key": plan["app_s3_key"],
    "app_s3_version": app_s3_version,
    "artifacts": artifacts,
}

publish_manifest_path.write_text(json.dumps(publish_manifest, indent=2))
print(str(publish_manifest_path))
PY

aws s3 cp "${publish_manifest_path}" "s3://${S3_BUCKET}/$(python3 - <<'PY' "${plan_file}"
import json
import sys

plan = json.loads(open(sys.argv[1]).read())
print(plan["publish_manifest_key"])
PY
)" \
  --region "${AWS_REGION}" \
  --only-show-errors

python3 - <<'PY' "${publish_manifest_path}"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
print(json.dumps(payload, indent=2))
PY
