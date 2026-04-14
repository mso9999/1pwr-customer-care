#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${BASE_DIR:-/opt/1meter-firmware}"
ESP_IDF_DIR="${ESP_IDF_DIR:-${BASE_DIR}/esp-idf}"
ESP_IDF_VERSION="${ESP_IDF_VERSION:-v5.2.3}"
REPO_DIR="${REPO_DIR:-${BASE_DIR}/onepwr-aws-mesh}"
IDF_TOOLS_PATH="${IDF_TOOLS_PATH:-${BASE_DIR}/.espressif}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this script as root (for example: sudo bash ...)." >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  git wget flex bison gperf cmake ninja-build ccache libffi-dev libssl-dev \
  dfu-util libusb-1.0-0 python3 python3-pip python3-venv python3-setuptools \
  python3-wheel pkg-config ca-certificates unzip

mkdir -p "${BASE_DIR}"
mkdir -p "${IDF_TOOLS_PATH}"
export IDF_TOOLS_PATH

if [[ ! -d "${ESP_IDF_DIR}/.git" ]]; then
  git clone --branch "${ESP_IDF_VERSION}" --depth 1 https://github.com/espressif/esp-idf.git "${ESP_IDF_DIR}"
fi

bash "${ESP_IDF_DIR}/install.sh" esp32c3

if ! command -v aws >/dev/null 2>&1; then
  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "${tmp_dir}"' EXIT
  wget -q -O "${tmp_dir}/awscliv2.zip" "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip"
  unzip -q "${tmp_dir}/awscliv2.zip" -d "${tmp_dir}"
  "${tmp_dir}/aws/install" --update
fi

if [[ -d "${REPO_DIR}/.git" ]]; then
  git -C "${REPO_DIR}" submodule update --init --recursive
fi

cat > "${BASE_DIR}/env.sh" <<EOF
export ESP_IDF_DIR="${ESP_IDF_DIR}"
export IDF_PATH="\${ESP_IDF_DIR}"
export IDF_TOOLS_PATH="${IDF_TOOLS_PATH}"
if [ -f "\${ESP_IDF_DIR}/export.sh" ]; then
  . "\${ESP_IDF_DIR}/export.sh"
fi
EOF

chmod 644 "${BASE_DIR}/env.sh"

echo "Build host bootstrap complete."
echo "To load ESP-IDF in a shell: source ${BASE_DIR}/env.sh"
echo "If AWS access is needed, configure credentials with aws configure or aws login."
