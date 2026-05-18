FROM pgbouncer/pgbouncer:1.15.0
COPY --chmod=755 entrypoint.sh /opt/pgbouncer/custom-entrypoint.sh
ENTRYPOINT ["/opt/pgbouncer/custom-entrypoint.sh"]
