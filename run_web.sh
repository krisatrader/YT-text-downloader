#!/usr/bin/env bash
# Indító script a YouTube Átirat Letöltő webes felületéhez

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "============================================================"
echo "🚀 YouTube Átirat Letöltő Web Szerver Indítása..."
echo "🌐 Nyisd meg a böngészőben: http://127.0.0.1:8000"
echo "============================================================"

./venv/bin/python web_app.py
