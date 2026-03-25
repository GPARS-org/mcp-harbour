import typer
import asyncio
from pathlib import Path
from typing import List, Optional
from rich.console import Console
from rich.table import Table
from .models import Server, ServerType, Identity, ToolPermission, ArgumentPolicy
from .config import ConfigManager

import secrets
import string
import keyring
import bcrypt

app = typer.Typer(help="MCP Harbour: Manage your MCP servers and permissions.")
console = Console()
config_manager = ConfigManager()

# Sub-typer for identity management
identity_app = typer.Typer()
app.add_typer(identity_app, name="identity", help="Manage identities (Captains)")

# Sub-typer for permission management
permit_app = typer.Typer()
app.add_typer(permit_app, name="permit", help="Manage permissions (Policies)")


@app.command()
def dock(
    name: str = typer.Option(..., help="Name of the server/ship"),
    command: str = typer.Option(..., help="Full command to run the server (e.g. 'npx -y @modelcontextprotocol/server-filesystem /path')"),
    server_type: ServerType = typer.Option(ServerType.stdio, help="Type of connection"),
):
    """
    Dock (install/register) a new MCP server.

    The --command should be the full command including arguments, e.g.:
    harbour dock --name filesystem --command "npx -y @modelcontextprotocol/server-filesystem /home/user"
    """
    if config_manager.get_server(name):
        console.print(f"[bold red]Error:[/bold red] Server '{name}' already docked.")
        raise typer.Exit(code=1)

    server = Server(
        name=name,
        command=command,
        server_type=server_type,
    )
    config_manager.add_server(server)
    console.print(
        f"[bold green]Success:[/bold green] Server '{name}' docked successfully!"
    )


@app.command()
def undock(name: str):
    """
    Undock (remove) an MCP server.
    """
    if not config_manager.get_server(name):
        console.print(f"[bold red]Error:[/bold red] Server '{name}' not found.")
        raise typer.Exit(code=1)

    config_manager.remove_server(name)
    console.print(f"[bold green]Success:[/bold green] Server '{name}' undocked.")


@app.command("list")
def list_servers():
    """
    List all docked MCP servers.
    """
    servers = config_manager.list_servers()
    if not servers:
        console.print("No servers docked.")
        return

    table = Table(title="Docked Ships (MCP Servers)")
    table.add_column("Name", style="cyan")
    table.add_column("Command", style="magenta")
    table.add_column("Type", style="green")
    for server in servers:
        table.add_row(
            server.name,
            server.command,
            server.server_type.value,
        )

    console.print(table)


@app.command()
def inspect(name: str):
    """
    Inspect details of a docked server.
    """
    server = config_manager.get_server(name)
    if not server:
        console.print(f"[bold red]Error:[/bold red] Server '{name}' not found.")
        raise typer.Exit(code=1)

    console.print(f"[bold]Name:[/bold] {server.name}")
    console.print(f"[bold]Command:[/bold] {server.command}")
    console.print(f"[bold]Args:[/bold] {server.args}")
    console.print(f"[bold]Env:[/bold] {server.env}")
    console.print(f"[bold]Type:[/bold] {server.server_type}")


@app.command()
def serve(
    host: str = typer.Option(None, help="Host to bind (default: 127.0.0.1)"),
    port: int = typer.Option(None, help="Port to bind (default: 4767)"),
):
    """
    Start the Harbour Daemon in the foreground.
    """
    from .gateway import HarbourGateway
    from .config import DEFAULT_HOST, DEFAULT_PORT
    import sys
    import logging

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    serve_host = host or DEFAULT_HOST
    serve_port = port or DEFAULT_PORT

    gateway = HarbourGateway()
    sys.stderr.write(f"Starting Harbour Daemon ({serve_host}:{serve_port})...\n")
    asyncio.run(gateway.serve(serve_host, serve_port))


@app.command()
def start():
    """
    Start the Harbour Daemon via the platform service manager.
    """
    import subprocess
    import sys

    if sys.platform == "linux":
        subprocess.run(["systemctl", "--user", "start", "mcp-harbour"], check=True)
    elif sys.platform == "darwin":
        plist = f"{Path.home()}/Library/LaunchAgents/dev.mcp-harbour.daemon.plist"
        subprocess.run(["launchctl", "load", plist], check=True)
    elif sys.platform == "win32":
        subprocess.run(["schtasks", "/Run", "/TN", "MCP Harbour Daemon"], check=True, capture_output=True)
    else:
        console.print("[bold red]Unsupported platform.[/bold red]")
        raise typer.Exit(1)
    console.print("[bold green]Daemon started.[/bold green]")


@app.command()
def stop():
    """
    Stop the Harbour Daemon via the platform service manager.
    """
    import subprocess
    import sys

    if sys.platform == "linux":
        subprocess.run(["systemctl", "--user", "stop", "mcp-harbour"], check=True)
    elif sys.platform == "darwin":
        plist = f"{Path.home()}/Library/LaunchAgents/dev.mcp-harbour.daemon.plist"
        subprocess.run(["launchctl", "unload", plist], check=True)
    elif sys.platform == "win32":
        subprocess.run(["schtasks", "/End", "/TN", "MCP Harbour Daemon"], check=True, capture_output=True)
    else:
        console.print("[bold red]Unsupported platform.[/bold red]")
        raise typer.Exit(1)
    console.print("[bold green]Daemon stopped.[/bold green]")


@app.command()
def status():
    """
    Check if the Harbour Daemon is running.
    """
    import subprocess
    import sys

    if sys.platform == "linux":
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "mcp-harbour"],
            capture_output=True, text=True
        )
        state = result.stdout.strip()
        if state == "active":
            console.print("[bold green]Daemon is running.[/bold green]")
        else:
            console.print(f"[yellow]Daemon is {state}.[/yellow]")
    elif sys.platform == "darwin":
        result = subprocess.run(
            ["launchctl", "list", "dev.mcp-harbour.daemon"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            console.print("[bold green]Daemon is running.[/bold green]")
        else:
            console.print("[yellow]Daemon is not running.[/yellow]")
    elif sys.platform == "win32":
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", "MCP Harbour Daemon", "/FO", "LIST"],
            capture_output=True, text=True
        )
        if "Running" in result.stdout:
            console.print("[bold green]Daemon is running.[/bold green]")
        else:
            console.print("[yellow]Daemon is not running.[/yellow]")
    else:
        console.print("[bold red]Unsupported platform.[/bold red]")
        raise typer.Exit(1)


@identity_app.command("create")
def identity_create(name: str):
    """Create a new identity (Captain) and generate an API key."""
    if config_manager.get_identity(name):
        console.print(f"[bold red]Identity '{name}' already exists![/bold red]")
        raise typer.Exit(1)

    alphabet = string.ascii_letters + string.digits
    token = "".join(secrets.choice(alphabet) for i in range(32))
    api_key = f"harbour_sk_{token}"
    key_prefix = api_key[:15] + "..."

    hashed_key_bytes = bcrypt.hashpw(api_key.encode(), bcrypt.gensalt())
    keyring.set_password("mcp-harbour", name, hashed_key_bytes.decode())

    identity = Identity(name=name, key_prefix=key_prefix)
    config_manager.add_identity(identity)

    console.print(f"[bold green]Identity '{name}' created successfully![/bold green]")
    console.print(f"API Key: [bold]{api_key}[/bold]")
    console.print("[yellow]Keep this key safe! It won't be shown again.[/yellow]")


@identity_app.command("list")
def identity_list():
    """List all identities."""
    identities = config_manager.config.identities

    if not identities:
        console.print("No identities found.")
        return

    table = Table(title="Docked Captains (Identities)")
    table.add_column("Name", style="cyan")
    table.add_column("API Key Prefix", style="magenta")

    for name, identity in identities.items():
        table.add_row(name, identity.key_prefix)

    console.print(table)


@identity_app.command("delete")
def identity_delete(name: str):
    """Delete an identity (Captain) and its policy."""
    if not config_manager.get_identity(name):
        console.print(f"[bold red]Error:[/bold red] Identity '{name}' not found.")
        raise typer.Exit(code=1)

    try:
        keyring.delete_password("mcp-harbour", name)
    except keyring.errors.PasswordDeleteError:
        pass

    config_manager.remove_identity(name)
    console.print(f"[bold green]Success:[/bold green] Identity '{name}' deleted.")


@permit_app.command("allow")
def permit_allow(
    identity: str,
    server: str,
    tool: str = typer.Option("*", help="Tool name or glob pattern (default: *)"),
    args: Optional[List[str]] = typer.Option(
        None, help="Argument policies: 'arg=pattern' (glob) or 'arg=re:pattern' (regex)"
    ),
):
    """
    Grant permission to an identity.

    Examples:
      harbour permit allow agent filesystem
      harbour permit allow agent filesystem --tool "read_*" --args "path=/home/user/**"
      harbour permit allow agent db --tool "query" --args "sql=re:^SELECT.*" "db=production"
    """
    if not config_manager.get_identity(identity):
        console.print(f"[bold red]Identity '{identity}' not found![/bold red]")
        raise typer.Exit(1)

    if not config_manager.get_server(server) and server != "*":
        console.print(
            f"[yellow]Warning: Server '{server}' is not currently docked.[/yellow]"
        )

    policies = []
    if args:
        for arg_str in args:
            try:
                key, pattern = arg_str.split("=", 1)
                if pattern.startswith("re:"):
                    match_type = "regex"
                    pattern = pattern[3:]
                else:
                    match_type = "glob"

                policies.append(
                    ArgumentPolicy(arg_name=key, match_type=match_type, pattern=pattern)
                )
            except ValueError:
                console.print(
                    f"[bold red]Invalid argument policy format: {arg_str}. Use key=pattern or key=re:pattern[/bold red]"
                )
                raise typer.Exit(1)

    policy = config_manager.load_policy(identity)
    if not policy:
        policy = config_manager.create_policy(identity)

    if server not in policy.permissions:
        policy.permissions[server] = []

    new_perm = ToolPermission(name=tool, policies=policies)
    policy.permissions[server].append(new_perm)

    config_manager.save_policy(policy)
    console.print(
        f"[bold green]Permission granted for '{identity}' on '{server}' tool '{tool}'[/bold green]"
    )


@permit_app.command("show")
def permit_show(identity: str):
    """Show the policy for an identity."""
    policy = config_manager.load_policy(identity)

    if not policy:
        console.print(
            f"[yellow]No policy found for '{identity}'. (Access Denied All)[/yellow]"
        )
        return

    console.print(f"[bold]Policy for {identity}:[/bold]")
    for server, tools in policy.permissions.items():
        console.print(f"  Server: [cyan]{server}[/cyan]")
        for tool in tools:
            pol_str = ""
            if tool.policies:
                pol_str = " -> " + ", ".join(
                    [
                        f"{p.arg_name}={'re:' if p.match_type == 'regex' else ''}{p.pattern}"
                        for p in tool.policies
                    ]
                )
            console.print(f"    - Tool: [green]{tool.name}[/green]{pol_str}")


if __name__ == "__main__":
    app()
