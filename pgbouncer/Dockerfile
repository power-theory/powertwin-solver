FROM pgbouncer/pgbouncer:1.15.0
COPY entrypoint.sh /opt/pgbouncer/custom-entrypoint.sh
RUN chmod +x /opt/pgbouncer/custom-entrypoint.sh
ENTRYPOINT ["/opt/pgbouncer/custom-entrypoint.sh"]
