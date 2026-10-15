"""Vercel function entrypoint for the MCP server."""

from mcp.main import app

__all__ = ["app"]
