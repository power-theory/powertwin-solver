#!/bin/bash
FLAG_FILE="/app/app/upload/simulation_run.flag"

if [ ! -f "$FLAG_FILE" ]; then
    #curl http://localhost:8080/test_db
    sleep 3

    curl -X POST http://localhost:8080/autorun_simulation    
else
    echo "Autosimulation has already been run. Skipping curl command."
fi

