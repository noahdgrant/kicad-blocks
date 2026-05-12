#!/bin/bash

# Check if VIRTUAL_ENV is NOT set
if [[ -z "$VIRTUAL_ENV" ]]; then
    echo "Error: No Python virtual environment is active."
    echo "Please activate your environment before running this script."
    exit 1
fi

# Get the absolute path to the directory containing THIS script
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)

claude --permission-mode acceptEdits "@$SCRIPT_DIR/PROMPT.md"
