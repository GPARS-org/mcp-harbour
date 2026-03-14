import pytest

from mcp_harbour.models import (
    Server,
    ServerType,
    Identity,
    AgentPolicy,
    ToolPermission,
    ArgumentPolicy,
)


@pytest.fixture
def tmp_config_dir(tmp_path):
    config_dir = tmp_path / ".mcp-harbour"
    config_dir.mkdir()
    (config_dir / "policies").mkdir()
    return config_dir


@pytest.fixture
def config_manager(tmp_config_dir, monkeypatch):
    import mcp_harbour.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_config_dir)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_config_dir / "config.json")
    monkeypatch.setattr(config_mod, "POLICIES_DIR", tmp_config_dir / "policies")
    monkeypatch.setattr(config_mod, "DEFAULT_HOST", "127.0.0.1")
    monkeypatch.setattr(config_mod, "DEFAULT_PORT", 0)

    from mcp_harbour.config import ConfigManager

    return ConfigManager()


@pytest.fixture
def sample_server():
    return Server(
        name="filesystem",
        command="npx -y @modelcontextprotocol/server-filesystem",
        server_type=ServerType.stdio,
    )


@pytest.fixture
def sample_http_server():
    return Server(
        name="web-search",
        command="http://localhost:3001/mcp",
        server_type=ServerType.http,
    )


@pytest.fixture
def sample_identity():
    return Identity(name="test-agent", key_prefix="harbour_sk_test")


@pytest.fixture
def restrictive_policy():
    return AgentPolicy(
        identity_name="test-agent",
        permissions={
            "filesystem": [
                ToolPermission(
                    name="read_file",
                    policies=[
                        ArgumentPolicy(
                            arg_name="path",
                            match_type="glob",
                            pattern="/home/user/public/**",
                        )
                    ],
                )
            ]
        },
    )


@pytest.fixture
def wildcard_policy():
    return AgentPolicy(
        identity_name="admin-agent",
        permissions={"filesystem": [ToolPermission(name="*", policies=[])]},
    )


@pytest.fixture
def multi_server_policy():
    return AgentPolicy(
        identity_name="multi-agent",
        permissions={
            "filesystem": [
                ToolPermission(name="read_*", policies=[]),
            ],
            "git": [
                ToolPermission(name="git_status", policies=[]),
                ToolPermission(name="git_log", policies=[]),
            ],
        },
    )
