#!/bin/sh
set -eu
exec "${PYTHON:-python3}" -m pipeline.sign keygen "${1:-.week3/keys}"
