#!/bin/sh
# macos-guest-setup.sh PUBKEY [USER] - one-time setup for an SSH-driven
# stationer build guest: enable Remote Login (sshd) and install the build
# public key for USER (default: the sudo caller). Remote Login is exempt
# from the Application Firewall automatically. Run it with sudo inside the
# guest, e.g.:
#   sudo sh macos-guest-setup.sh "ssh-ed25519 AAAA… builder"
set -eu

if [ $# -lt 1 ]; then
    echo "usage: $0 <ssh-public-key> [user]" >&2
    exit 2
fi
PUBLIC_KEY="$1"
SSH_USER="${2:-${SUDO_USER:?pass a user when not running through sudo}}"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this one-time guest setup with sudo." >&2
    exit 1
fi

systemsetup -setremotelogin on 2>/dev/null \
    || launchctl load -w /System/Library/LaunchDaemons/ssh.plist

USER_HOME="$(eval echo "~${SSH_USER}")"
if [ ! -d "$USER_HOME" ]; then
    echo "User ${SSH_USER} has no home directory." >&2
    exit 1
fi
SSH_DIR="$USER_HOME/.ssh"
mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"
KEYS="$SSH_DIR/authorized_keys"
touch "$KEYS"
if ! grep -qxF "$PUBLIC_KEY" "$KEYS" 2>/dev/null; then
    printf '%s\n' "$PUBLIC_KEY" >>"$KEYS"
fi
chmod 600 "$KEYS"
chown -R "$SSH_USER" "$SSH_DIR"

echo "macOS build guest SSH setup is complete."
