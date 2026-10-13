"""Privileged helper package.

The helper is the only component that runs as root. It exposes a tiny,
allow-listed RPC surface over a unix socket and never executes arbitrary
commands: each action maps to a fixed argv list and validated arguments.
"""
