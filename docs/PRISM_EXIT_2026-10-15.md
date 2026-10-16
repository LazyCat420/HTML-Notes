# Prism exit — lazy-agent-service is the only harness (2026-10-15)

Per the sun-wide Prism exit decision, HTML-Notes no longer calls prism-service
(`:7777`) anywhere. lazy-agent-service (`http://10.0.0.16:5591`, env
`LAZY_AGENT_URL`) is the single agent harness for every path:

- The `use_lazy_agent` request flag and the Prism mode branch in the turn
  handler are removed; the agent turn always targets `{LAZY_AGENT_URL}/agent`.
- The Prism health polling of `/mcp-servers` + `/custom-agents` and the MCP
  reconnect/watchdog are removed. `GET /health/app` (and the boot check)
  probes `{LAZY_AGENT_URL}/config` instead; it reports a `gateway` key, not
  `prism`.
- `GET /models` reads `{LAZY_AGENT_URL}/config?includeLocal=true`.
- `{PRISM_URL}/audio-to-text` became `{LAZY_AGENT_URL}/audio-to-text` (route
  exists natively on the gateway).
- `PRISM_URL` / `PRISM_AGENT_ID` are gone from `app/config.py` /
  `app/main.py`; the compose file now sets only
  `LAZY_AGENT_URL=${LAZY_AGENT_URL:-http://10.0.0.16:5591}`.

NAS env change: remove `PRISM_URL` from the html-notes container environment
(nothing in the image reads it anymore). `LAZY_AGENT_URL` already defaults
correctly; set it explicitly only if the gateway moves.
