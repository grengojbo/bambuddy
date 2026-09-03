"""Configuration loader for SpoolBuddy daemon.

All configuration is via environment variables. The systemd service file
or a shell wrapper sets these before launching the daemon.

Required:
    SPOOLBUDDY_BACKEND_URL  — Bambuddy server URL (e.g. http://192.168.1.100:5000)
    SPOOLBUDDY_API_KEY      — API key created in Bambuddy Settings → API Keys

Optional:
    SPOOLBUDDY_DEVICE_ID    — Unique device identifier (default: derived from MAC)
    SPOOLBUDDY_HOSTNAME     — Display name (default: system hostname)
    SPOOLBUDDY_NFC_DRIVER   — NFC frontend: pn5180 (default) or mfrc522
    SPOOLBUDDY_SCALE_DRIVER — Load cell ADC: nau7802 (default) or hx711
"""

import os
import socket
from dataclasses import dataclass
from pathlib import Path

NFC_DRIVERS = ("pn5180", "mfrc522")
SCALE_DRIVERS = ("nau7802", "hx711")


def _driver(env_var: str, default: str, allowed: tuple[str, ...]) -> str:
    value = os.environ.get(env_var, "").strip().lower()
    if not value:
        return default
    if value not in allowed:
        raise RuntimeError(f"{env_var}={value!r} is not supported (expected one of: {', '.join(allowed)})")
    return value


@dataclass
class Config:
    backend_url: str = ""
    api_key: str = ""
    device_id: str = ""
    hostname: str = ""

    nfc_poll_interval: float = 0.3
    scale_read_interval: float = 0.1
    scale_report_interval: float = 1.0
    heartbeat_interval: float = 10.0
    stability_threshold: float = 2.0
    stability_window: float = 1.0

    tare_offset: int = 0
    calibration_factor: float = 1.0

    nfc_driver: str = "pn5180"
    scale_driver: str = "nau7802"

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()

        cfg.backend_url = os.environ.get("SPOOLBUDDY_BACKEND_URL", "")
        cfg.api_key = os.environ.get("SPOOLBUDDY_API_KEY", "")
        cfg.device_id = os.environ.get("SPOOLBUDDY_DEVICE_ID", "")
        cfg.hostname = os.environ.get("SPOOLBUDDY_HOSTNAME", "")

        cfg.nfc_driver = _driver("SPOOLBUDDY_NFC_DRIVER", cfg.nfc_driver, NFC_DRIVERS)
        cfg.scale_driver = _driver("SPOOLBUDDY_SCALE_DRIVER", cfg.scale_driver, SCALE_DRIVERS)

        if not cfg.backend_url:
            raise RuntimeError("SPOOLBUDDY_BACKEND_URL is required (e.g. http://192.168.1.100:5000)")
        if not cfg.api_key:
            raise RuntimeError("SPOOLBUDDY_API_KEY is required (create one in Bambuddy Settings → API Keys)")

        # Default device_id from MAC address
        if not cfg.device_id:
            cfg.device_id = _get_mac_id()

        # Default hostname from system
        if not cfg.hostname:
            cfg.hostname = socket.gethostname()

        return cfg


def _get_mac_id() -> str:
    """Generate a stable device ID from the primary network interface MAC address.

    Interfaces are sorted by name so the same interface is always picked
    regardless of filesystem iteration order (eth0 before wlan0, etc.).
    """
    try:
        ifaces = sorted(Path("/sys/class/net").iterdir(), key=lambda p: p.name)
        for iface in ifaces:
            if iface.name == "lo":
                continue
            addr_file = iface / "address"
            if addr_file.exists():
                mac = addr_file.read_text().strip().replace(":", "")
                if mac and mac != "000000000000":
                    return f"sb-{mac}"
    except Exception:
        pass
    import uuid

    return f"sb-{uuid.uuid4().hex[:12]}"
