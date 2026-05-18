#!/bin/sh
set -e

# Run the base entrypoint to generate pgbouncer.ini from env vars,
# but suppress its exec so we can patch the config before starting.
QUIET=1 /opt/pgbouncer/entrypoint.sh &
PID=$!
sleep 2
kill $PID 2>/dev/null
wait $PID 2>/dev/null || true

# Force pgbouncer to use the system resolver (Docker's 127.0.0.11)
# instead of c-ares, which fails to resolve container hostnames.
echo "resolv_conf = /etc/resolv.conf" >> /etc/pgbouncer/pgbouncer.ini

exec /opt/pgbouncer/pgbouncer /etc/pgbouncer/pgbouncer.ini
