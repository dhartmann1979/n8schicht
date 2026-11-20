#!/usr/bin/env bash
# =============================================================================
#  N8 ZelloBridge – geführte Installation auf dem Asus Tinker Board (DietPi)
#
#  Installiert das Übersichts-Dashboard für asl-zello-bridge im Design von
#  DMR Die N8schicht. Python-Standardbibliothek, SQLite, ein systemd-Dienst –
#  keine weiteren Pakete. Die Bridge selbst wird nicht verändert.
#
#    git clone https://github.com/dhartmann1979/n8schicht
#    cd n8schicht/zellobridge
#    sudo ./scripts/install.sh
#
#  Ein zweiter Aufruf aktualisiert; Daten und Benutzer bleiben erhalten.
#  Umgebungsvariablen (optional): N8ZELLO_DIR=/opt/n8zello, N8ZELLO_PORT=80, N8ZELLO_YES=1
# =============================================================================
set -euo pipefail

DIR="${N8ZELLO_DIR:-/opt/n8zello}"
DATA=/var/lib/n8zello
SVC_USER=n8zello
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG=/var/log/n8zello-install.log
YES="${N8ZELLO_YES:-0}"
STEPS=7

C='\033[0;36m'; G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; B='\033[1m'; D='\033[2m'; N='\033[0m'
step()  { echo; echo -e "${C}━━ Schritt $1/${STEPS}: $2${N}"; }
ok()    { echo -e "   ${G}✓${N} $*"; }
warn()  { echo -e "   ${Y}!${N} $*"; }
fail()  { echo -e "   ${R}✗ $*${N}"; exit 1; }
info()  { echo -e "   ${D}$*${N}"; }
interactive() { [ "$YES" != 1 ] && [ -t 0 ]; }
ask() {
    local __v="$1" q="$2" def="${3:-}" ans=""
    if ! interactive; then printf -v "$__v" '%s' "$def"; return 0; fi
    read -r -p "   $q [${def}]: " ans || true
    printf -v "$__v" '%s' "${ans:-$def}"
}
ask_secret() { local __v="$1" ans=""; read -r -s -p "   $2: " ans || true; echo; printf -v "$__v" '%s' "$ans"; }
yesno() { local __yn=""; ask __yn "$1 (j/n)" "$2"; [[ "$__yn" =~ ^[jJyY] ]]; }

[ "$(id -u)" -eq 0 ] || fail "Bitte mit sudo bzw. als root starten: sudo $0"
exec > >(tee -a "$LOG") 2>&1

echo -e "
   ${B}🌙 N8 ZelloBridge – Dashboard für die Zello-Bridge${N}
   asl-zello-bridge · Tinker Board · Statistiken in SQLite · Design: DMR Die N8schicht
   Fragen haben eine Vorgabe in [Klammern] – Enter übernimmt sie.
"

# ---------------------------------------------------------------------------
step 1 "System prüfen"
. /etc/os-release 2>/dev/null || true
MODEL="$( { tr -d '\0' < /proc/device-tree/model; } 2>/dev/null || uname -m)"
ok "${MODEL} · ${PRETTY_NAME:-Linux}$( [ -f /boot/dietpi/.version ] && echo ' · DietPi')"
command -v systemctl >/dev/null || fail "systemd wird gebraucht."
command -v python3 >/dev/null || { info "Python 3 wird installiert …"; apt-get update -qq >/dev/null; apt-get install -y -q python3 >/dev/null; }
python3 -c 'import sys, sqlite3; sys.exit(sys.version_info < (3, 9))' || fail "Python 3.9 oder neuer mit sqlite3 wird gebraucht."
ok "Python $(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])') – das Dashboard braucht keine weiteren Pakete"

# ---------------------------------------------------------------------------
step 2 "Zello-Bridge finden"
CUR_UNIT="$(sed -n 's/^BRIDGE=//p' /etc/n8zello.conf 2>/dev/null || true)"
DEF_UNIT="${CUR_UNIT:-$(systemctl list-units --type=service --all --no-legend --plain 2>/dev/null | awk '{print $1}' | grep -i -E 'zello' | head -1 | sed 's/\.service$//')}"
ask UNIT "systemd-Dienst der Bridge" "${DEF_UNIT:-asl-zello-bridge}"
if systemctl cat "$UNIT.service" >/dev/null 2>&1; then
    ST="$(systemctl is-active "$UNIT" 2>/dev/null || true)"
    ok "${UNIT}: ${ST}$( [ "$ST" = active ] && echo " seit $(systemctl show "$UNIT" -p ActiveEnterTimestamp --value | cut -d' ' -f2-3)")"
    ENV="$(systemctl show "$UNIT" -p Environment --value 2>/dev/null || true)"
    CH="$(echo "$ENV" | grep -o 'ZELLO_CHANNEL=[^"]*' | head -1 | cut -d= -f2- || true)"
    [ -n "$CH" ] && ok "Zello-Kanal: ${CH}"
    PORTS="$(echo "$ENV" | tr ' ' '\n' | grep -E '^USRP_(RX|TX)PORT=' | tr '\n' ' ' || true)"
    [ -n "$PORTS" ] && ok "USRP: ${PORTS}"
else
    warn "Dienst ${UNIT} nicht gefunden – das Dashboard zeigt dann nur die Hardware. Später unter Admin → Settings ändern."
fi
EXTRA="$(systemctl list-units --type=service --all --no-legend --plain 2>/dev/null | awk '{print $1}' | grep -i -E 'svxlink|usrp' | sed 's/\.service$//' | grep -v -x "$UNIT" | tr '\n' ' ' || true)"
[ -n "$EXTRA" ] && ok "weitere Dienste: ${EXTRA}" || info "keine weiteren Dienste (SvxLink …) gefunden"

# ---------------------------------------------------------------------------
step 3 "Programm nach ${DIR}"
id "$SVC_USER" >/dev/null 2>&1 || useradd --system --home "$DATA" --shell /usr/sbin/nologin "$SVC_USER"
for g in systemd-journal adm; do getent group "$g" >/dev/null && usermod -aG "$g" "$SVC_USER"; done
mkdir -p "$DIR" "$DATA"
if [ "$SRC_DIR" != "$DIR" ]; then
    rm -rf "$DIR/n8zello" "$DIR/static"
    cp -a "$SRC_DIR/n8zello" "$SRC_DIR/static" "$SRC_DIR/scripts" "$SRC_DIR/systemd" "$DIR/"
fi
install -m 0755 -o root -g root "$DIR/scripts/n8zello-backup" /usr/local/sbin/n8zello-backup
chown -R "$SVC_USER:$SVC_USER" "$DATA"; chmod 750 "$DATA"
echo "BRIDGE=$UNIT" > /etc/n8zello.conf
SC="$(command -v systemctl)"
RULES=""
for u in $UNIT $EXTRA; do RULES+="$SC start $u.service, $SC stop $u.service, $SC restart $u.service, "; done
cat > /etc/sudoers.d/n8zello <<EOF
# N8 ZelloBridge: the dashboard may control the bridge services, make a backup and (superadmin) reboot the board
$SVC_USER ALL=(root) NOPASSWD: ${RULES}/usr/local/sbin/n8zello-backup, $SC reboot, $SC poweroff
EOF
chmod 440 /etc/sudoers.d/n8zello
visudo -cq -f /etc/sudoers.d/n8zello || fail "sudoers-Regel fehlerhaft"
cat > /usr/local/bin/n8zello <<EOF
#!/bin/sh
# N8 ZelloBridge command line: n8zello set-superadmin NAME | reset-password NAME | show-settings
cd "$DIR" && exec sudo -u $SVC_USER env N8ZELLO_DATA=$DATA python3 -m n8zello "\$@"
EOF
chmod 755 /usr/local/bin/n8zello
ok "Benutzer ${SVC_USER} (liest das Journal, darf nur die Bridge-Dienste steuern) · Daten in ${DATA} (SQLite)"

run_app() { (cd "$DIR" && sudo -u "$SVC_USER" env N8ZELLO_DATA="$DATA" ${N8ZELLO_PASSWORD:+N8ZELLO_PASSWORD="$N8ZELLO_PASSWORD"} python3 -m n8zello "$@"); }
CUR="$(run_app show-settings 2>/dev/null || echo '{}')"
cget() { python3 -c "import json,sys; d=json.loads(sys.argv[1] or '{}'); print(d.get(sys.argv[2], ''))" "$CUR" "$1" 2>/dev/null || true; }
run_app set bridge.service "$UNIT" >/dev/null

# ---------------------------------------------------------------------------
step 4 "Superadmin"
if [ "$(cget _superadmin)" = "True" ] && { ! interactive || ! yesno "Superadmin ist angelegt – neu setzen?" n; }; then
    ok "Superadmin vorhanden"
elif ! interactive; then
    warn "Keine Eingabe möglich – später: ${B}sudo n8zello set-superadmin NAME${N}"
else
    info "Der Superadmin darf alles: Dienste steuern, Sicherung, Admins anlegen, neu starten. Es gibt keinen Werks-Login."
    while :; do ask SA "Benutzername" "admin"; [[ "$SA" =~ ^[A-Za-z0-9._@-]{2,32}$ ]] && break; warn "2–32 Zeichen: Buchstaben, Ziffern, . _ @ -"; done
    while :; do
        ask_secret PW "Passwort für ${SA} (min. 10 Zeichen)"
        [ ${#PW} -ge 10 ] || { warn "zu kurz"; continue; }
        ask_secret PW2 "Passwort wiederholen"; [ "$PW" = "$PW2" ] && break; warn "Die Passwörter sind verschieden."
    done
    N8ZELLO_PASSWORD="$PW" run_app set-superadmin "$SA" | sed 's/^/   /'
    unset PW PW2
fi

# ---------------------------------------------------------------------------
step 5 "Dashboard einrichten"
CUR_PORT="$(sed -n 's/^Environment=N8ZELLO_PORT=//p' /etc/systemd/system/n8zello.service 2>/dev/null || true)"
DEF_PORT="${N8ZELLO_PORT:-${CUR_PORT:-80}}"
if [ -z "$CUR_PORT" ] && ss -ltnH 2>/dev/null | awk '{print $4}' | grep -q -E "[:.]${DEF_PORT}\$"; then
    warn "Port ${DEF_PORT} ist belegt ($(ss -ltnpH "sport = :${DEF_PORT}" 2>/dev/null | grep -o 'users:(("[^"]*' | cut -d'"' -f2 | head -1)) – Vorschlag 8080"
    DEF_PORT=8080
fi
ask PORT "Web-Port des Dashboards" "$DEF_PORT"
ask TITLE "Titel" "$(cget dashboard.title)"
run_app set dashboard.title "$TITLE" >/dev/null
if yesno "Dashboard im Netz ohne Login sichtbar?" "$( [ "$(cget dashboard.public)" = False ] && echo n || echo j)"; then run_app set dashboard.public true >/dev/null; else run_app set dashboard.public false >/dev/null; fi
ok "gespeichert in SQLite"

# ---------------------------------------------------------------------------
step 6 "Dienst n8zello"
sed -e "s#/opt/n8zello#${DIR}#g" -e "s#N8ZELLO_PORT=80#N8ZELLO_PORT=${PORT}#" -e "s#/usr/bin/python3#$(command -v python3)#" \
    "$DIR/systemd/n8zello.service" > /etc/systemd/system/n8zello.service
systemctl daemon-reload
systemctl enable -q n8zello
systemctl restart n8zello
sleep 3
if systemctl is-active -q n8zello; then ok "n8zello läuft und startet beim Booten"; else warn "n8zello startet nicht: journalctl -u n8zello -n 50"; fi
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then ufw allow "$PORT/tcp" >/dev/null; ok "Firewall: Port ${PORT} frei"; fi

# ---------------------------------------------------------------------------
step 7 "Sicherung der Bridge-Konfiguration"
BK="/root/zellobridge-backup-$(date +%Y%m%d-%H%M).tar.gz"
if /usr/local/sbin/n8zello-backup "$BK" >/dev/null 2>&1; then
    ok "${BK} ($(du -h "$BK" | cut -f1)) – Bridge-Dienst, Zello-Schlüssel, SvxLink"
    info "Enthält Passwörter und Schlüssel: sicher aufbewahren, nicht öffentlich hochladen."
else
    warn "Keine Sicherung erstellt (nichts gefunden)"
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
SFX=""; [ "$PORT" != "80" ] && SFX=":$PORT"
echo -e "
${G}━━ ✅ Fertig!${N}
   Dashboard:  ${B}http://${IP}${SFX}${N}
   Admin:      Login oben rechts
   Befehle:    sudo n8zello reset-password NAME · journalctl -u n8zello -f
   Update:     cd $(dirname "$SRC_DIR") && git pull && sudo ./zellobridge/scripts/install.sh
"
