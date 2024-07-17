#!/bin/bash

echo "Killing old docker processes"
docker compose rm -fs

echo "Loading env variables from './.env.local'"
export ENV_FILE=./.env.local

echo "Building docker containers"
docker compose --env-file $ENV_FILE build && docker compose --env-file $ENV_FILE up --detach && docker compose --env-file $ENV_FILE logs --follow
