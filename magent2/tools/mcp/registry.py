from __future__ import annotations

from .config import MCPServerConfig, load_agent_mcp_configs
from .gateway import MCPToolGateway


def load_for_agent(agent_name: str) -> MCPToolGateway | None:
    """Load MCP servers for the given agent and start a gateway.

    Returns None if no servers are configured.
    """
    configs = load_agent_mcp_configs(agent_name)
    if not configs:
        return None
    gateway = MCPToolGateway(configs)
    gateway.start()
    return gateway


__all__ = ["MCPServerConfig", "load_agent_mcp_configs", "MCPToolGateway", "load_for_agent"]

