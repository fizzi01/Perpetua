#!/usr/bin/env bash
set -euo pipefail

RULE_PATH="/etc/udev/rules.d/01-perpetua-keyboard.rules"
RULE_CONTENT='KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess", GROUP="input", MODE="0660"'

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

if command -v udevadm >/dev/null 2>&1; then
  udevadm control --reload-rules || true
  udevadm trigger --action=change || true
fi

echo "Wrote udev rule to $RULE_PATH"
