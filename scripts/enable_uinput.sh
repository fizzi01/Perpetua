#!/usr/bin/env bash
set -euo pipefail

RULE_PATH="/etc/udev/rules.d/01-perpetua-keyboard.rules"
DUMPYKEYS_RULE_PATH="/etc/udev/rules.d/12-input.rules"
RULE_CONTENT='KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess", GROUP="input", MODE="0660"'
DUMPYKEYS_RULE_CONTENT='SUBSYSTEM=="tty", MODE="0666" TAG+="uaccess", GROUP="input", MODE="0660"'

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "This script must be run as root. Use sudo or run as root." >&2
  exit 1
fi

if [ -f "$RULE_PATH" ]; then
  BACKUP="$RULE_PATH.bak.$(date +%s)"
  echo "Existing $RULE_PATH detected — backing up to $BACKUP"
  cp -a -- "$RULE_PATH" "$BACKUP"
fi

printf "%s\n" "$RULE_CONTENT" > "$RULE_PATH"
chown root:root "$RULE_PATH"
chmod 644 "$RULE_PATH"

if [ -f "$DUMPYKEYS_RULE_PATH" ]; then
  BACKUP="$DUMPYKEYS_RULE_PATH.bak.$(date +%s)"
  echo "Existing $DUMPYKEYS_RULE_PATH detected — backing up to $BACKUP"
  cp -a -- "$DUMPYKEYS_RULE_PATH" "$BACKUP"
fi

printf "%s\n" "$DUMPYKEYS_RULE_CONTENT" > "$DUMPYKEYS_RULE_PATH"
chown root:root "$DUMPYKEYS_RULE_PATH"
chmod 644 "$DUMPYKEYS_RULE_PATH"

# Add user to input group if not already a member
if ! groups "$SUDO_USER" | grep -qw "input"; then
  usermod -aG input "$SUDO_USER"
  echo "Added $SUDO_USER to input group. Please log out and back in for changes to take effect."
else
  echo "$SUDO_USER is already a member of the input group."
fi

if command -v udevadm >/dev/null 2>&1; then
  udevadm control --reload-rules || true
  udevadm trigger --action=change || true
fi

echo "Wrote udev rule to $RULE_PATH"
echo "Wrote udev rule to $DUMPYKEYS_RULE_PATH"