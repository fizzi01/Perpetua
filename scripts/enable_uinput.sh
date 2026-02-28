#!/bin/sh
# postinst script for perpetua
set -e

RULE_PATH="/etc/udev/rules.d/01-perpetua-keyboard.rules"
DUMPYKEYS_RULE_PATH="/etc/udev/rules.d/12-input.rules"
RULE_CONTENT='KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess", GROUP="input", MODE="0660"'
DUMPYKEYS_RULE_CONTENT='SUBSYSTEM=="tty", MODE="0666" TAG+="uaccess", GROUP="input", MODE="0660"'

check_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "perpetua: insufficient privileges — this operation requires root." >&2
    exit 1
  fi
}

write_rule() {
  path="$1"
  content="$2"

  if [ -f "$path" ] && [ "$(cat "$path")" = "$content" ]; then
    echo "perpetua: $path already up to date — skipping."
    return 0
  fi

  if [ -f "$path" ]; then
    backup="${path}.bak.$(date +%s)"
    echo "perpetua: backing up existing $path to $backup"
    cp -a -- "$path" "$backup"
  fi

  printf "%s\n" "$content" > "$path"
  chown root:root "$path"
  chmod 644 "$path"
  echo "perpetua: wrote udev rule to $path"
}

reload_udev() {
  check_root
  if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules || true
    udevadm trigger --action=change || true
  fi
}

case "$1" in
  configure)
    write_rule "$RULE_PATH" "$RULE_CONTENT"
    write_rule "$DUMPYKEYS_RULE_PATH" "$DUMPYKEYS_RULE_CONTENT"

    # Detect the real user (best-effort, may not be set in unattended installs)
    REAL_USER=""
    if [ -n "${SUDO_USER:-}" ]; then
      REAL_USER="$SUDO_USER"
    elif command -v logname >/dev/null 2>&1; then
      REAL_USER="$(logname 2>/dev/null || true)"
    fi

    if [ -n "$REAL_USER" ] && [ "$REAL_USER" != "root" ]; then
      if id "$REAL_USER" >/dev/null 2>&1; then
        if ! groups "$REAL_USER" | grep -qw "input"; then
          usermod -aG input "$REAL_USER"
          echo "perpetua: added $REAL_USER to the input group."
          echo "perpetua: please log out and back in for the group change to take effect."
        else
          echo "perpetua: $REAL_USER is already in the input group."
        fi
      fi
    else
      echo "perpetua: could not detect the installing user — add yourself to the 'input' group manually:"
      echo "  sudo usermod -aG input \$USER"
    fi

    reload_udev
    ;;

  abort-upgrade|abort-remove|abort-deconfigure)
    ;;

  *)
    echo "postinst called with unknown argument '$1'" >&2
    exit 1
    ;;
esac

exit 0