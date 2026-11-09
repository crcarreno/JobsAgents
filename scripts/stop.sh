#!/usr/bin/env bash
set -euo pipefail

systemctl stop autonomous-agent-worker
systemctl stop autonomous-agent-web
systemctl --no-pager --lines=0 status autonomous-agent-web autonomous-agent-worker || true
