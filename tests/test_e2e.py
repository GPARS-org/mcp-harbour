"""
End-to-end tests that start a real daemon, connect via TCP,
and exercise the full MCP protocol through the gateway.

These tests use the @modelcontextprotocol/server-filesystem MCP server
as a real downstream server. They require `npx` to be available.

Tests cover:
- Handshake + authentication
- MCP initialize
- tools/list with policy filtering
- tools/call with allowed operations
- tools/call with AUTHORIZATION_DENIED (path policy violation)
- tools/call with AUTHORIZATION_DENIED (tool not permitted)
- Default deny for identities without policies
"""

import os
import json
import shutil
import socket
import asyncio
import secrets
import string
import pytest
import pytest_asyncio
import keyring
import bcrypt
from pathlib import Path

from mcp_harbour.gateway import HarbourGateway
from mcp_harbour.config import ConfigManager
from mcp_harbour.models import (
    Server,
    Identity,
    AgentPolicy,
    ToolPermission,
    ArgumentPolicy,
)
from mcp_harbour.errors import AUTHORIZATION_DENIED_CODE


# ─── Helpers ────────────────────────────────────────────────────────


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def generate_token() -> str:
    alphabet = string.ascii_letters + string.digits
    raw = "".join(secrets.choice(alphabet) for _ in range(32))
    return f"harbour_sk_{raw}"


class MCPClient:
    """Minimal MCP client that speaks JSON-RPC over TCP with harbour handshake."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.reader = None
        self.writer = None
        self._req_id = 0

    async def connect(self, token: str) -> dict:
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        # Handshake
        self.writer.write(json.dumps({"auth": token}).encode() + b"\n")
        await self.writer.drain()
        ack_line = await asyncio.wait_for(self.reader.readline(), timeout=5)
        return json.loads(ack_line.decode())

    async def request(self, method: str, params: dict = None, timeout: float = 10) -> dict:
        self._req_id += 1
        msg = {"jsonrpc": "2.0", "id": self._req_id, "method": method}
        if params is not None:
            msg["params"] = params
        self.writer.write(json.dumps(msg).encode() + b"\n")
        await self.writer.drain()
        line = await asyncio.wait_for(self.reader.readline(), timeout=timeout)
        return json.loads(line.decode())

    async def notify(self, method: str, params: dict = None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.writer.write(json.dumps(msg).encode() + b"\n")
        await self.writer.drain()

    async def initialize(self) -> dict:
        resp = await self.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "e2e-test", "version": "0.1.0"},
        })
        await self.notify("notifications/initialized")
        return resp

    async def list_tools(self) -> list:
        resp = await self.request("tools/list", {})
        return resp.get("result", {}).get("tools", [])

    async def call_tool(self, name: str, arguments: dict = None) -> dict:
        return await self.request("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })

    async def close(self):
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def e2e_dir(tmp_path):
    """Create isolated config + test data directories."""
    config_dir = tmp_path / "harbour-config"
    config_dir.mkdir()
    (config_dir / "policies").mkdir()

    test_data = tmp_path / "test-data"
    test_data.mkdir()
    (test_data / "readme.txt").write_text("hello from e2e test")
    (test_data / "subdir").mkdir()
    (test_data / "subdir" / "nested.txt").write_text("nested content")
    (test_data / "secret.txt").write_text("top secret")

    return {"config_dir": config_dir, "test_data": test_data}


@pytest.fixture
def e2e_config(e2e_dir, monkeypatch):
    """Set up ConfigManager pointing to the temp directory."""
    import mcp_harbour.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", e2e_dir["config_dir"])
    monkeypatch.setattr(config_mod, "CONFIG_FILE", e2e_dir["config_dir"] / "config.json")
    monkeypatch.setattr(config_mod, "POLICIES_DIR", e2e_dir["config_dir"] / "policies")
    monkeypatch.setattr(config_mod, "DEFAULT_HOST", "127.0.0.1")
    monkeypatch.setattr(config_mod, "DEFAULT_PORT", 0)

    return ConfigManager()


@pytest.fixture
def e2e_port():
    return find_free_port()


@pytest.fixture
def e2e_setup(e2e_config, e2e_dir, e2e_port, monkeypatch):
    """Full setup: dock filesystem server, create identities with policies, return everything needed."""
    test_data = str(e2e_dir["test_data"])

    # Dock the filesystem server
    fs_server = Server(
        name="filesystem",
        command=f"npx -y @modelcontextprotocol/server-filesystem {test_data}",
    )
    e2e_config.add_server(fs_server)

    # Create tokens and identities
    full_token = generate_token()
    read_only_token = generate_token()
    no_policy_token = generate_token()

    for name, token in [
        ("full-access", full_token),
        ("read-only", read_only_token),
        ("no-policy", no_policy_token),
    ]:
        hashed = bcrypt.hashpw(token.encode(), bcrypt.gensalt()).decode()
        keyring.set_password("mcp-harbour", name, hashed)
        e2e_config.add_identity(Identity(name=name, key_prefix=token[:15] + "..."))

    # Full access policy
    full_policy = AgentPolicy(
        identity_name="full-access",
        permissions={"filesystem": [ToolPermission(name="*")]},
    )
    e2e_config.save_policy(full_policy)

    # Read-only policy with path restriction
    read_policy = AgentPolicy(
        identity_name="read-only",
        permissions={
            "filesystem": [
                ToolPermission(
                    name="read_text_file",
                    policies=[
                        ArgumentPolicy(
                            arg_name="path",
                            match_type="glob",
                            pattern=f"{test_data}/readme.txt",
                        )
                    ],
                ),
                ToolPermission(name="list_directory"),
            ]
        },
    )
    e2e_config.save_policy(read_policy)

    # no-policy identity has no policy file → default deny

    return {
        "config": e2e_config,
        "port": e2e_port,
        "test_data": test_data,
        "tokens": {
            "full-access": full_token,
            "read-only": read_only_token,
            "no-policy": no_policy_token,
        },
    }


@pytest_asyncio.fixture
async def e2e_daemon(e2e_setup):
    """Start the gateway daemon on a random port, yield, then shut down."""
    gateway = HarbourGateway()
    port = e2e_setup["port"]

    daemon_task = asyncio.create_task(gateway.serve("127.0.0.1", port))

    # Wait for the daemon to start listening
    for _ in range(50):
        try:
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.close()
            await w.wait_closed()
            break
        except ConnectionRefusedError:
            await asyncio.sleep(0.1)
    else:
        daemon_task.cancel()
        pytest.fail("Daemon did not start in time")

    yield {"port": port, **e2e_setup}

    daemon_task.cancel()
    try:
        await daemon_task
    except (asyncio.CancelledError, Exception):
        pass


# ─── Tests ──────────────────────────────────────────────────────────


class TestE2EHandshake:
    @pytest.mark.asyncio
    async def test_valid_token_connects(self, e2e_daemon):
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            ack = await client.connect(e2e_daemon["tokens"]["full-access"])
            assert ack["status"] == "ok"
            assert ack["identity"] == "full-access"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_invalid_token_rejected(self, e2e_daemon):
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            ack = await client.connect("harbour_sk_bogus_token_that_doesnt_exist")
            assert "error" in ack
            assert ack["error"] == "Invalid token"
        finally:
            await client.close()


class TestE2EInitialize:
    @pytest.mark.asyncio
    async def test_initialize_returns_capabilities(self, e2e_daemon):
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            ack = await client.connect(e2e_daemon["tokens"]["full-access"])
            assert ack["status"] == "ok"

            resp = await client.initialize()
            assert "result" in resp
            assert "capabilities" in resp["result"]
            assert "tools" in resp["result"]["capabilities"]
            assert resp["result"]["serverInfo"]["name"] == "mcp-harbour"
        finally:
            await client.close()


class TestE2EListTools:
    @pytest.mark.asyncio
    async def test_full_access_sees_all_tools(self, e2e_daemon):
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            await client.connect(e2e_daemon["tokens"]["full-access"])
            await client.initialize()

            tools = await client.list_tools()
            tool_names = [t["name"] for t in tools]

            assert len(tools) > 0
            assert "read_file" in tool_names or "read_text_file" in tool_names
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_read_only_sees_filtered_tools(self, e2e_daemon):
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            await client.connect(e2e_daemon["tokens"]["read-only"])
            await client.initialize()

            tools = await client.list_tools()
            tool_names = [t["name"] for t in tools]

            assert "read_text_file" in tool_names
            assert "list_directory" in tool_names
            assert "write_file" not in tool_names
            assert "delete_file" not in tool_names
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_no_policy_sees_no_tools(self, e2e_daemon):
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            await client.connect(e2e_daemon["tokens"]["no-policy"])
            await client.initialize()

            tools = await client.list_tools()
            assert len(tools) == 0
        finally:
            await client.close()


class TestE2ECallTool:
    @pytest.mark.asyncio
    async def test_full_access_can_read_file(self, e2e_daemon):
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            await client.connect(e2e_daemon["tokens"]["full-access"])
            await client.initialize()

            test_data = e2e_daemon["test_data"]
            resp = await client.call_tool("read_text_file", {"path": f"{test_data}/readme.txt"})

            assert "result" in resp
            result_str = json.dumps(resp["result"])
            assert "hello from e2e test" in result_str
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_full_access_can_write_file(self, e2e_daemon):
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            await client.connect(e2e_daemon["tokens"]["full-access"])
            await client.initialize()

            test_data = e2e_daemon["test_data"]
            write_path = f"{test_data}/new_file.txt"
            resp = await client.call_tool("write_file", {
                "path": write_path,
                "content": "written by e2e test",
            })

            assert "result" in resp
            assert Path(write_path).read_text() == "written by e2e test"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_read_only_can_read_allowed_path(self, e2e_daemon):
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            await client.connect(e2e_daemon["tokens"]["read-only"])
            await client.initialize()

            test_data = e2e_daemon["test_data"]
            resp = await client.call_tool("read_text_file", {"path": f"{test_data}/readme.txt"})

            assert "result" in resp
            result_str = json.dumps(resp["result"])
            assert "hello from e2e test" in result_str
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_read_only_denied_on_restricted_path(self, e2e_daemon):
        """Reading a file outside the allowed path pattern should be denied by GPARS policy."""
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            await client.connect(e2e_daemon["tokens"]["read-only"])
            await client.initialize()

            test_data = e2e_daemon["test_data"]
            resp = await client.call_tool("read_text_file", {"path": f"{test_data}/secret.txt"})

            assert "result" in resp
            result = resp["result"]
            assert result.get("isError") is True
            error_text = result["content"][0]["text"]
            assert "does not satisfy policy" in error_text.lower() or "denied" in error_text.lower()
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_read_only_denied_on_write_tool(self, e2e_daemon):
        """Calling a tool not in the policy should be denied."""
        client = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            await client.connect(e2e_daemon["tokens"]["read-only"])
            await client.initialize()

            test_data = e2e_daemon["test_data"]
            resp = await client.call_tool("write_file", {
                "path": f"{test_data}/hack.txt",
                "content": "should not work",
            })

            assert "result" in resp
            result = resp["result"]
            assert result.get("isError") is True
            error_text = result["content"][0]["text"]
            assert "not found" in error_text.lower() or "not allowed" in error_text.lower()
        finally:
            await client.close()


class TestE2EMultipleSessions:
    @pytest.mark.asyncio
    async def test_two_clients_get_isolated_sessions(self, e2e_daemon):
        """Two clients connecting simultaneously should each get their own session."""
        client_a = MCPClient("127.0.0.1", e2e_daemon["port"])
        client_b = MCPClient("127.0.0.1", e2e_daemon["port"])
        try:
            await client_a.connect(e2e_daemon["tokens"]["full-access"])
            await client_b.connect(e2e_daemon["tokens"]["read-only"])

            await client_a.initialize()
            await client_b.initialize()

            tools_a = await client_a.list_tools()
            tools_b = await client_b.list_tools()

            # Full access should see more tools than read-only
            assert len(tools_a) > len(tools_b)

            names_a = {t["name"] for t in tools_a}
            names_b = {t["name"] for t in tools_b}

            assert "write_file" in names_a
            assert "write_file" not in names_b
        finally:
            await client_a.close()
            await client_b.close()
