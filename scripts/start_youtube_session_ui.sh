#!/bin/sh
set -eu

DISPLAY_NUMBER="${DISPLAY:-:99}"
VNC_PORT="${YOUTUBE_SESSION_VNC_PORT:-5900}"
NOVNC_PORT="${YOUTUBE_SESSION_NOVNC_PORT:-6080}"
SCREEN_SIZE="${YOUTUBE_SESSION_SCREEN_SIZE:-1440x900x24}"

rm -f /tmp/.X99-lock
mkdir -p /tmp/.X11-unix

Xvfb "$DISPLAY_NUMBER" -screen 0 "$SCREEN_SIZE" -nolisten tcp -ac &
XVFB_PID=$!

export DISPLAY="$DISPLAY_NUMBER"
sleep 1

fluxbox >/tmp/youtube-session-fluxbox.log 2>&1 &
FLUXBOX_PID=$!

if [ -x /home/largas/dubplate-special/.venv/bin/celery ]; then
  /home/largas/dubplate-special/.venv/bin/celery -A config worker   --loglevel=info   --queues=youtube_session_login   --concurrency=1   --hostname=youtube-session-login@%h   >/tmp/youtube-session-celery.log 2>&1 &
else
  uv run celery -A config worker   --loglevel=info   --queues=youtube_session_login   --concurrency=1   --hostname=youtube-session-login@%h   >/tmp/youtube-session-celery.log 2>&1 &
fi
CELERY_PID=$!

x11vnc   -display "$DISPLAY_NUMBER"   -rfbport "$VNC_PORT"   -forever   -shared   -nopw   -localhost   >/tmp/youtube-session-x11vnc.log 2>&1 &
X11VNC_PID=$!

websockify   --web=/usr/share/novnc/   "$NOVNC_PORT"   "localhost:$VNC_PORT"   >/tmp/youtube-session-novnc.log 2>&1 &
NOVNC_PID=$!

cleanup() {
  kill "$NOVNC_PID" "$X11VNC_PID" "$FLUXBOX_PID" "$XVFB_PID" "$CELERY_PID" 2>/dev/null || true
}

trap cleanup INT TERM EXIT

while true
 do
  for pid in "$XVFB_PID" "$FLUXBOX_PID" "$X11VNC_PID" "$NOVNC_PID" "$CELERY_PID"
  do
    if ! kill -0 "$pid" 2>/dev/null
    then
      exit 1
    fi
  done
  sleep 2
done
