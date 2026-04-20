#!/bin/bash
DB_PATH="$(dirname "$0")/backend/tradehub.db"

if [ -f "$DB_PATH" ]; then
  rm "$DB_PATH"
  echo "Deleted $DB_PATH"
else
  echo "No database found at $DB_PATH"
fi
