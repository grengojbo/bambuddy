# SpoolBuddy Hardware Setup

## PN5180 NFC Reader (SPI)

### Wiring

| PN5180 Pin | Raspberry Pi Pin | GPIO | Wire Color |
|------------|------------------|------|------------|
| 3V3        | Pin 1            | —    | Red        |
| 5V         | Pin 2            | —    | Red        |
| GND        | Pin 20           | —    | Black      |
| SCK        | Pin 23           | GPIO11 | Yellow   |
| MISO       | Pin 21           | GPIO9  | Blue     |
| MOSI       | Pin 19           | GPIO10 | Green    |
| NSS (CS)   | Pin 16           | GPIO23 | Orange   |
| BUSY       | Pin 22           | GPIO25 | White    |
| RST        | Pin 18           | GPIO24 | Brown    |

> **Power:** The PN5180 board has two power pins. 3V3 powers the IC itself,
> 5V powers the antenna booster and extends read range. Both should be connected.
> Do NOT connect 5V to the 3V3 pin — it will destroy the reader.

> **NSS:** We use GPIO23 for manual chip-select instead of the default SPI CE0
> (GPIO8) because the kernel SPI driver's automatic CS timing does not meet the
> PN5180's requirements (5µs setup, 100µs hold). The reader's NSS line is wired
> to GPIO23 only, so whether the kernel auto-toggles CE0 is electrically
> invisible to the PN5180. Pi 4 and Pi 5 are both supported — the code asks
> the driver to disable CE0 toggling but tolerates Pi 5's RP1 driver rejecting
> that request (#1424).

### Setup Steps

#### 1. Enable SPI and I2C

After a fresh Raspberry Pi OS install, SPI and I2C are disabled by default.

```bash
sudo raspi-config
# Navigate to: Interface Options -> SPI -> Enable
# Navigate to: Interface Options -> I2C -> Enable
sudo reboot
```

Verify after reboot:

```bash
ls /dev/spidev0.*
# Should show: /dev/spidev0.0  /dev/spidev0.1

ls /dev/i2c-*
# Should include: /dev/i2c-1
```

#### 2. Configure `/boot/firmware/config.txt`

Add the following lines under the `[all]` section:

```
# SpoolBuddy: I2C bus 1 for NAU7802 scale (GPIO2/GPIO3)
dtparam=i2c_arm=on

# SpoolBuddy: Disable SPI auto CS (manual CS on GPIO23 for PN5180)
dtoverlay=spi0-0cs
```

- `i2c_arm=on` enables I2C bus 1 (GPIO2/GPIO3). The NAU7802 is wired to bus 1.
  manual CS on GPIO23 because the driver's CS timing doesn't meet the PN5180's

Then reboot:

```bash
sudo reboot
```

Verify after reboot:

```bash
ls /dev/i2c-1
# Should exist

sudo i2cdetect -y 1
# Should show 0x2A (NAU7802)
```

#### 3. Install system packages

```bash
sudo apt install python3-spidev python3-libgpiod gpiod libgpiod3 i2c-tools
```

- `python3-spidev` / `libgpiod3` — system libraries for SPI and GPIO access
- `gpiod` — command-line GPIO tools (useful for debugging)

```bash
pip install spidev gpiod smbus2
```

- `spidev` — Python SPI bindings (PN5180 NFC reader)
- `gpiod` — Python GPIO bindings via libgpiod (works on both RPi 4 and RPi 5)

Wago connectors or breadboard jumpers are unreliable for SPI — the PN5180
is very sensitive to signal integrity issues (loose connections cause RF
field flickering, phantom errors, and intermittent communication failures).
**Solder all wires directly** for reliable operation.

#### 6. Verify hardware communication

Run the diagnostic script to confirm the PN5180 is responding:

```bash
sudo python3 spoolbuddy/pn5180_diag.py
```

Expected output includes product version (e.g. `v4.0`), firmware version,
register dump, and "Diagnostics complete" at the end.

#### 7. Test tag reading

```bash
sudo python3 spoolbuddy/read_tag.py
```

Place a tag on the reader. Supported tag types:

| Tag Type            | SAK    | Use Case                     |
|---------------------|--------|------------------------------|
| MIFARE Classic 1K   | `0x08` | Bambu Lab filament tags      |
| MIFARE Classic 4K   | `0x18` | Bambu Lab filament tags      |
| NTAG (213/215/216)  | `0x00` / `0x04` | SpoolEase / OpenPrintTag     |

### Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| All zeros from SPI reads | SPI not enabled | Run `raspi-config` and enable SPI, then reboot |
| `GENERAL_ERROR` on SEND_DATA | Automatic CS timing too fast | Use manual CS on GPIO23 with `spi0-0cs` overlay |
| `BUSY timeout` | Wiring issue or RST not connected | Check RST and BUSY pin connections |
| RF field flickering on/off | Loose power wires | Solder all connections |
| `No tag found` but tag is present | Wrong protocol or missing `setTransceiveMode()` | Ensure ISO 14443A config (`0x00, 0x80`) and `setTransceiveMode()` before every `SEND_DATA` |
| Auth failed for block N | Wrong key derivation | Verify HKDF uses context `"RFID-A\0"` (7 bytes including null terminator) |
| `EBUSY` when requesting GPIO8 | Kernel SPI driver owns CE0 | Use GPIO23 for NSS instead |

### Technical Notes

- SPI speed: **500 kHz** (higher speeds cause communication errors)
- SPI mode: **0** (CPOL=0, CPHA=0)


### Wiring

| NAU7802 Pin | Raspberry Pi Pin | GPIO   | Wire Color |
|-------------|------------------|--------|------------|
| VCC         | Pin 1            | —      | Red        |
| SDA         | Pin 3            | GPIO 2 | Yellow     |
| SCL         | Pin 5            | GPIO 3 | White      |
| GND         | Pin 30           | —      | Black      |

> **I2C Bus:** Uses I2C bus 1 (GPIO2/GPIO3), enabled via `dtparam=i2c_arm=on`
> in config.txt.

### Verify

```bash
sudo i2cdetect -y 1
# Should show 0x2A

sudo python3 spoolbuddy/scale_diag.py
```

The diagnostic reads 10 samples at 10 SPS and shows raw ADC values, average,
and spread. Typical idle readings are around ~500k with a spread under 20k.

---

## Alternative hardware: MFRC522 + HX711

The stock build uses a PN5180 reader and a NAU7802 scale. An MFRC522 reader and
an HX711 amplifier work just as well and are far easier to source. Select them
with two environment variables — everything else in the daemon is unchanged:

```
SPOOLBUDDY_NFC_DRIVER=mfrc522
SPOOLBUDDY_SCALE_DRIVER=hx711
```

Or, at install time:

```bash
sudo ./install.sh --mode spoolbuddy --nfc-driver mfrc522 --scale-driver hx711 \
    --bambuddy-url http://192.168.1.100:8000 --api-key bb_xxx --yes
```

### MFRC522 wiring

Unlike the PN5180, this reader uses the kernel's own chip select.

| MFRC522 Pin | Raspberry Pi Pin | GPIO |
|-------------|------------------|------|
| 3.3V        | Pin 1            | —    |
| RST         | Pin 22           | GPIO25 |
| GND         | Pin 25           | —    |
| IRQ         | —                | not connected |
| MISO        | Pin 21           | GPIO9  |
| MOSI        | Pin 19           | GPIO10 |
| SCK         | Pin 23           | GPIO11 |
| SDA (NSS)   | Pin 24           | GPIO8 / CE0 |

> **3.3V only.** 5V destroys the module.

> **`dtoverlay=spi0-0cs` must NOT be set.** That overlay removes CE0 so the
> PN5180 can drive chip select manually from GPIO23; with it in place the
> MFRC522 never responds. The installer manages this automatically, but check
> `/boot/firmware/config.txt` if you are converting an existing PN5180 build.

RST is optional — the driver falls back to a soft reset if the line cannot be
claimed.

### HX711 wiring

| HX711 Pin | Raspberry Pi Pin | GPIO |
|-----------|------------------|------|
| VCC       | Pin 17 (3.3V)    | —    |
| GND       | Pin 39           | —    |
| DT (DOUT) | Pin 29           | GPIO5 |
| SCK (PD_SCK) | Pin 31        | GPIO6 |

Load cell: red to E+, black to E−, white to A−, green to A+. If weight goes
negative under load, swap white and green.

> **Power the HX711 from 3.3V, not 5V.** On the common green module the digital
> supply is tied to VCC, so DOUT swings to whatever VCC is — and GPIO5 is not
> 5V tolerant. There is no sensitivity cost: the HX711 is ratiometric, deriving
> both the bridge excitation and the ADC reference from the same rail.

The beam must be mounted so it can bend: one end fixed, the other carrying the
load, with spacers between. An unmounted cell lying flat deforms by almost
nothing and reads as if nothing is on it.

### Why this scale driver maps registers directly

The HX711 powers down if PD_SCK stays high longer than 60 µs. Character-device
GPIO cannot meet that from Python. Measured on a Pi Model B+, 200 bursts of 24
pulses, time spent with PD_SCK high:

| Method | Median | Over 60 µs |
|--------|--------|------------|
| `lgpio` | 130 µs | 100% |
| `gpiod` v2 | 535 µs | 100% |
| `mmap` of `/dev/gpiomem` | 38 µs | 1.6% |
| `mmap` + sampling DOUT while low + `SCHED_FIFO` | **14 µs** | **0.12%** |

So the driver maps the GPIO block directly and samples DOUT after the falling
edge, leaving only two register writes inside the high phase.

Overruns are caught two ways. Most leave DOUT low where a finished conversion
would have raised it, and are discarded outright. The rest come from a chip that
power-cycled and then completed a fresh conversion, so the garbage looks
well-formed — those are caught by confirming any sudden jump with a second read,
since corruption is uncorrelated between reads while a real weight change
persists. Steady state costs nothing extra. Both matter in practice: NFC polling
shares the same core, and one wild sample poisons the moving average for twenty
readings. Measured over 30 s of concurrent NFC polling and weighing: 318
readings at 10.6 Hz, spread 313 counts (~0.75 g), zero outliers.

`SCHED_FIFO` needs `CAP_SYS_NICE`, which the systemd unit grants; without it the
driver still works with a smaller margin. `/dev/gpiomem` is owned by the `gpio`
group, so no root is required.

### Verify

```bash
sudo python3 scripts/mfrc522_diag.py
sudo python3 scripts/hx711_diag.py
```

### Behavioural differences from the PN5180

- **Full UID cascade.** 7-byte UIDs (NTAG213/215/216) are returned whole. The
  PN5180 path reads cascade level 1 only, giving a truncated UID prefixed with
  the 0x88 cascade tag, so `tag_uid` differs between the two readers for NTAG.
- **Writes are verified.** NTAG answers a write with a 4-bit ACK that the
  PN5180 cannot capture, so its `ntag_write_page()` always reports success. The
  MFRC522 reads the real ACK and reports a refused write.
- **No pre-poll reset.** The PN5180 wedges after a failed activation and needs a
  full RF re-init before every idle poll, costing ~240 ms. The MFRC522 opts out
  via `needs_reset_before_poll`, so polling runs at the configured interval.
