#!/usr/bin/env bash
set -euo pipefail

source_dir="$(cd -- "${1:-$(dirname -- "${BASH_SOURCE[0]}")/../..}" && pwd)"
install_dir=/opt/ingen-agent
config_dir=/etc/ingen-agent

if [[ $EUID != 0 ]]; then
    printf '%s\n' 'Run the installer as root.' >&2
    exit 1
fi
systemd_version="$(systemctl --version | awk 'NR == 1 {print $2}')"
if [[ ! $systemd_version =~ ^[0-9]+$ ]] || (( systemd_version < 254 )); then
    printf '%s\n' 'systemd 254 or later is required.' >&2
    exit 1
fi
command -v uv >/dev/null

if ! getent group ingen-agent >/dev/null; then
    groupadd --system ingen-agent
fi
if ! id ingen-agent >/dev/null 2>&1; then
    useradd --system --gid ingen-agent --home-dir /var/lib/ingen-agent \
        --no-create-home --shell /usr/sbin/nologin ingen-agent
fi

install -d -m 0755 "$install_dir" "$install_dir/agent" \
    "$install_dir/services" "$install_dir/services/registry"
install -d -o root -g ingen-agent -m 0750 "$config_dir" "$config_dir/trust"
install -d -o ingen-agent -g ingen-agent -m 0750 /var/lib/ingen-agent
install -m 0644 "$source_dir/pyproject.toml" "$source_dir/uv.lock" \
    "$source_dir/.python-version" "$install_dir/"
install -m 0644 "$source_dir"/agent/*.py "$install_dir/agent/"
install -m 0644 "$source_dir/services/__init__.py" "$install_dir/services/"
install -m 0644 "$source_dir/services/registry/"{__init__,schemas}.py \
    "$install_dir/services/registry/"
(
    cd -- "$install_dir"
    UV_PYTHON_INSTALL_DIR="$install_dir/python" uv sync --locked --no-dev --group robot
)
install -m 0644 "$source_dir/agent/systemd/ingen-agent.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable ingen-agent.service
