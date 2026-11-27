#!/usr/bin/env bash
# Fase 0: probe portali Wayland. Lancialo DENTRO la sessione grafica della VM
# (terminale GNOME), non via ssh: serve il D-Bus di sessione.
#   bash run_probes.sh            # setup + tutti i passi
#   bash run_probes.sh setup      # solo dipendenze
#   bash run_probes.sh 3          # solo il passo 3
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/results/$(date +%Y%m%d-%H%M%S)-$(. /etc/os-release; echo "${VERSION_ID}")"
VENV="$HOME/.cache/perpetua-probe/venv"   # fuori dalla cartella condivisa
PY="$VENV/bin/python"

setup() {
  sudo apt-get update
  sudo apt-get install -y build-essential python3-venv python3-dev git pkg-config libei-dev liboeffis-dev || \
    sudo apt-get install -y build-essential python3-venv python3-dev git pkg-config libei1 liboeffis1
  # Python 3.12 like CI: pyinputcapture's PyO3 0.23 stops at 3.13 and the
  # system python may be newer. uv fetches a standalone 3.12.
  local boot="$HOME/.cache/perpetua-probe/uv-boot"
  [[ -x "$boot/bin/uv" ]] || { python3 -m venv "$boot" && "$boot/bin/pip" install -q uv; }
  rm -rf "$VENV"
  "$boot/bin/uv" venv --python 3.12 "$VENV"
  "$boot/bin/uv" pip install --python "$PY" dbus-fast "pyinputcapture==0.3.0" \
    "snegg @ git+https://gitlab.freedesktop.org/whot/snegg"
  "$PY" -c "import snegg.ei, pyinputcapture, dbus_fast; print('deps ok')"
  build_pw
}

build_pw() {  # helper C per i metadati del cursore dello ScreenCast
  dpkg -s libpipewire-0.3-dev >/dev/null 2>&1 || sudo apt-get install -y libpipewire-0.3-dev
  local bin="$HOME/.cache/perpetua-probe/probe_pw_cursor"
  cc -O2 -Wall -o "$bin" "$HERE/probe_pw_cursor.c" $(pkg-config --cflags --libs libpipewire-0.3)
  echo "helper -> $bin"
}

ext_install() {  # estensione GNOME del probe: poi logout/login, poi ext-enable
  local uuid="perpetua-pointer-probe@federicoizzi.com"
  local dst="$HOME/.local/share/gnome-shell/extensions/$uuid"
  mkdir -p "$dst" && cp "$HERE/probe_shell_ext/"* "$dst/"
  gsettings set org.gnome.shell disable-user-extensions false
  echo "installata in $dst"
  echo ">>> Ora fai logout e login (su Wayland GNOME non carica estensioni nuove senza), poi: bash run_probes.sh ext-enable"
}

ext_enable() {
  local uuid="perpetua-pointer-probe@federicoizzi.com"
  gnome-extensions enable "$uuid"
  sleep 1
  gnome-extensions info "$uuid" | grep -iE "state|stato" || true
  gdbus call --session --dest org.perpetua.ProbePointer --object-path /org/perpetua/ProbePointer \
    --method org.perpetua.ProbePointer.GetPointer && echo "estensione OK"
}

env_report() {
  mkdir -p "$OUT"
  {
    echo "== os";        cat /etc/os-release
    echo "== session";   echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-} XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP:-}"
    echo "== gnome";     gnome-shell --version 2>/dev/null || true
    echo "== x-d-p";     dpkg-query -W xdg-desktop-portal xdg-desktop-portal-gnome libei1 2>/dev/null || true
    echo "== monitors";  gnome-randr query 2>/dev/null || xrandr --listmonitors 2>/dev/null || true
    echo "== hw cursor env"; systemctl --user show-environment | grep -i mutter || echo "none"
  } > "$OUT/env.txt" 2>&1
  echo "ambiente -> $OUT/env.txt"
}

step() {  # step <n> <descrizione> <script> [args...]
  local n="$1" desc="$2"; shift 2
  echo; echo "=== PASSO $n: $desc"
  read -r -p "    Invio per partire (s = salta) " ans
  [[ "$ans" == "s" ]] && return 0
  "$PY" -u "$HERE/$@" --json "$OUT/step$n.json" 2>&1 | tee "$OUT/step$n.log" || true
}

steps() {
  local only="${1:-}"
  env_report
  run() { [[ -z "$only" || "$only" == "$1" ]] && step "$@"; return 0; }
  run 1 "RD primo avvio: il dialog DEVE comparire, accettalo"            probe_rd_persistence.py
  run 2 "RD secondo avvio: NON deve comparire nessun dialog"             probe_rd_persistence.py
  run 3 "RD con app_id registrato (dialog atteso: nuovo app_id)"         probe_rd_persistence.py --app-id com.federicoizzi.Perpetua
  run 4 "RD con app_id, di nuovo: deve essere silenzioso"                probe_rd_persistence.py --app-id com.federicoizzi.Perpetua
  run 5 "Falsificatore: senza token il dialog DEVE ricomparire"         probe_rd_persistence.py --forget --app-id com.federicoizzi.Perpetua
  run 6 "Posizionamento assoluto/relativo e sprite (rispondi alle domande)" probe_rd_persistence.py --move --app-id com.federicoizzi.Perpetua
  run 7 "Falsificatore IC: spingi il mouse FISICO contro il bordo destro" probe_ic_emulated.py --physical
  run 8 "IC con moto emulato verso destra"                               probe_ic_emulated.py
  run 9 "Verità del moto assoluto via barriere (nessuna domanda)"      probe_abs_truth.py
  run 10 "Falsificatore: senza moto assoluto gli errori DEVONO crescere" probe_abs_truth.py --skip-abs
  run 11 "Posizione dai metadati ScreenCast (dialog: scegli lo schermo; alla fine muovi il mouse fisico)" probe_pointer_truth.py --source screencast
  run 12 "Posizione dall'estensione GNOME (serve ext-install + logout + ext-enable)" probe_pointer_truth.py --source shell
  run 13 "Multi-monitor + scaling: nel dialog seleziona TUTTI i monitor" probe_pointer_multi.py
  run 14 "Riferimento CPU: stesso movimento, solo RemoteDesktop (nessuno stream)" probe_pointer_multi.py --baseline
  run 15 "ScreenCast con video limitato a 1 fps: il cursore arriva ancora?" probe_pointer_multi.py --max-fps 1
  echo; echo "Fatto. Risultati in: $OUT"
}

case "${1:-all}" in
  setup) setup ;;
  build-pw)   build_pw ;;
  ext-install) ext_install ;;
  ext-enable)  ext_enable ;;
  all)   "$PY" -c "import snegg.ei, pyinputcapture, dbus_fast" 2>/dev/null || setup; steps ;;
  *)     steps "$1" ;;
esac
