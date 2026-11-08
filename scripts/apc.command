#!/bin/bash
# Double-click in Finder (or run from Terminal) to start the APC40 controller.
# Leave the window open while you work; close it or press Ctrl+C to stop.
cd "$(dirname "$0")/.." || exit 1
exec .venv/bin/python -u -m arranger.apc
