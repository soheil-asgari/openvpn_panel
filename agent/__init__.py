"""OpenVPN node agent.

A small FastAPI service running on each node. It authenticates the panel with a
per-node bearer token (hashed at rest) and exposes a narrow, validated API for
status, client management and certificate operations. It never runs the panel's
code and never accepts shell strings.
"""
