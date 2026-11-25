#!/bin/sh
# postinst script for perpetua
set -e

RULE_PATH="/etc/udev/rules.d/01-perpetua-keyboard.rules"
DUMPYKEYS_RULE_PATH="/etc/udev/rules.d/12-input.rules"
MODULES_LOAD_PATH="/etc/modules-load.d/perpetua-uinput.conf"
RULE_CONTENT='KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess", GROUP="input", MODE="0660"'
# tty access lets pynput's uinput backend read the kernel keymap (dumpkeys)
# without root. Group-readable ("input", 0660) + uaccess ACL for the active
# session; NOT world-writable. (The previous value had a malformed rule:
# missing comma and a stray MODE="0666" — both fixed here.)
DUMPYKEYS_RULE_CONTENT='SUBSYSTEM=="tty", TAG+="uaccess", GROUP="input", MODE="0660"'

# Path the user can run by hand when every escalation fails; $0 is unusable
# for that, since in an AppImage it lives in a FUSE mount that disappears
# with the process and that root cannot read anyway.
MANUAL_SCRIPT_PATH=""

# Self-elevation: an AppImage has no postinst, so AppRun and the daemon's
# permission flow both invoke this script unprivileged.
self_escalate() {
  # The AppImage squashfs is a FUSE mount private to this user: root cannot
  # read it, so the script has to copy itself out before elevating.
  staging="$(mktemp -d "${TMPDIR:-/tmp}/perpetua-udev-XXXXXX")" || return 1
  trap 'rm -rf "$staging"' EXIT INT TERM
  cp -- "$0" "$staging/enable_uinput.sh" || return 1
  chmod 0755 "$staging/enable_uinput.sh"
  chmod 0755 "$staging"

  rc=0
  # pkexec is the only prompt reachable without a terminal.
  if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v pkexec >/dev/null 2>&1; then
    pkexec /bin/sh "$staging/enable_uinput.sh" "$@" || rc=$?
    return "$rc"
  fi
  # sudo can only ask on a tty, so the GUI never reaches this branch.
  if [ -t 0 ] && command -v sudo >/dev/null 2>&1; then
    sudo /bin/sh "$staging/enable_uinput.sh" "$@" || rc=$?
    return "$rc"
  fi

  # Nothing worked: leave a copy behind that outlives this process. mktemp
  # keeps the name unpredictable, so no one can pre-create what root runs.
  MANUAL_SCRIPT_PATH="$(mktemp "${TMPDIR:-/tmp}/perpetua-enable-uinput-XXXXXX.sh" 2>/dev/null)" || MANUAL_SCRIPT_PATH=""
  if [ -n "$MANUAL_SCRIPT_PATH" ] && cp -- "$staging/enable_uinput.sh" "$MANUAL_SCRIPT_PATH"; then
    chmod 0755 "$MANUAL_SCRIPT_PATH"
  else
    MANUAL_SCRIPT_PATH="$0"
  fi
  return 1
}

load_uinput() {
  # static_node=uinput only materialises the node when udevd starts, so a
  # freshly written rule leaves udevadm trigger with nothing to relabel.
  if command -v modprobe >/dev/null 2>&1; then
    modprobe uinput 2>/dev/null || true
  fi
  mkdir -p -- "$(dirname "$MODULES_LOAD_PATH")"
  printf "uinput\n" > "$MODULES_LOAD_PATH"
  chmod 644 "$MODULES_LOAD_PATH"
}

check_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "perpetua: insufficient privileges - this operation requires root." >&2
    exit 1
  fi
}

write_rule() {
  path="$1"
  content="$2"

  # Minimal containers (used by CI smoke-tests) don't ship the udev rules
  # directory; create it lazily so the same script works on both bare
  # containers and real desktop installs.
  mkdir -p -- "$(dirname "$path")"

  if [ -f "$path" ] && [ "$(cat "$path")" = "$content" ]; then
    echo "perpetua: $path already up to date - skipping."
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

case "${1:-configure}" in
  # Debian: "configure" on install/upgrade.
  # RPM (%post): numeric - "1" on first install, "2"+ on upgrade.
  # No argument (manual ``sudo bash enable_uinput.sh``): treat as install.
  configure|1|2)
    if [ "$(id -u)" -ne 0 ]; then
      rc=0
      self_escalate "$@" || rc=$?
      if [ "$rc" -ne 0 ]; then
        echo "perpetua: could not obtain administrator rights." >&2
        echo "perpetua: run it manually with:  sudo sh ${MANUAL_SCRIPT_PATH:-$0}" >&2
      fi
      exit "$rc"
    fi

    write_rule "$RULE_PATH" "$RULE_CONTENT"
    write_rule "$DUMPYKEYS_RULE_PATH" "$DUMPYKEYS_RULE_CONTENT"

    # Detect the real user (best-effort, may not be set in unattended installs)
    REAL_USER=""
    if [ -n "${SUDO_USER:-}" ]; then
      REAL_USER="$SUDO_USER"
    elif [ -n "${PKEXEC_UID:-}" ]; then
      # pkexec clears the environment and passes the caller's uid instead.
      REAL_USER="$(getent passwd "$PKEXEC_UID" | cut -d: -f1)"
    elif command -v logname >/dev/null 2>&1; then
      REAL_USER="$(logname 2>/dev/null || true)"
    fi

    if [ -n "$REAL_USER" ] && [ "$REAL_USER" != "root" ]; then
      if id "$REAL_USER" >/dev/null 2>&1; then
        if ! groups "$REAL_USER" | grep -qw "input"; then
          usermod -aG input "$REAL_USER"
          echo "perpetua: added $REAL_USER to the input group."
          # The uaccess ACL usually grants access right away; the group is
          # only the fallback for sessions udev does not tag.
          if ! su "$REAL_USER" -c 'test -w /dev/uinput' 2>/dev/null; then
            echo "perpetua: log out and back in for the group change to take effect."
          fi
        else
          echo "perpetua: $REAL_USER is already in the input group."
        fi
      fi
    else
      echo "perpetua: could not detect the installing user - add yourself to the 'input' group manually:"
      echo "  sudo usermod -aG input \$USER"
    fi

    load_uinput
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