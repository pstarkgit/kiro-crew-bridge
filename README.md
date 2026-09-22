# Kiro Crew Bridge (Codex → KiroCrew)

Local, user-authorized bridge so **Codex** can dispatch bounded work to a running **Kiro Crew** gateway, with receipt tracking and no approval bypass.

This is a Codex plugin + MCP server. It talks only to `http://127.0.0.1:5476` on your machine.

## What it does

- Checks whether the local Kiro Crew gateway is ready
- Dispatches an explicitly authorized, bounded task into Crew
- Tracks idempotent receipts under `~/.local/state/codex-kiro-crew`
- Keeps native Crew approvals in force (no secret leakage in argv/outputs)

## Requirements

- macOS/Linux with Python 3
- Kiro Crew gateway running locally (default port **5476**)
- Gateway credential at `~/.kiro/crew/run/gateway-5476.secret` or `~/.kiro/crew/.local_secret`

## Install (Codex plugin)

1. Copy or clone this repo into your Codex plugins directory, e.g. `~/.codex/plugins/kiro-crew-bridge` or `~/plugins/kiro-crew-bridge`.
2. Ensure `.mcp.json` points at `scripts/run-mcp.sh` (already configured with a relative path).
3. Restart Codex / reload plugins.
4. Start Kiro Crew so the local gateway is up.

## Manual MCP smoke test

```bash
python3 scripts/bridge.py --help
python3 -m unittest discover -s tests -v
```

## Security notes

- Localhost only; redirects refused; proxy disabled for gateway calls
- Credential files must be private regular files owned by you
- Task text is capped; results are size-limited
- Gateway error bodies are not echoed (rejection codes only)

## License

MIT (unless you add a different LICENSE).
