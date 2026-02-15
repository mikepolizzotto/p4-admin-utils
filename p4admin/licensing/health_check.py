"""
Server Health & License Dashboard.

Gives you a quick snapshot of your Perforce server's health and license status
without digging through `p4 license`, `p4 info`, and `p4 monitor` output.

Answers the questions P4 admins actually ask:
- How many seats am I using vs. my limit?
- When does my license expire?
- Who's consuming seats but hasn't been active?
- What's the server version and do I need to upgrade?
- Who do I call if something breaks?

Usage:
    checker = ServerHealthCheck(p4_conn, config)
    report = checker.analyze()
    render_terminal(report)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from p4admin.core.connection import P4Connection
from p4admin.core.output import ReportData

logger = logging.getLogger(__name__)


@dataclass
class LicenseInfo:
    """Parsed license information."""

    license_type: str = "Unknown"
    user_limit: int | None = None  # None = unlimited
    client_limit: int | None = None
    expiration: datetime | None = None
    ip_address: str = ""
    is_valid: bool = True
    raw_data: dict[str, str] = field(default_factory=dict)

    @property
    def days_until_expiration(self) -> int | None:
        if self.expiration is None:
            return None
        return (self.expiration - datetime.now()).days

    @property
    def is_expiring_soon(self) -> bool:
        """True if license expires within 30 days."""
        days = self.days_until_expiration
        return days is not None and days <= 30

    @property
    def is_expired(self) -> bool:
        days = self.days_until_expiration
        return days is not None and days < 0


@dataclass
class SeatUsage:
    """License seat utilization data."""

    total_users: int = 0
    active_users: int = 0  # Users with activity in last N days
    inactive_users: int = 0
    user_limit: int | None = None
    seats_available: int | None = None
    inactive_user_list: list[dict[str, Any]] = field(default_factory=list)

    @property
    def utilization_pct(self) -> float | None:
        if self.user_limit is None or self.user_limit == 0:
            return None
        return (self.total_users / self.user_limit) * 100

    @property
    def reclaimable_seats(self) -> int:
        return self.inactive_users


class ServerHealthCheck:
    """
    Comprehensive Perforce server health and license check.

    Args:
        conn: Active P4Connection instance.
        config: Configuration dictionary (from load_config).
        inactive_days: Days without activity before a user is "inactive".
    """

    def __init__(
        self,
        conn: P4Connection,
        config: dict[str, Any] | None = None,
        inactive_days: int = 90,
    ):
        self.conn = conn
        self.config = config or {}
        self.inactive_days = inactive_days
        self._licensing_config = self.config.get("licensing", {})

    def analyze(self) -> ReportData:
        """Run all health checks and produce a report."""
        logger.info("Running server health check...")

        server_info = self.conn.server_info
        license_info = self._get_license_info()
        seat_usage = self._get_seat_usage(license_info)
        server_version = self._parse_server_version(server_info)

        report = ReportData(
            title="Server Health & License Dashboard",
            server_info=server_info,
        )

        # Summary
        report.summary = {
            "Server": server_info.get("serverAddress", "N/A"),
            "Version": server_version.get("version_string", "N/A"),
            "License type": license_info.license_type,
            "License status": self._license_status_text(license_info),
            "Seat utilization": self._seat_utilization_text(seat_usage),
        }

        # License details section
        license_stats = {
            "License type": license_info.license_type,
            "User limit": str(license_info.user_limit) if license_info.user_limit else "Unlimited",
            "Client limit": str(license_info.client_limit) if license_info.client_limit else "Unlimited",
        }

        if license_info.expiration:
            days_left = license_info.days_until_expiration
            exp_str = license_info.expiration.strftime("%Y-%m-%d")
            if license_info.is_expired:
                license_stats["Expiration"] = f"EXPIRED ({exp_str})"
            elif license_info.is_expiring_soon:
                license_stats["Expiration WARNING"] = f"{exp_str} ({days_left} days remaining)"
            else:
                license_stats["Expiration"] = f"{exp_str} ({days_left} days remaining)"
        else:
            license_stats["Expiration"] = "No expiration set"

        report.add_section(title="License Details", stats=license_stats)

        # Seat usage section
        seat_stats = {
            "Total users": str(seat_usage.total_users),
            "Active users (last {0} days)".format(self.inactive_days): str(seat_usage.active_users),
            "Inactive users": str(seat_usage.inactive_users),
        }
        if seat_usage.user_limit:
            seat_stats["Seats available"] = str(seat_usage.seats_available)
            seat_stats["Utilization"] = f"{seat_usage.utilization_pct:.1f}%"
            seat_stats["Reclaimable seats"] = str(seat_usage.reclaimable_seats)

        seat_notes = []
        if seat_usage.reclaimable_seats > 0:
            seat_notes.append(
                f"Removing {seat_usage.reclaimable_seats} inactive users would "
                f"free seats for new team members."
            )

        seat_section = report.add_section(
            title="Seat Usage",
            stats=seat_stats,
            notes=seat_notes,
        )

        # Top inactive users table
        if seat_usage.inactive_user_list:
            top_inactive = sorted(
                seat_usage.inactive_user_list,
                key=lambda u: u.get("days_inactive", 0),
                reverse=True,
            )[:20]  # Top 20

            report.add_section(
                title="Inactive Users (longest idle first)",
                headers=["User", "Full Name", "Email", "Last Access", "Days Inactive"],
                rows=[
                    [
                        u["user"],
                        u.get("full_name", ""),
                        u.get("email", ""),
                        u.get("last_access", "Never"),
                        str(u.get("days_inactive", "N/A")),
                    ]
                    for u in top_inactive
                ],
                notes=[f"Showing top 20 of {len(seat_usage.inactive_user_list)} inactive users."],
            )

        # Server info section
        report.add_section(
            title="Server Details",
            stats={
                "Server address": server_info.get("serverAddress", "N/A"),
                "Server root": server_info.get("serverRoot", "N/A"),
                "Server version": server_version.get("version_string", "N/A"),
                "Server uptime": server_info.get("serverUptime", "N/A"),
                "Case handling": server_info.get("caseHandling", "N/A"),
                "Unicode enabled": server_info.get("unicode", "N/A"),
            },
        )

        # Support contacts section
        support_stats = {}
        if self._licensing_config.get("support_email"):
            support_stats["Internal IT contact"] = self._licensing_config["support_email"]
        if self._licensing_config.get("perforce_rep"):
            support_stats["Perforce account rep"] = self._licensing_config["perforce_rep"]
        if self._licensing_config.get("portal_url"):
            support_stats["Support portal"] = self._licensing_config["portal_url"]
        if self._licensing_config.get("license_renewal_url"):
            support_stats["License renewal"] = self._licensing_config["license_renewal_url"]

        if support_stats:
            report.add_section(
                title="Support & Quick Links",
                stats=support_stats,
            )

        return report

    def _get_license_info(self) -> LicenseInfo:
        """Parse license information from the server."""
        info = LicenseInfo()

        try:
            license_data, errors = self.conn.run_safe("license", "-o")
            if errors:
                logger.warning("Could not retrieve license info: %s", errors)
                info.is_valid = False
                return info

            if license_data:
                data = license_data[0] if isinstance(license_data, list) else license_data
                info.raw_data = data

                info.license_type = data.get("License", "Unknown")

                # Parse user limit
                user_limit = data.get("Users", data.get("userLimit"))
                if user_limit and str(user_limit).lower() != "unlimited":
                    try:
                        info.user_limit = int(user_limit)
                    except (ValueError, TypeError):
                        pass

                # Parse client limit
                client_limit = data.get("Clients", data.get("clientLimit"))
                if client_limit and str(client_limit).lower() != "unlimited":
                    try:
                        info.client_limit = int(client_limit)
                    except (ValueError, TypeError):
                        pass

                # Parse expiration
                exp_str = data.get("Expiration", data.get("expDate"))
                if exp_str:
                    info.expiration = self._parse_date(exp_str)

                info.ip_address = data.get("IP", "")

        except Exception as e:
            logger.error("Error getting license info: %s", e)
            info.is_valid = False

        return info

    def _get_seat_usage(self, license_info: LicenseInfo) -> SeatUsage:
        """Analyze seat usage across all users."""
        usage = SeatUsage(user_limit=license_info.user_limit)
        cutoff = datetime.now() - timedelta(days=self.inactive_days)

        try:
            users = self.conn.run("users")
            usage.total_users = len(users)

            for user in users:
                username = user.get("User", "")
                email = user.get("Email", "")
                full_name = user.get("FullName", "")
                access_str = user.get("Access")

                if access_str:
                    try:
                        last_access = datetime.fromtimestamp(int(access_str))
                        if last_access >= cutoff:
                            usage.active_users += 1
                        else:
                            days_inactive = (datetime.now() - last_access).days
                            usage.inactive_users += 1
                            usage.inactive_user_list.append({
                                "user": username,
                                "full_name": full_name,
                                "email": email,
                                "last_access": last_access.strftime("%Y-%m-%d"),
                                "days_inactive": days_inactive,
                            })
                    except (ValueError, TypeError):
                        # Can't parse access time — count as inactive
                        usage.inactive_users += 1
                        usage.inactive_user_list.append({
                            "user": username,
                            "full_name": full_name,
                            "email": email,
                            "last_access": "Unknown",
                            "days_inactive": "N/A",
                        })
                else:
                    # No access time recorded
                    usage.inactive_users += 1
                    usage.inactive_user_list.append({
                        "user": username,
                        "full_name": full_name,
                        "email": email,
                        "last_access": "Never",
                        "days_inactive": "N/A",
                    })

            if license_info.user_limit:
                usage.seats_available = license_info.user_limit - usage.total_users

        except Exception as e:
            logger.error("Error getting seat usage: %s", e)

        return usage

    def _parse_server_version(self, server_info: dict[str, Any]) -> dict[str, str]:
        """Parse the server version string into components."""
        version_str = server_info.get("serverVersion", "")
        result = {"version_string": version_str}

        # P4 version string format: P4D/LINUX26X86_64/2024.1/2596294
        parts = version_str.split("/")
        if len(parts) >= 3:
            result["platform"] = parts[1] if len(parts) > 1 else ""
            result["release"] = parts[2] if len(parts) > 2 else ""
            result["build"] = parts[3] if len(parts) > 3 else ""

        return result

    def _license_status_text(self, info: LicenseInfo) -> str:
        """Human-readable license status."""
        if not info.is_valid:
            return "Unable to read license"
        if info.is_expired:
            return "EXPIRED"
        if info.is_expiring_soon:
            return f"EXPIRING SOON ({info.days_until_expiration} days)"
        if info.expiration:
            return f"OK (expires in {info.days_until_expiration} days)"
        return "OK"

    def _seat_utilization_text(self, usage: SeatUsage) -> str:
        """Human-readable seat utilization."""
        if usage.user_limit is None:
            return f"{usage.total_users} users (unlimited seats)"
        return (
            f"{usage.total_users}/{usage.user_limit} seats "
            f"({usage.utilization_pct:.0f}% used, "
            f"{usage.reclaimable_seats} reclaimable)"
        )

    @staticmethod
    def _parse_date(date_str: str) -> datetime | None:
        """Try multiple date formats common in P4 output."""
        formats = [
            "%Y/%m/%d",
            "%Y/%m/%d %H:%M:%S",
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M:%S",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except (ValueError, TypeError):
                continue

        # Try epoch timestamp
        try:
            return datetime.fromtimestamp(int(date_str))
        except (ValueError, TypeError):
            logger.warning("Could not parse date: %s", date_str)
            return None
