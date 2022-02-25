#!/bin/sh

if [ -f db_check ]; then
    echo "DB in already set up, starting bot..."
    python3 /app/src/bot.py
else
    echo "DB not initialized. Initializing DB..."
    alembic upgrade head
    touch db_check
    echo "DB initialized. Starting bot..."
    python3 /app/src/bot.py
fi
