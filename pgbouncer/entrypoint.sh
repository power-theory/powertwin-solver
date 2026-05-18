#!/bin/sh
set -e

cat > /etc/pgbouncer/pgbouncer.ini <<EOF
[databases]
* = host=${DATABASES_HOST:-postgres-db} port=${DATABASES_PORT:-5432} user=${DATABASES_USER:-postgres} password=${DATABASES_PASSWORD} dbname=${DATABASES_DBNAME:-powertwin}

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
