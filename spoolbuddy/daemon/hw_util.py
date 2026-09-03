"""Small helpers shared by the hardware drivers."""

import os


def env_int(name: str, default: int) -> int:
    """Read an integer from the environment, falling back to default."""
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def find_gpio_chip():
    """Return the gpiod chip that owns the 40-pin header.

    Pi 5 exposes it as /dev/gpiochip4, everything older as /dev/gpiochip0, so
    probe both and match on the pinctrl label rather than trusting the number.
    """
    import gpiod

    for path in ["/dev/gpiochip4", "/dev/gpiochip0"]:
        try:
            chip = gpiod.Chip(path)
            if "pinctrl" in chip.get_info().label:
                return chip
            chip.close()
        except (FileNotFoundError, PermissionError, OSError):
            continue
    raise RuntimeError("No GPIO chip")
