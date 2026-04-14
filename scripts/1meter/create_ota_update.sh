#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${BASE_DIR:-/opt/1meter-firmware}"
RELEASES_DIR="${RELEASES_DIR:-${BASE_DIR}/releases}"
RELEASE_DIR="${RELEASE_DIR:-}"
PUBLISH_MANIFEST="${PUBLISH_MANIFEST:-}"
AWS_REGION="${AWS_REGION:-us-east-1}"
OTA_UPDATE_ID="${OTA_UPDATE_ID:-}"
DESCRIPTION="${DESCRIPTION:-}"
THING_NAMES="${THING_NAMES:-}"
THING_GROUP_NAMES="${THING_GROUP_NAMES:-}"
TARGET_ARNS="${TARGET_ARNS:-}"
ACCOUNT_ID="${ACCOUNT_ID:-}"
SIGNING_PROFILE_NAME="${SIGNING_PROFILE_NAME:-}"
OTA_ROLE_ARN="${OTA_ROLE_ARN:-}"
CERTIFICATE_PATH_ON_DEVICE="${CERTIFICATE_PATH_ON_DEVICE-/}"
SIGNED_S3_PREFIX="${SIGNED_S3_PREFIX:-signed}"
PROTOCOLS="${PROTOCOLS:-MQTT}"
TARGET_SELECTION="${TARGET_SELECTION:-SNAPSHOT}"
FILE_NAME="${FILE_NAME:-}"
FILE_TYPE="${FILE_TYPE:-0}"
ROLL_OUT_MAX_PER_MINUTE="${ROLL_OUT_MAX_PER_MINUTE:-}"
IN_PROGRESS_TIMEOUT_MINUTES="${IN_PROGRESS_TIMEOUT_MINUTES:-}"
DRY_RUN="${DRY_RUN:-0}"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

require_cmd python3

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

if [[ -z "${PUBLISH_MANIFEST}" ]]; then
  PUBLISH_MANIFEST="${RELEASE_DIR}/s3-publish-manifest.json"
fi

if [[ ! -f "${PUBLISH_MANIFEST}" ]]; then
  echo "Publish manifest missing: ${PUBLISH_MANIFEST}" >&2
  echo "Run publish_release.sh first." >&2
  exit 1
fi

if [[ -z "${SIGNING_PROFILE_NAME}" ]]; then
  echo "Set SIGNING_PROFILE_NAME to the AWS IoT OTA signing profile name." >&2
  exit 1
fi

if [[ -z "${OTA_ROLE_ARN}" ]]; then
  echo "Set OTA_ROLE_ARN to the IAM role ARN used for AWS IoT OTA updates." >&2
  exit 1
fi

if [[ -z "${THING_NAMES}${THING_GROUP_NAMES}${TARGET_ARNS}" ]]; then
  echo "Provide THING_NAMES, THING_GROUP_NAMES, or TARGET_ARNS." >&2
  exit 1
fi

if [[ -z "${ACCOUNT_ID}" ]]; then
  if [[ "${OTA_ROLE_ARN}" =~ ^arn:aws:iam::([0-9]{12}):role/ ]]; then
    ACCOUNT_ID="${BASH_REMATCH[1]}"
  fi
fi

if [[ "${DRY_RUN}" != "1" ]]; then
  require_cmd aws
  if [[ -z "${ACCOUNT_ID}" ]]; then
    ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
  else
    aws sts get-caller-identity >/dev/null
  fi
elif [[ -z "${ACCOUNT_ID}" && -z "${TARGET_ARNS}" ]]; then
  echo "Set ACCOUNT_ID for dry-run OTA planning when using THING_NAMES or THING_GROUP_NAMES." >&2
  exit 1
fi

plan_file="$(mktemp)"
trap 'rm -f "${plan_file}"' EXIT

python3 - <<'PY' \
  "${PUBLISH_MANIFEST}" \
  "${AWS_REGION}" \
  "${OTA_UPDATE_ID}" \
  "${DESCRIPTION}" \
  "${THING_NAMES}" \
  "${THING_GROUP_NAMES}" \
  "${TARGET_ARNS}" \
  "${ACCOUNT_ID}" \
  "${SIGNING_PROFILE_NAME}" \
  "${OTA_ROLE_ARN}" \
  "${CERTIFICATE_PATH_ON_DEVICE}" \
  "${SIGNED_S3_PREFIX}" \
  "${PROTOCOLS}" \
  "${TARGET_SELECTION}" \
  "${FILE_NAME}" \
  "${FILE_TYPE}" \
  "${ROLL_OUT_MAX_PER_MINUTE}" \
  "${IN_PROGRESS_TIMEOUT_MINUTES}" > "${plan_file}"
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def split_csv(value):
    return [item.strip() for item in value.split(",") if item.strip()]


publish_manifest = json.loads(Path(sys.argv[1]).read_text())
aws_region = sys.argv[2]
ota_update_id = sys.argv[3]
description = sys.argv[4]
thing_names = split_csv(sys.argv[5])
thing_group_names = split_csv(sys.argv[6])
target_arns = split_csv(sys.argv[7])
account_id = sys.argv[8]
signing_profile_name = sys.argv[9]
ota_role_arn = sys.argv[10]
certificate_path_on_device = sys.argv[11]
signed_s3_prefix = sys.argv[12].strip("/")
protocols = split_csv(sys.argv[13])
target_selection = sys.argv[14]
file_name = sys.argv[15]
file_type = int(sys.argv[16] or "0")
roll_out_max_per_minute = sys.argv[17]
in_progress_timeout_minutes = sys.argv[18]

if not file_name:
    file_name = publish_manifest["app_binary"]

targets = list(target_arns)
targets.extend(
    f"arn:aws:iot:{aws_region}:{account_id}:thing/{name}"
    for name in thing_names
)
targets.extend(
    f"arn:aws:iot:{aws_region}:{account_id}:thinggroup/{name}"
    for name in thing_group_names
)

if not targets:
    raise SystemExit("No OTA targets were resolved")

ota_app_version = publish_manifest.get("ota_app_version")
release_name = publish_manifest["release_name"]
app_s3_version = publish_manifest.get("app_s3_version")

if not app_s3_version:
    for artifact in publish_manifest.get("artifacts", []):
        if artifact.get("name") == publish_manifest["app_binary"]:
            app_s3_version = artifact.get("version_id")
            break

if not ota_update_id:
    version_part = (ota_app_version or release_name).replace(".", "-")
    version_part = re.sub(r"[^A-Za-z0-9_-]+", "-", version_part).strip("-")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    ota_update_id = f"1meter-ota-{version_part}-{timestamp}"

if not description:
    if ota_app_version:
        description = f"1Meter OTA {ota_app_version} from {release_name}"
    else:
        description = f"1Meter OTA from {release_name}"

signed_prefix = signed_s3_prefix
if ota_app_version:
    signed_prefix = f"{signed_prefix}/v{ota_app_version}/{release_name}" if signed_prefix else f"v{ota_app_version}/{release_name}"
else:
    signed_prefix = f"{signed_prefix}/{release_name}" if signed_prefix else release_name

file_entry = {
    "fileName": file_name,
    "fileType": file_type,
    "fileVersion": ota_app_version or release_name,
    "fileLocation": {
        "s3Location": {
            "bucket": publish_manifest["bucket"],
            "key": publish_manifest["app_s3_key"],
        }
    },
    "codeSigning": {
        "startSigningJobParameter": {
            "signingProfileName": signing_profile_name,
            "destination": {
                "s3Destination": {
                    "bucket": publish_manifest["bucket"],
                    "prefix": signed_prefix,
                }
            },
        }
    },
    "attributes": {
        "release_name": release_name,
        "project_name": publish_manifest.get("project_name") or "",
        "project_version": publish_manifest.get("project_version") or "",
    },
}

if app_s3_version:
    file_entry["fileLocation"]["s3Location"]["version"] = app_s3_version

if certificate_path_on_device:
    file_entry["codeSigning"]["startSigningJobParameter"]["signingProfileParameter"] = {
        "certificatePathOnDevice": certificate_path_on_device
    }

plan = {
    "release_dir": publish_manifest["release_dir"],
    "publish_manifest": str(Path(sys.argv[1])),
    "ota_update_id": ota_update_id,
    "description": description,
    "targets": targets,
    "protocols": protocols or ["MQTT"],
    "target_selection": target_selection,
    "files": [file_entry],
    "role_arn": ota_role_arn,
    "result_path": str(Path(publish_manifest["release_dir"]) / f"ota-create-{ota_update_id}.json"),
}

if roll_out_max_per_minute:
    plan["aws_job_executions_rollout_config"] = {
        "maximumPerMinute": int(roll_out_max_per_minute)
    }

if in_progress_timeout_minutes:
    plan["aws_job_timeout_config"] = {
        "inProgressTimeoutInMinutes": int(in_progress_timeout_minutes)
    }

print(json.dumps(plan, indent=2))
PY

if [[ "${DRY_RUN}" == "1" ]]; then
  echo "Dry run: OTA update plan"
  python3 -m json.tool "${plan_file}"
  exit 0
fi

response_path="$(python3 - <<'PY' "${plan_file}"
import json
import sys

plan = json.loads(open(sys.argv[1]).read())
print(plan["result_path"])
PY
)"

targets=()
while IFS= read -r target; do
  targets+=("${target}")
done < <(
  python3 - <<'PY' "${plan_file}"
import json
import sys

plan = json.loads(open(sys.argv[1]).read())
for target in plan["targets"]:
    print(target)
PY
)

protocols=()
while IFS= read -r protocol; do
  protocols+=("${protocol}")
done < <(
  python3 - <<'PY' "${plan_file}"
import json
import sys

plan = json.loads(open(sys.argv[1]).read())
for protocol in plan["protocols"]:
    print(protocol)
PY
)

files_json="$(python3 - <<'PY' "${plan_file}"
import json
import sys

plan = json.loads(open(sys.argv[1]).read())
print(json.dumps(plan["files"]))
PY
)"

ota_update_id_value="$(python3 - <<'PY' "${plan_file}"
import json
import sys

plan = json.loads(open(sys.argv[1]).read())
print(plan["ota_update_id"])
PY
)"

description_value="$(python3 - <<'PY' "${plan_file}"
import json
import sys

plan = json.loads(open(sys.argv[1]).read())
print(plan["description"])
PY
)"

target_selection_value="$(python3 - <<'PY' "${plan_file}"
import json
import sys

plan = json.loads(open(sys.argv[1]).read())
print(plan["target_selection"])
PY
)"

cmd=(
  aws iot create-ota-update
  --ota-update-id "${ota_update_id_value}"
  --description "${description_value}"
  --targets "${targets[@]}"
  --protocols "${protocols[@]}"
  --target-selection "${target_selection_value}"
  --files "${files_json}"
  --role-arn "${OTA_ROLE_ARN}"
  --region "${AWS_REGION}"
  --output json
)

rollout_config="$(python3 - <<'PY' "${plan_file}"
import json
import sys

plan = json.loads(open(sys.argv[1]).read())
config = plan.get("aws_job_executions_rollout_config")
if config:
    print(json.dumps(config))
PY
)"
if [[ -n "${rollout_config}" ]]; then
  cmd+=(--aws-job-executions-rollout-config "${rollout_config}")
fi

timeout_config="$(python3 - <<'PY' "${plan_file}"
import json
import sys

plan = json.loads(open(sys.argv[1]).read())
config = plan.get("aws_job_timeout_config")
if config:
    print(json.dumps(config))
PY
)"
if [[ -n "${timeout_config}" ]]; then
  cmd+=(--aws-job-timeout-config "${timeout_config}")
fi

"${cmd[@]}" > "${response_path}"
python3 -m json.tool "${response_path}"
