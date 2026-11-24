#!/bin/sh
set -e

if [ "$(id -u)" = "0" ] && command -v setpriv >/dev/null 2>&1; then
  mkdir -p /data
  chown -R appuser:appuser /data
  exec setpriv --reuid=appuser --regid=appuser --init-groups "$@"
fi

exec "$@"
