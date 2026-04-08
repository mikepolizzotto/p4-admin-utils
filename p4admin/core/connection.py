"""
P4 server connection management.

Provides a clean wrapper around p4python with connection pooling,
error handling, and context manager support. Designed to work with
both direct connections and environment-based P4 configuration.

Usage:
    # From environment (P4PORT, P4USER, P4CLIENT, etc.)
    with P4Connection() as p4:
        clients = p4.run("clients")

    # Explicit configuration
    with P4Connection(port="ssl:perforce:1666", user="admin") as p4:
        info = p4.run("info")

    # From a config file
    config = load_config("server.yaml")
    with P4Connection.from_config(config) as p4:
        users = p4.run("users")
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from P4 import P4, P4Exception

logger = logging.getLogger(__name__)


class P4ConnectionError(Exception):
    """Raised when a P4 connection cannot be established."""

    pass


class P4CommandError(Exception):
    """Raised when a P4 command fails."""

    def __init__(self, command: str, errors: list[str]):
        self.command = command
        self.errors = errors
        super().__init__(f"P4 command '{command}' failed: {'; '.join(errors)}")


@dataclass
class P4Connection:
    """
    Manages a connection to a Perforce server.

    Wraps p4python with sensible defaults, error handling, and
    context manager support. Reads from P4 environment variables
    by default, but accepts explicit overrides.

    Attributes:
        port: P4PORT — server address (e.g., "ssl:perforce:1666")
        user: P4USER — username for authentication
        client: P4CLIENT — workspace/client name (optional)
        charset: Character set for unicode servers (default: "utf8")
        password: P4PASSWD — password/ticket (optional, prefers env or ticket file)
    """

    port: str | None = None
    user: str | None = None
    client: str | None = None
    charset: str = "utf8"
    password: str | None = None
    _p4: P4 = field(default=None, init=False, repr=False)
    _connected: bool = field(default=False, init=False, repr=False)
    _server_info_cache: dict[str, Any] | None = field(default=None, init=False, repr=False)

    def __post_init__(self):
        self._p4 = P4()

        # Apply explicit settings, fall back to environment
        if self.port:
            self._p4.port = self.port
        if self.user:
            self._p4.user = self.user
        if self.client:
            self._p4.client = self.client
        if self.charset:
            self._p4.charset = self.charset
        if self.password:
            self._p4.password = self.password

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> P4Connection:
        """
        Create a connection from a configuration dictionary.

        Expected keys (all optional — falls back to environment):
            server:
                port: "ssl:perforce:1666"
                user: "admin"
                client: "admin-workspace"
                charset: "utf8"
        """
        server_config = config.get("server", {})
        return cls(
            port=server_config.get("port"),
            user=server_config.get("user"),
            client=server_config.get("client"),
            charset=server_config.get("charset", "utf8"),
        )

    def connect(self) -> P4:
        """Establish connection to the P4 server."""
        if self._connected:
            return self._p4

        try:
            self._p4.connect()
            self._connected = True
            server_info = self._p4.run("info")[0]
            logger.info(
                "Connected to %s as %s (server version: %s)",
                server_info.get("serverAddress", "unknown"),
                server_info.get("userName", "unknown"),
                server_info.get("serverVersion", "unknown"),
            )
            return self._p4
        except P4Exception as e:
            raise P4ConnectionError(
                f"Failed to connect to Perforce server: {e}"
            ) from e

    def disconnect(self):
        """Disconnect from the P4 server."""
        if self._connected:
            try:
                self._p4.disconnect()
            except P4Exception:
                pass  # Don't raise on disconnect failures
            finally:
                self._connected = False

    def run(self, *args, **kwargs) -> list[dict[str, Any]]:
        """
        Run a P4 command with error handling.

        Wraps p4.run() with consistent error handling and logging.
        Automatically connects if not already connected.

        Args:
            *args: Command and arguments (e.g., "clients", "-u", "admin")
            **kwargs: Keyword arguments. Use input=<dict|str> to pass spec
                      data for -i commands. Other kwargs passed to p4.run().

        Returns:
            List of result dictionaries from the P4 command.

        Raises:
            P4CommandError: If the command produces errors.
            P4ConnectionError: If not connected and connection fails.
        """
        if not self._connected:
            self.connect()

        # Handle input= kwarg for spec-based commands (p4python requires
        # setting p4.input as an attribute, not as a run() keyword arg)
        input_data = kwargs.pop("input", None)
        if input_data is not None:
            self._p4.input = input_data

        cmd_str = " ".join(str(a) for a in args)
        logger.debug("Running: p4 %s", cmd_str)

        try:
            results = self._p4.run(*args, **kwargs)

            # Check for warnings (non-fatal)
            if self._p4.warnings:
                for warning in self._p4.warnings:
                    logger.warning("P4 warning: %s", warning)

            # Check for errors
            if self._p4.errors:
                raise P4CommandError(cmd_str, self._p4.errors)

            return results

        except P4Exception as e:
            raise P4CommandError(cmd_str, [str(e)]) from e

    def run_safe(self, *args, **kwargs) -> tuple[list[dict[str, Any]], list[str]]:
        """
        Run a P4 command, returning results and errors instead of raising.

        Useful for commands where partial failure is expected
        (e.g., querying workspaces that may have been deleted).

        Returns:
            Tuple of (results, errors)
        """
        if not self._connected:
            self.connect()

        # Handle input= kwarg (same as run())
        input_data = kwargs.pop("input", None)
        if input_data is not None:
            self._p4.input = input_data

        cmd_str = " ".join(str(a) for a in args)
        logger.debug("Running (safe): p4 %s", cmd_str)

        try:
            results = self._p4.run(*args, **kwargs)
            errors = list(self._p4.errors) if self._p4.errors else []
            return results, errors
        except P4Exception as e:
            return [], [str(e)]

    @property
    def server_info(self) -> dict[str, Any]:
        """Get current server info (cached after first call)."""
        if self._server_info_cache is not None:
            return self._server_info_cache
        if not self._connected:
            self.connect()
        self._server_info_cache = self._p4.run("info")[0]
        return self._server_info_cache

    @property
    def is_connected(self) -> bool:
        """Check if currently connected."""
        return self._connected

    def __enter__(self) -> P4Connection:
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False
