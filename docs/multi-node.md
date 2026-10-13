# Multi-node setup

The panel drives one or more OpenVPN nodes through a small agent on each host.

## 1. Add the node

In **Nodes → New node** (or `POST /api/v1/nodes`) provide the public host and
port; set an agent URL such as `http://<public-ip>:8091`. The panel then shows a
**one-line install command**.

## 2. Run the install command on the node host

```bash
curl -fsSL https://panel.example.com/nodes/agent/install.sh | sudo bash -s -- \
  --secret '<generated>' --node_name de1 --agent_port 8091 \
  --panel_url https://panel.example.com
```

It creates the `ovpn-panel` service user, writes `/etc/ovpn-panel/agent.env`
(mode 600) with the shared secret, opens the agent port and enables
`ovpn-panel-agent@de1`.

## 3. Authentication

Panel→agent requests are signed with **HMAC-SHA256** over
`method\npath\ntimestamp\nbody` (`X-OVPN-Signature`, `X-OVPN-Timestamp`). The
agent also accepts a bearer token for hand-provisioned nodes. Rotate a node's
secret by re-issuing the install command and restarting the agent.

## 4. What the panel syncs

- **Users / limits / enable-disable**: pushed on every change; the agent
  materialises the user's limits locally.
- **On failure** the operation is queued (`node_tasks`) and retried with
  backoff. When a node becomes reachable again the scheduler reconciles its full
  user set.
- **Metrics**: CPU, RAM, disk, network and client count are collected and shown
  on the node page and the dashboard.

## 5. Per-user and per-reseller node access

- Subscriptions expose only the nodes a user is allowed to see
  (`hidden_node_ids`).
- Sub-admins can be pinned to an explicit node allow-list.

## 6. Firewall and TLS

- Allow the panel host to reach each agent port.
- Prefer putting the agent behind TLS (a reverse proxy with a certificate) and
  using an `https://` agent URL.
