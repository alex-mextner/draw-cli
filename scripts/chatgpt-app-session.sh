#!/bin/sh
# Starts the CLOSED app normally with a loopback control port and writes a
# credentials-free report under /tmp/draw-app-report-*/report.json.
# Does not quit/restart a running app, modify it, or copy its credentials.
set -eu
exec draw app-session --launch --probe-web-session "$@"
