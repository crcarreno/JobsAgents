#!/usr/bin/env bash
set -euo pipefail

systemctl start autonomous-agent-web
systemctl start autonomous-agent-worker
systemctl --no-pager --lines=0 status autonomous-agent-web autonomous-agent-worker
