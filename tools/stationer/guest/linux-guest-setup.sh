#!/bin/sh
# linux-guest-setup.sh PUBKEY [USER] - one-time setup for an SSH-driven
# stationer build guest: install and start an OpenSSH server, open port 22,
# and install the build public key for USER (default: the sudo caller, else
# root). Run it as root inside the guest, e.g.:
#   sudo sh linux-guest-setup.sh "ssh-ed25519 AAAA… builder"
set -eu

if [ $# -lt 1 ]; then
    echo "usage: $0 <ssh-public-key> [user]" >&2
    exit 2
fi
PUBLIC_KEY="$1"
SSH_USER="${2:-${SUDO_USER:-root}}"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this one-time guest setup as root (sudo $0 ...)." >&2
    exit 1
fi

if ! command -v sshd >/dev/null 2>&1 && [ ! -x /usr/sbin/sshd ]; then
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y openssh-server
    elif command -v yum >/dev/null 2>&1; then
        yum install -y openssh-server
    elif command -v apk >/dev/null 2>&1; then
        apk add --no-cache openssh
    elif command -v pacman >/dev/null 2>&1; then
        pacman -Sy --noconfirm openssh
    else
        echo "No supported package manager found to install OpenSSH." >&2
        exit 1
    fi
fi

if command -v systemctl >/dev/null 2>&1; then
    # ssh.service on Debian/Ubuntu, sshd.service on Fedora/RHEL/Arch/SUSE.
    systemctl enable --now sshd.service 2>/dev/null \
        || systemctl enable --now ssh.service
elif command -v rc-service >/dev/null 2>&1; then
    rc-update add sshd default
    rc-service sshd start
elif command -v service >/dev/null 2>&1; then
    service ssh start 2>/dev/null || service sshd start
else
    /usr/sbin/sshd
fi

if command -v ufw >/dev/null 2>&1; then
    ufw allow 22/tcp >/dev/null
elif command -v firewall-cmd >/dev/null 2>&1; then
    firewall-cmd --permanent --add-service=ssh >/dev/null
    firewall-cmd --reload >/dev/null
fi

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

echo "Linux build guest SSH setup is complete."
