# MCP Harbour

A security enforcement point for MCP servers. Sits between AI agents and MCP servers, enforcing per-agent security policies defined by the user.

Built as an implementation of the [GPARS](https://github.com/GPARS-org/GPARS) plane boundary — the user-controlled layer that verifies agent identity and governs what agents are permitted to do.

## Install

**Linux / macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/GPARS-org/mcp-harbour/main/scripts/install.sh | bash
```

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/GPARS-org/mcp-harbour/main/scripts/install.ps1 | iex
```

This installs the package, registers the daemon as a system service, and starts it.

## Quick Start

```bash
# Dock an MCP server
harbour dock --name filesystem \
  --command "npx -y @modelcontextprotocol/server-filesystem /home/user/projects"

# Create an identity for your agent
harbour identity create my-agent

# Grant permissions
harbour permit allow my-agent filesystem --tool "*" --args "path=/home/user/projects/**"
```

Then configure your MCP client (Claude Desktop, VS Code, Cursor):

```json
{
  "mcpServers": {
    "harbour": {
      "command": "harbour-bridge",
      "args": ["--token", "harbour_sk_..."]
    }
  }
}
```

The agent sees a single MCP server with tools from all docked servers — filtered and enforced by its policy. No policy means no access.

## How It Works

```
Agent → harbour-bridge → TCP:4767 → HarbourGateway → MCP Servers
              │                           │
         (no admin          identity verification
          access)           policy enforcement
                            AUTHORIZATION_DENIED / SERVER_UNAVAILABLE
```

## Documentation

| Doc | Description |
|-----|-------------|
| [Architecture](docs/architecture.md) | System design, GPARS alignment |
| [CLI Reference](docs/cli-reference.md) | All commands and options |
| [Permissions](docs/permissions.md) | Policy engine, GPARS error codes |
| [Configuration](docs/configuration.md) | Config format, file layout |
| [Contributing](CONTRIBUTING.md) | Development setup, guidelines |

## Author

[Ismael Kaissy](https://github.com/15m43lk4155y)

## License

This project is licensed under the [MIT License](./LICENSE).
