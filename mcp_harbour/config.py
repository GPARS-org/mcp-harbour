import os
import sys
import json
from pathlib import Path
from typing import Optional, List
from .models import Config, Server, Identity, AgentPolicy


def _get_config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "mcp-harbour"
    return Path.home() / ".mcp-harbour"


CONFIG_DIR = _get_config_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"
POLICIES_DIR = CONFIG_DIR / "policies"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4767


class ConfigManager:
    def __init__(self):
        self._ensure_dirs()
        self.config = self._load_config()

    def _ensure_dirs(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        POLICIES_DIR.mkdir(parents=True, exist_ok=True)

    def _load_config(self) -> Config:
        if not CONFIG_FILE.exists():
            return Config()
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            return Config(**data)
        except Exception as e:
            print(f"Warning: Could not load config: {e}")
            return Config()

    def save_config(self):
        with open(CONFIG_FILE, "w") as f:
            f.write(self.config.model_dump_json(indent=2))

    def reload(self):
        self.config = self._load_config()

    # --- Server Management ---
    def add_server(self, server: Server):
        self.config.servers[server.name] = server
        self.save_config()

    def remove_server(self, name: str):
        if name in self.config.servers:
            del self.config.servers[name]
            self.save_config()

    def get_server(self, name: str) -> Optional[Server]:
        return self.config.servers.get(name)

    def list_servers(self) -> List[Server]:
        return list(self.config.servers.values())

    # --- Identity Management ---
    def add_identity(self, identity: Identity):
        self.config.identities[identity.name] = identity
        self.save_config()

    def get_identity(self, name: str) -> Optional[Identity]:
        return self.config.identities.get(name)

    def remove_identity(self, name: str):
        if name in self.config.identities:
            del self.config.identities[name]
            self.save_config()
            policy_path = self._get_policy_path(name)
            if policy_path.exists():
                try:
                    policy_path.unlink()
                except OSError:
                    pass

    def list_identities(self) -> list:
        return list(self.config.identities.values())

    # --- Policy Management ---
    def _get_policy_path(self, identity_name: str) -> Path:
        return POLICIES_DIR / f"{identity_name}.json"

    def create_policy(self, identity_name: str) -> AgentPolicy:
        policy = AgentPolicy(identity_name=identity_name, permissions={})
        self.save_policy(policy)
        return policy

    def save_policy(self, policy: AgentPolicy):
        path = self._get_policy_path(policy.identity_name)
        with open(path, "w") as f:
            f.write(policy.model_dump_json(indent=2))

    def load_policy(self, identity_name: str) -> Optional[AgentPolicy]:
        path = self._get_policy_path(identity_name)
        if not path.exists():
            return None
        try:
            with open(path, "r") as f:
                data = json.load(f)
            return AgentPolicy(**data)
        except Exception as e:
            print(f"Error loading policy for {identity_name}: {e}")
            return None
