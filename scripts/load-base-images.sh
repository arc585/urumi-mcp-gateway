#!/bin/sh
# Workaround for Docker Desktop image pulls hanging (seen on this Mac, where even `docker pull busybox`
# hung while the internet worked). The Kind node has its own containerd that pulls fine, so we fetch
# the official base images there and copy them into Docker's local image store. `docker build` then
# uses the local copies and never touches the stuck pull path. The images are unchanged.
#
# Usage: sh scripts/load-base-images.sh   (needs the Kind cluster "urumi" running)
set -eu

NODE=urumi-control-plane
PLATFORM=linux/$(docker version --format '{{.Server.Arch}}')
IMAGES="docker.io/library/python:3.12-slim docker.io/library/node:22-alpine docker.io/nginxinc/nginx-unprivileged:1.30-alpine"

for ref in $IMAGES; do
  echo "== $ref"
  # `content fetch` keeps the compressed layers (Kind's containerd discards them after a normal pull),
  # which `images export` needs.
  docker exec "$NODE" ctr -n k8s.io content fetch --platform "$PLATFORM" "$ref" >/dev/null
  docker exec "$NODE" ctr -n k8s.io images export --platform "$PLATFORM" - "$ref" | docker load
done
