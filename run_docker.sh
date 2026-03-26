#!/bin/bash

# Load environment variables from .env.local
echo "Loading env variables from './.env.local'"
set -o allexport
source ./.env.local
set +o allexport

echo "Killing old Docker processes"
docker compose -f docker-compose-local.yml rm -fs

echo "Spinning up Docker containers"
docker compose -f docker-compose-local.yml build && \
docker compose -f docker-compose-local.yml up --detach && \
docker compose -f docker-compose-local.yml logs --follow
