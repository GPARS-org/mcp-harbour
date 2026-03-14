# Contributing to MCP Harbour

## Development Setup

```bash
git clone https://github.com/GPARS-org/mcp-harbour.git
cd mcp-harbour
uv venv
source .venv/bin/activate    # or .venv\Scripts\activate on Windows
uv pip install -e ".[dev]"
```

## Running Tests

```bash
pytest
```

## Project Structure

```
mcp_harbour/
├── main.py              # CLI entry point (harbour)
├── bridge.py            # Agent bridge entry point (harbour-bridge)
├── models.py            # Pydantic data models
├── config.py            # ConfigManager, file paths, platform detection
├── process_manager.py   # ServerProcess, HarbourDaemon
├── gateway.py           # HarbourGateway, session factory, MCP stream handling
├── permissions.py       # PermissionEngine, policy matching
└── errors.py            # GPARS error codes (AUTHORIZATION_DENIED, SERVER_UNAVAILABLE)
```

## Architecture

MCP Harbour implements the [GPARS](https://github.com/GPARS-org/GPARS) plane boundary. The key architectural rule: the agent (Cognitive Plane) never talks directly to MCP servers (Action Plane). All traffic flows through the gateway, which enforces the user's security policy.

Two entry points exist by design:
- `harbour` — admin CLI for managing servers, identities, and policies
- `harbour-bridge` — minimal agent bridge with zero admin capabilities

These are intentionally separate so that agents cannot access admin commands.

## What We're Looking For

- Bug fixes and reliability improvements
- Additional policy match types (beyond glob, regex, exact)
- Performance improvements to the gateway proxy
- Test coverage for edge cases
- Documentation improvements

## Guidelines

- Run `pytest` before submitting. All tests must pass.
- Keep `harbour-bridge` dependency-free (stdlib only). It must not import any admin modules.
- Policy enforcement is default-deny. No code path should allow access without an explicit policy check.
- Errors returned to agents must use GPARS error codes (`-31001` for `AUTHORIZATION_DENIED`, `-31002` for `SERVER_UNAVAILABLE`).
- Don't leak policy details in error messages. The agent should know *what* was denied, not *what would be allowed*.
