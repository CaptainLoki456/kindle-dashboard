#!/bin/sh
# Test: holt das Bild einmal und zeigt es an, ohne die Kindle-Oberfläche zu
# beenden. Nach ein paar Sekunden zeichnet der Kindle seine Oberfläche wieder.
DIR="/mnt/us/extensions/dashboard"
tr -d '\r' < "$DIR/einstellungen.txt" > /tmp/dashboard-einst.sh
. /tmp/dashboard-einst.sh
eval "$(sed -n '/^log()/,/^}/p;/^aufloesung_ermitteln()/,/^}/p;/^bild_holen()/,/^}/p' "$DIR/dash.sh")"
LOG="$DIR/log.txt"; BILD="$DIR/dash.png"
AUFL="$(aufloesung_ermitteln)"
log "Test: Auflösung $AUFL"
if bild_holen; then
    eips -f -g "$BILD"
    log "Test: Bild angezeigt"
else
    eips 1 3 " Fehler - Details in log.txt "
fi
