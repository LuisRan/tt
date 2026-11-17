#!/bin/sh
set -eu
umask 077
exec python3 /usr/local/bin/respaldo.py "$@"
