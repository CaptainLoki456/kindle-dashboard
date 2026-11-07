#!/bin/sh
# Startet das Dashboard im Hintergrund (wird von KUAL aufgerufen).
DIR="/mnt/us/extensions/dashboard"
if [ -f /tmp/dashboard.pid ] && kill -0 "$(cat /tmp/dashboard.pid)" 2>/dev/null; then
    exit 0
fi
nohup sh "$DIR/dash.sh" >> "$DIR/log.txt" 2>&1 &
