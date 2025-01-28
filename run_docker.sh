#!/bin/bash

# Load environment variables from .env.local and .env.gitlab
echo "Loading env variables from './.env.local'"
set -o allexport
source ./.env.local
set -o allexport -

echo "Killing old Docker processes"
docker compose rm -fs

echo "Spinning up Docker containers"
docker compose build --force-rm --no-cache && \
docker compose up --detach && \
docker compose logs --follow
