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

# SUDO can be overridden (e.g. SUDO="sudo -S" to feed the password on stdin
# when running over a non-interactive SSH session).
SUDO="${SUDO:-sudo}"

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

# Fetch the whole response first: piping curl straight into `grep -m1` makes
# grep exit early, curl fails with "(23) Failure writing output", and
# `pipefail` then aborts the script.
RELEASE_JSON="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest)"
VERSION="$(printf '%s' "$RELEASE_JSON" | grep -m1 '"tag_name"' | sed -E 's/.*"v([^"]+)".*/\1/')"
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

$SUDO ./svc.sh install "$USER"
$SUDO ./svc.sh start
$SUDO ./svc.sh status | head -5

echo "runner installed - it should show as 'Idle' under Settings -> Actions -> Runners"
