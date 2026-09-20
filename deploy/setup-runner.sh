#!/usr/bin/env bash
#
# One-time: installs a GitHub Actions self-hosted runner on the Pi5 so a push
# to main can deploy it. The runner only makes OUTBOUND connections to
# GitHub, so no router port-forward is needed.
#
# Get a registration token (valid ~1 hour) from:
#   GitHub repo -> Settings -> Actions -> Runners -> New self-hosted runner
#
#   bash deploy/setup-runner.sh <owner>/<repo> <registration-token>
#
# Needs sudo (installs a systemd service so it survives reboots).
#
set -euo pipefail

REPO="${1:?usage: setup-runner.sh <owner>/<repo> <registration-token>}"
TOKEN="${2:?usage: setup-runner.sh <owner>/<repo> <registration-token>}"
RUNNER_DIR="$HOME/actions-runner"

case "$(uname -m)" in
    aarch64|arm64) ARCH=arm64 ;;
    x86_64)        ARCH=x64 ;;
    *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

if [ -f "$RUNNER_DIR/.runner" ]; then
    echo "runner already configured in $RUNNER_DIR - remove it first to reconfigure" >&2
    exit 1
fi

mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

VERSION="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest \
    | grep -m1 '"tag_name"' | sed -E 's/.*"v([^"]+)".*/\1/')"
echo "installing actions runner v$VERSION ($ARCH)"

curl -fsSL -o runner.tar.gz \
    "https://github.com/actions/runner/releases/download/v${VERSION}/actions-runner-linux-${ARCH}-${VERSION}.tar.gz"
tar xzf runner.tar.gz
rm runner.tar.gz

./config.sh --unattended --replace \
    --url "https://github.com/${REPO}" \
    --token "$TOKEN" \
    --name pi5 \
    --labels pi5

sudo ./svc.sh install "$USER"
sudo ./svc.sh start
sudo ./svc.sh status | head -5

echo "runner installed - it should show as 'Idle' under Settings -> Actions -> Runners"
