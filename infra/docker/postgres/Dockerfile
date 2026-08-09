# syntax=docker/dockerfile:1.11
#
# Bella Postgres — pgvector plus the tuning and roles the application expects.
#
# Production runs Cloud SQL, which provides its own image; this exists so that local and
# CI environments are schema- and extension-identical to production rather than
# "close enough".

ARG PG_MAJOR=17
FROM pgvector/pgvector:pg${PG_MAJOR}

# Scripts in this directory run once, in lexical order, on first initialisation of an
# empty data directory.
COPY initdb/ /docker-entrypoint-initdb.d/

# Sensible local defaults. Cloud SQL is configured through Terraform instead.
CMD ["postgres", \
     "-c", "max_connections=200", \
     "-c", "shared_buffers=256MB", \
     "-c", "work_mem=16MB", \
     "-c", "maintenance_work_mem=128MB", \
     "-c", "effective_cache_size=1GB", \
     "-c", "random_page_cost=1.1", \
     "-c", "log_min_duration_statement=500", \
     "-c", "log_lock_waits=on", \
     "-c", "track_io_timing=on"]
