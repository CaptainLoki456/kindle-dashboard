#!/bin/sh
# Kindle-Dashboard: holt regelmäßig ein PNG und zeigt es mit eips an.
# Gestartet über KUAL ("Dashboard starten"). Beenden: Power-Taste lange
# gedrückt halten, bis der Kindle neu startet.

DIR="/mnt/us/extensions/dashboard"
LOG="$DIR/log.txt"
BILD="$DIR/dash.png"

# Einstellungen laden (Windows-Zeilenenden entfernen, falls am PC bearbeitet)
tr -d '\r' < "$DIR/einstellungen.txt" > /tmp/dashboard-einst.sh
. /tmp/dashboard-einst.sh
export TZ

log() {
    [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 200000 ] && tail -n 300 "$LOG" > "$LOG.alt" && mv "$LOG.alt" "$LOG"
    echo "$(date '+%d.%m. %H:%M:%S') $*" >> "$LOG"
}

ui_stoppen() {
    log "Kindle-Oberfläche wird angehalten"
    if [ -x /etc/init.d/framework ]; then
        /etc/init.d/framework stop
    else
        stop lab126_gui >/dev/null 2>&1
        stop framework >/dev/null 2>&1
    fi
    initctl stop webreader >/dev/null 2>&1
    lipc-set-prop com.lab126.powerd preventScreenSaver 1 >/dev/null 2>&1
    echo powersave > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null
    sleep 2
}

aufloesung_ermitteln() {
    if [ "$AUFLOESUNG" != "auto" ]; then echo "$AUFLOESUNG"; return; fi
    info="$(eips -i 2>/dev/null)"
    xr="$(echo "$info" | grep -o 'xres[^0-9_]*[0-9][0-9]*' | head -n1 | grep -o '[0-9][0-9]*$')"
    yr="$(echo "$info" | grep -o 'yres[^0-9_]*[0-9][0-9]*' | head -n1 | grep -o '[0-9][0-9]*$')"
    if [ -n "$xr" ] && [ -n "$yr" ]; then
        if [ "$xr" -gt "$yr" ]; then t=$xr; xr=$yr; yr=$t; fi   # immer Hochformat
        echo "${xr}x${yr}"
    else
        echo "1072x1448"
    fi
}

warte_wlan() {
    i=0
    while [ $i -lt 40 ]; do
        [ "$(lipc-get-prop com.lab126.wifid cmState 2>/dev/null)" = "CONNECTED" ] && return 0
        [ $i -eq 15 ] && lipc-set-prop com.lab126.wifid enable 1 >/dev/null 2>&1
        sleep 2
        i=$((i + 1))
    done
    return 1
}

bild_holen() {
    url="${BILD_URL%/}/dash-${AUFL}.png"
    opt=""
    [ "$UNSICHERES_HTTPS" = "ja" ] && opt="-k"
    rm -f "$BILD.neu"
    if command -v curl >/dev/null 2>&1; then
        fehler="$(curl -sSfL $opt --max-time 60 -o "$BILD.neu" "$url" 2>&1)" || { log "Download fehlgeschlagen: $fehler ($url)"; return 1; }
    else
        wget -q -T 60 -O "$BILD.neu" "$url" || { log "Download fehlgeschlagen (wget): $url"; return 1; }
    fi
    [ -s "$BILD.neu" ] || { log "Leere Datei erhalten"; return 1; }
    mv "$BILD.neu" "$BILD"
}

anzeigen() {
    if [ "$zaehler" -ge "$VOLLE_AUFFRISCHUNG_ALLE" ]; then
        eips -f -g "$BILD" >/dev/null 2>&1
        zaehler=0
    else
        eips -g "$BILD" >/dev/null 2>&1
    fi
    zaehler=$((zaehler + 1))

    akku="$(lipc-get-prop com.lab126.powerd battLevel 2>/dev/null)"
    laedt="$(lipc-get-prop com.lab126.powerd isCharging 2>/dev/null)"
    if [ -n "$akku" ] && [ "$akku" -lt "$AKKU_WARNUNG" ] 2>/dev/null && [ "$laedt" != "1" ]; then
        eips 1 1 " Akku ${akku}% - bitte laden " >/dev/null 2>&1
    fi
}

zahl() { echo "$1" | sed 's/^0*//;s/^$/0/'; }

wartezeit() {
    h=$(zahl "$(date +%H)"); m=$(zahl "$(date +%M)"); s=$(zahl "$(date +%S)")
    jetzt=$((h * 60 + m))
    nacht=0
    if [ "$NACHTRUHE_VON" -ne "$NACHTRUHE_BIS" ]; then
        if [ "$NACHTRUHE_VON" -gt "$NACHTRUHE_BIS" ]; then
            { [ "$h" -ge "$NACHTRUHE_VON" ] || [ "$h" -lt "$NACHTRUHE_BIS" ]; } && nacht=1
        else
            [ "$h" -ge "$NACHTRUHE_VON" ] && [ "$h" -lt "$NACHTRUHE_BIS" ] && nacht=1
        fi
    fi
    if [ $nacht -eq 1 ]; then
        ziel=$((NACHTRUHE_BIS * 60 + VERSATZ_MINUTEN))
        [ $ziel -le $jetzt ] && ziel=$((ziel + 1440))
    else
        ziel=$(( ((jetzt - VERSATZ_MINUTEN) / INTERVALL_MINUTEN + 1) * INTERVALL_MINUTEN + VERSATZ_MINUTEN ))
        [ $ziel -le $jetzt ] && ziel=$((ziel + INTERVALL_MINUTEN))
    fi
    sek=$(( (ziel - jetzt) * 60 - s ))
    [ $sek -lt 60 ] && sek=60
    echo $sek
}

schlafen() {
    sek=$1
    if [ "$TIEFSCHLAF" = "ja" ]; then
        rtc=""
        for r in /sys/class/rtc/rtc1 /sys/class/rtc/rtc0; do
            [ -e "$r/wakealarm" ] && rtc="$r" && break
        done
        if [ -n "$rtc" ]; then
            vorher=$(date +%s)
            if echo 0 > "$rtc/wakealarm" && echo "+$sek" > "$rtc/wakealarm" && echo mem > /sys/power/state; then
                vergangen=$(( $(date +%s) - vorher ))
                # Suspend wurde abgelehnt oder zu früh beendet -> Rest normal warten
                if [ $vergangen -lt $((sek - 30)) ]; then
                    sleep $((sek - vergangen))
                else
                    zaehler=$VOLLE_AUFFRISCHUNG_ALLE   # nach dem Aufwachen sauber neu zeichnen
                fi
                return
            fi
            log "Tiefschlaf nicht möglich, warte normal"
        fi
    fi
    sleep "$sek"
}

# ------------------------------------------------------------------ Start
echo $$ > /tmp/dashboard.pid
log "=== Dashboard startet ==="
AUFL="$(aufloesung_ermitteln)"
log "Auflösung: $AUFL"
ui_stoppen
zaehler=$VOLLE_AUFFRISCHUNG_ALLE   # erstes Bild mit voller Auffrischung

while true; do
    if warte_wlan; then
        bild_holen && anzeigen && log "Bild aktualisiert"
    else
        log "Kein WLAN"
    fi
    sek="$(wartezeit)"
    log "Nächste Aktualisierung in $((sek / 60)) Min."
    schlafen "$sek"
done
