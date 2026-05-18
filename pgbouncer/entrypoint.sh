#!/bin/sh
set -e

# Resolve hostname to IP — c-ares cannot talk to Docker's embedded DNS (127.0.0.11)
# Retry briefly in case postgres hasn't registered in Docker DNS yet
DB_HOST=${DATABASES_HOST:-postgres-db}
for i in 1 2 3 4 5; do
  RESOLVED_IP=$(getent hosts "$DB_HOST" | awk '{print $1}' | head -1)
  [ -n "$RESOLVED_IP" ] && break
  sleep 2
done
if [ -n "$RESOLVED_IP" ]; then
  DB_HOST=$RESOLVED_IP
fi

cat > /etc/pgbouncer/pgbouncer.ini <<EOF
[databases]
* = host=${DB_HOST} port=${DATABASES_PORT:-5432} user=${DATABASES_USER:-postgres} password=${DATABASES_PASSWORD} dbname=${DATABASES_DBNAME:-powertwin}

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
auth_type = any
pool_mode = transaction
max_client_conn = ${MAX_CLIENT_CONN:-1000}
default_pool_size = ${DEFAULT_POOL_SIZE:-25}
max_db_connections = ${MAX_DB_CONNECTIONS:-100}
admin_users = ${ADMIN_USERS:-postgres}
ignore_startup_parameters = ${IGNORE_STARTUP_PARAMETERS:-extra_float_digits,application_name}
server_reset_query = DISCARD ALL
resolv_conf = /etc/resolv.conf
EOF

exec /opt/pgbouncer/pgbouncer /etc/pgbouncer/pgbouncer.ini
