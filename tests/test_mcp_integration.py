"""
Integration tests for the full MCP protocol flow through HarbourGateway.

Tests the complete chain: handshake → initialize → list_tools → call_tool,
verifying that GPARS error codes are returned correctly and that the MCP
session lifecycle works end-to-end.
"""

import pytest
import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from mcp.types import Tool, ListToolsResult, CallToolResult, TextContent
from mcp.shared.exceptions import McpError

from mcp_harbour.models import (
    Server,
    ServerType,
    Identity,
    AgentPolicy,
    ToolPermission,
    ArgumentPolicy,
)
from mcp_harbour.process_manager import ServerProcess, HarbourDaemon
from mcp_harbour.gateway import HarbourGateway
from mcp_harbour.errors import AUTHORIZATION_DENIED_CODE, SERVER_UNAVAILABLE_CODE


# ─── Helpers ────────────────────────────────────────────────────────


def make_mock_process(server_name: str, tool_names: list[str]) -> ServerProcess:
    proc = MagicMock(spec=ServerProcess)
    proc.server_config = Server(name=server_name, command="echo")
    proc.session = MagicMock()

    mock_tools = []
    for name in tool_names:
        tool = Tool(
            name=name,
            description=f"Mock {name}",
            inputSchema={"type": "object", "properties": {}},
        )
        mock_tools.append(tool)

    proc.list_tools = AsyncMock(return_value=ListToolsResult(tools=mock_tools))

    call_result = CallToolResult(
        content=[TextContent(type="text", text=f"result from {server_name}")]
    )
    proc.call_tool = AsyncMock(return_value=call_result)
    proc.stop = AsyncMock()

    return proc


def make_jsonrpc_request(method: str, params: dict = None, req_id: int = 1) -> bytes:
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg).encode() + b"\n"


def parse_jsonrpc_response(data: bytes) -> dict:
    for line in data.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        return json.loads(line)
    return None


class MockStream:
    """Simulates an AnyIO byte stream for testing the gateway handler."""

    def __init__(self):
        self._inbox = asyncio.Queue()
        self._outbox = asyncio.Queue()
        self._closed = False

    async def send(self, data: bytes):
        await self._outbox.put(data)

    async def receive(self, max_bytes: int = 4096) -> bytes:
        data = await self._inbox.get()
        if data is None:
            raise Exception("Stream closed")
        return data

    async def inject(self, data: bytes):
        """Inject data as if the client sent it."""
        await self._inbox.put(data)

    async def read_response(self, timeout: float = 2.0) -> bytes:
        """Read a response from the gateway."""
        return await asyncio.wait_for(self._outbox.get(), timeout=timeout)

    async def aclose(self):
        self._closed = True


# ─── Handshake Tests ────────────────────────────────────────────────


class TestHandshake:
    @pytest.mark.asyncio
    async def test_valid_token_returns_ok(self, config_manager):
        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()
        gateway._resolve_identity_from_token = MagicMock(return_value="test-agent")

        identity = Identity(name="test-agent", key_prefix="harbour_sk_test...")
        config_manager.add_identity(identity)

        stream = MockStream()
        await stream.inject(b'{"auth": "harbour_sk_testtoken"}\n')

        task = asyncio.create_task(gateway._handle_connection(stream))
        response = await stream.read_response()

        ack = json.loads(response.decode())
        assert ack["status"] == "ok"
        assert ack["identity"] == "test-agent"

        # Cancel the task (it's waiting for MCP traffic)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_invalid_token_returns_error(self, config_manager):
        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()
        gateway._resolve_identity_from_token = MagicMock(return_value=None)

        stream = MockStream()
        await stream.inject(b'{"auth": "bad_token"}\n')

        await gateway._handle_connection(stream)

        response = await stream.read_response()
        ack = json.loads(response.decode())
        assert "error" in ack
        assert ack["error"] == "Invalid token"

    @pytest.mark.asyncio
    async def test_missing_token_returns_error(self, config_manager):
        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        stream = MockStream()
        await stream.inject(b'{"something": "else"}\n')

        await gateway._handle_connection(stream)

        response = await stream.read_response()
        ack = json.loads(response.decode())
        assert ack["error"] == "Missing auth token"

    @pytest.mark.asyncio
    async def test_invalid_json_returns_error(self, config_manager):
        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        stream = MockStream()
        await stream.inject(b'not json at all\n')

        await gateway._handle_connection(stream)

        response = await stream.read_response()
        ack = json.loads(response.decode())
        assert "error" in ack

    @pytest.mark.asyncio
    async def test_no_newline_returns_error(self, config_manager):
        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        stream = MockStream()
        await stream.inject(b'{"auth": "token"}')  # no newline

        await gateway._handle_connection(stream)

        response = await stream.read_response()
        ack = json.loads(response.decode())
        assert "error" in ack


# ─── Session Tool Discovery Tests ───────────────────────────────────


class TestSessionToolDiscovery:
    @pytest.mark.asyncio
    async def test_session_discovers_tools_from_single_server(self, config_manager):
        server = Server(name="filesystem", command="echo")
        config_manager.add_server(server)

        policy = AgentPolicy(
            identity_name="agent",
            permissions={"filesystem": [ToolPermission(name="*")]},
        )
        config_manager.save_policy(policy)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        mock_proc = make_mock_process("filesystem", ["read_file", "write_file", "list_dir"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        session_server, owned = await gateway.create_session("agent")

        from mcp.types import ListToolsRequest
        result = await session_server.request_handlers[ListToolsRequest](MagicMock())
        tool_names = [t.name for t in result.root.tools]

        assert len(tool_names) == 3
        assert "read_file" in tool_names
        assert "write_file" in tool_names
        assert "list_dir" in tool_names

    @pytest.mark.asyncio
    async def test_session_discovers_tools_from_multiple_servers(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))
        config_manager.add_server(Server(name="git", command="echo"))

        policy = AgentPolicy(
            identity_name="agent",
            permissions={
                "filesystem": [ToolPermission(name="*")],
                "git": [ToolPermission(name="*")],
            },
        )
        config_manager.save_policy(policy)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        fs_proc = make_mock_process("filesystem", ["read_file", "write_file"])
        git_proc = make_mock_process("git", ["git_status", "git_log"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(side_effect=[fs_proc, git_proc])

        session_server, owned = await gateway.create_session("agent")

        from mcp.types import ListToolsRequest
        result = await session_server.request_handlers[ListToolsRequest](MagicMock())
        tool_names = [t.name for t in result.root.tools]

        assert len(tool_names) == 4
        assert set(tool_names) == {"read_file", "write_file", "git_status", "git_log"}

    @pytest.mark.asyncio
    async def test_no_policy_returns_no_tools(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()
        gateway.daemon.spawn_stdio_instance = AsyncMock()

        session_server, owned = await gateway.create_session("no-policy-agent")

        from mcp.types import ListToolsRequest
        result = await session_server.request_handlers[ListToolsRequest](MagicMock())

        assert len(result.root.tools) == 0
        gateway.daemon.spawn_stdio_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_policy_filters_tools_by_glob(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))

        policy = AgentPolicy(
            identity_name="agent",
            permissions={"filesystem": [ToolPermission(name="read_*")]},
        )
        config_manager.save_policy(policy)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        mock_proc = make_mock_process("filesystem", ["read_file", "read_dir", "write_file", "delete_file"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        session_server, owned = await gateway.create_session("agent")

        from mcp.types import ListToolsRequest
        result = await session_server.request_handlers[ListToolsRequest](MagicMock())
        tool_names = [t.name for t in result.root.tools]

        assert set(tool_names) == {"read_file", "read_dir"}

    @pytest.mark.asyncio
    async def test_policy_filters_servers(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))
        config_manager.add_server(Server(name="bash", command="echo"))

        policy = AgentPolicy(
            identity_name="agent",
            permissions={"filesystem": [ToolPermission(name="*")]},
            # bash not in policy
        )
        config_manager.save_policy(policy)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        fs_proc = make_mock_process("filesystem", ["read_file"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=fs_proc)

        session_server, owned = await gateway.create_session("agent")

        # Only 1 spawn call (filesystem), not 2 (bash was skipped)
        assert gateway.daemon.spawn_stdio_instance.call_count == 1


# ─── Tool Call Tests ────────────────────────────────────────────────


class TestSessionToolCalls:
    @pytest.mark.asyncio
    async def test_call_tool_routes_to_correct_server(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))
        config_manager.add_server(Server(name="git", command="echo"))

        policy = AgentPolicy(
            identity_name="agent",
            permissions={
                "filesystem": [ToolPermission(name="*")],
                "git": [ToolPermission(name="*")],
            },
        )
        config_manager.save_policy(policy)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        fs_proc = make_mock_process("filesystem", ["read_file"])
        git_proc = make_mock_process("git", ["git_status"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(side_effect=[fs_proc, git_proc])

        session_server, owned = await gateway.create_session("agent")

        from mcp.types import CallToolRequest, CallToolRequestParams

        # Call filesystem tool
        handler = session_server.request_handlers[CallToolRequest]
        request = MagicMock()
        request.params = CallToolRequestParams(name="read_file", arguments={"path": "/tmp/test"})
        await handler(request)
        fs_proc.call_tool.assert_called_once_with("read_file", {"path": "/tmp/test"})
        git_proc.call_tool.assert_not_called()

        # Call git tool
        request2 = MagicMock()
        request2.params = CallToolRequestParams(name="git_status", arguments={})
        await handler(request2)
        git_proc.call_tool.assert_called_once_with("git_status", {})

    @pytest.mark.asyncio
    async def test_call_tool_with_argument_policy_allowed(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))

        policy = AgentPolicy(
            identity_name="agent",
            permissions={
                "filesystem": [
                    ToolPermission(
                        name="read_file",
                        policies=[
                            ArgumentPolicy(arg_name="path", match_type="glob", pattern="/home/user/**")
                        ],
                    )
                ]
            },
        )
        config_manager.save_policy(policy)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        mock_proc = make_mock_process("filesystem", ["read_file"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        session_server, owned = await gateway.create_session("agent")

        from mcp.types import CallToolRequest, CallToolRequestParams
        handler = session_server.request_handlers[CallToolRequest]
        request = MagicMock()
        request.params = CallToolRequestParams(
            name="read_file", arguments={"path": "/home/user/project/main.py"}
        )
        result = await handler(request)
        mock_proc.call_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_call_tool_with_argument_policy_denied(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))

        policy = AgentPolicy(
            identity_name="agent",
            permissions={
                "filesystem": [
                    ToolPermission(
                        name="read_file",
                        policies=[
                            ArgumentPolicy(arg_name="path", match_type="glob", pattern="/home/user/**")
                        ],
                    )
                ]
            },
        )
        config_manager.save_policy(policy)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        mock_proc = make_mock_process("filesystem", ["read_file"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        session_server, owned = await gateway.create_session("agent")

        from mcp.types import CallToolRequest, CallToolRequestParams
        handler = session_server.request_handlers[CallToolRequest]
        request = MagicMock()
        request.params = CallToolRequestParams(
            name="read_file", arguments={"path": "/etc/shadow"}
        )
        result = await handler(request)

        # SDK wraps McpError into CallToolResult with isError=True
        call_result = result.root
        assert call_result.isError is True
        mock_proc.call_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_call_unknown_tool_returns_authorization_denied(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))

        policy = AgentPolicy(
            identity_name="agent",
            permissions={"filesystem": [ToolPermission(name="read_file")]},
        )
        config_manager.save_policy(policy)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        mock_proc = make_mock_process("filesystem", ["read_file"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        session_server, owned = await gateway.create_session("agent")

        from mcp.types import CallToolRequest, CallToolRequestParams
        handler = session_server.request_handlers[CallToolRequest]
        request = MagicMock()
        request.params = CallToolRequestParams(name="nonexistent_tool", arguments={})
        result = await handler(request)

        call_result = result.root
        assert call_result.isError is True
        mock_proc.call_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_call_tool_on_unavailable_server(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))

        policy = AgentPolicy(
            identity_name="agent",
            permissions={"filesystem": [ToolPermission(name="*")]},
        )
        config_manager.save_policy(policy)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        mock_proc = make_mock_process("filesystem", ["read_file"])
        # Simulate server going down after tool cache was built
        mock_proc.session = None
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        session_server, owned = await gateway.create_session("agent")

        from mcp.types import CallToolRequest, CallToolRequestParams
        handler = session_server.request_handlers[CallToolRequest]
        request = MagicMock()
        request.params = CallToolRequestParams(name="read_file", arguments={})
        result = await handler(request)

        call_result = result.root
        assert call_result.isError is True


# ─── Default Deny Tests ─────────────────────────────────────────────


class TestDefaultDeny:
    @pytest.mark.asyncio
    async def test_identity_with_no_policy_gets_no_tools(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()
        gateway.daemon.spawn_stdio_instance = AsyncMock()

        session_server, owned = await gateway.create_session("unknown-agent")

        from mcp.types import ListToolsRequest
        result = await session_server.request_handlers[ListToolsRequest](MagicMock())

        assert len(result.root.tools) == 0

    @pytest.mark.asyncio
    async def test_identity_with_empty_policy_gets_no_tools(self, config_manager):
        config_manager.add_server(Server(name="filesystem", command="echo"))

        policy = AgentPolicy(identity_name="empty-agent", permissions={})
        config_manager.save_policy(policy)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()
        gateway.daemon.spawn_stdio_instance = AsyncMock()

        session_server, owned = await gateway.create_session("empty-agent")

        from mcp.types import ListToolsRequest
        result = await session_server.request_handlers[ListToolsRequest](MagicMock())

        assert len(result.root.tools) == 0
        gateway.daemon.spawn_stdio_instance.assert_not_called()


# ─── Remainder Handling Test ─────────────────────────────────────────


class TestRemainderHandling:
    @pytest.mark.asyncio
    async def test_data_after_handshake_newline_is_not_lost(self, config_manager):
        """
        If the client sends the handshake and the first MCP message in the
        same TCP chunk, the data after the newline must not be dropped.
        """
        identity = Identity(name="test-agent", key_prefix="harbour_sk_test...")
        config_manager.add_identity(identity)

        policy = AgentPolicy(
            identity_name="test-agent",
            permissions={"filesystem": [ToolPermission(name="*")]},
        )
        config_manager.save_policy(policy)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()
        gateway._resolve_identity_from_token = MagicMock(return_value="test-agent")

        mock_proc = make_mock_process("filesystem", ["read_file"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        # Send handshake + MCP initialize in same chunk
        initialize_msg = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1"}
            }
        })
        combined = b'{"auth": "harbour_sk_testtoken"}\n' + initialize_msg.encode() + b'\n'

        stream = MockStream()
        await stream.inject(combined)

        task = asyncio.create_task(gateway._handle_connection(stream))

        # Should get ACK first
        ack_data = await stream.read_response(timeout=3.0)
        ack = json.loads(ack_data.decode())
        assert ack["status"] == "ok"

        # Should get initialize response (not hang)
        try:
            response_data = await asyncio.wait_for(stream.read_response(), timeout=3.0)
            response = json.loads(response_data.decode())
            # The initialize response should have a result with server info
            assert "result" in response or "error" in response
        except asyncio.TimeoutError:
            pytest.fail("Gateway did not respond to initialize — remainder was likely dropped")
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
