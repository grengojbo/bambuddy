"""Shared test setup.

The hardware drivers import their bus libraries at module import time, which
only exist on a Raspberry Pi. Stub them here — before any test module imports
`daemon.*` — so the driver modules can be imported and patched on a laptop.
"""

import sys
from unittest.mock import MagicMock

for module in ("spidev", "gpiod", "smbus2"):
    sys.modules.setdefault(module, MagicMock())
