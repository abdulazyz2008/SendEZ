#!/bin/bash
cd "$(dirname "$0")"
if command -v python3 >/dev/null; then python3 run.py --online; else echo "Install Python 3.10+ from https://www.python.org/downloads/"; read -r; fi
