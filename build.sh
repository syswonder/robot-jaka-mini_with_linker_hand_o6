#!/usr/bin/env bash
# Deployment build entry: prepares a deployment-local venv (robonix_api deps
# + grpcio-tools for package codegen, with --system-site-packages so rclpy
# from the system ROS 2 install is visible), then builds the selected
# manifests. Keeps the system python3 untouched (JetPack tensorflow pins
# protobuf<5; robonix_api needs protobuf 6).
set -euo pipefail
DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DEPLOY_DIR/.venv"

if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv --system-site-packages "$VENV"
fi
"$VENV/bin/pip" install -q \
  grpcio==1.80.0 protobuf==6.33.6 "mcp>=1.27,<2" "fastmcp>=3,<5" "pyyaml>=6" \
  grpcio-tools==1.76.0

# rbnx codegen inside fetched packages honors this interpreter override.
export RBNX_CODEGEN_PYTHON="$VENV/bin/python"
export PATH="$VENV/bin:$PATH"

# Local-first: prefer the gitignored path-based manifest (builds the sibling
# checkouts ../primitive-jaka-mini_with_linker_hand_o6-arm_hand-rbnx (arm +
# O6 hand in one primitive), ../primitive-orbbec-camera-rbnx and
# ../skill-jaka-grasp-rbnx directly — no GitHub clone / freshness fetch, so
# unpushed local edits take effect). Fall back
# to the committed url-based manifest on machines without it.
# --no-update-check keeps the url entry (robot_description) from probing
# GitHub; refresh it with `rbnx update`.
MANIFEST="$DEPLOY_DIR/robonix_manifest.local.yaml"
[[ -f "$MANIFEST" ]] || MANIFEST="$DEPLOY_DIR/robonix_manifest.yaml"
rbnx build --no-update-check -f "$MANIFEST"
