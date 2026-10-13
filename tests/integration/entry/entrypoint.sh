#!/bin/sh
set -e

: "${ROOT_PASSWORD:=root}"
echo "root:${ROOT_PASSWORD}" | chpasswd

# Regenerate host keys on first boot so the test can pin a fresh fingerprint.
ssh-keygen -A

exec /usr/sbin/sshd -D -e
