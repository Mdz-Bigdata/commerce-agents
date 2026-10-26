#!/usr/bin/env bash
# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
#
# One-click start: double-click in Finder (macOS opens it in Terminal) or run ./start.command.
# Stops whatever an earlier run left behind, starts all four verticals on the code on disk,
# and opens the unified app, where the five demo apps run as one behind a rail. Arguments
# pass through to scripts/start_all.sh (./start.command retail, ./start.command --stop). Close
# the window or press Ctrl-C to stop.

cd "$(dirname "$0")" && exec scripts/start_all.sh --open "$@"
