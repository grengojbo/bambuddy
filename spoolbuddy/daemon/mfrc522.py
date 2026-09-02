"""MFRC522 NFC frontend driver — drop-in alternative to the PN5180.

Exposes the same surface NFCReader calls on the PN5180, so switching readers is
a config change rather than a code change.

Differences from the PN5180 worth knowing about:

- Chip select is the kernel's (CE0 by default). The PN5180 needs manual CS via
  GPIO because its timing requirements are tighter than the SPI driver's
  automatic toggling; the MFRC522 has no such problem. This means
  `dtoverlay=spi0-0cs` must NOT be set — it removes CE0 entirely.
- `activate_type_a()` runs the full anticollision cascade, so 7-byte UIDs
  (NTAG213/215/216) come back complete. The PN5180 path reads cascade level 1
  only and returns a truncated UID prefixed with the 0x88 cascade tag.
- NTAG writes are verified. The tag answers a write with a 4-bit ACK (0x0A)
  which the PN5180 cannot capture, so its `ntag_write_page()` always reports
  success; the MFRC522 reports the real ACK via ControlReg's RxLastBits.
- Crypto1 for MIFARE Classic is handled in hardware by the MFAuthent command,
  same as the PN5180's MFC_AUTHENTICATE.
"""

import logging
import time

import spidev

from .bambu_keys import BAMBU_BLOCKS, get_sector_key, hkdf_derive_keys
from .hw_util import env_int, find_gpio_chip

logger = logging.getLogger(__name__)

RST_PIN = env_int("SPOOLBUDDY_NFC_RST_PIN", 25)
SPI_BUS = env_int("SPOOLBUDDY_NFC_SPI_BUS", 0)
SPI_DEVICE = env_int("SPOOLBUDDY_NFC_SPI_DEVICE", 0)
SPI_SPEED_HZ = env_int("SPOOLBUDDY_NFC_SPI_SPEED_HZ", 1_000_000)
ANTENNA_GAIN = env_int("SPOOLBUDDY_NFC_ANTENNA_GAIN", 0x07)  # 0-7, 7 = 48 dB

# Registers (the SPI address byte is (reg << 1) & 0x7E)
CommandReg = 0x01
ComIrqReg = 0x04
DivIrqReg = 0x05
ErrorReg = 0x06
Status2Reg = 0x08
FIFODataReg = 0x09
FIFOLevelReg = 0x0A
ControlReg = 0x0C
BitFramingReg = 0x0D
CollReg = 0x0E
ModeReg = 0x11
TxControlReg = 0x14
TxASKReg = 0x15
CRCResultRegH = 0x21
CRCResultRegL = 0x22
RFCfgReg = 0x26
TModeReg = 0x2A
TPrescalerReg = 0x2B
TReloadRegH = 0x2C
TReloadRegL = 0x2D
AutoTestReg = 0x36
VersionReg = 0x37

# Chip commands
CMD_IDLE = 0x00
CMD_MEM = 0x01
CMD_CALCCRC = 0x03
CMD_TRANSCEIVE = 0x0C
CMD_MFAUTHENT = 0x0E
CMD_SOFTRESET = 0x0F

# ComIrqReg bits
IRQ_RX = 0x20
IRQ_IDLE = 0x10
IRQ_TIMER = 0x01
# ProtocolErr | ParityErr | BufferOvfl — collisions and CRC are checked separately
ERR_MASK = 0x13

STATUS2_CRYPTO1ON = 0x08
FIFO_FLUSH = 0x80
START_SEND = 0x80
CMD_POWERDOWN = 0x10

# PICC commands (ISO 14443A)
PICC_REQA = 0x26
PICC_WUPA = 0x52
PICC_HALT = 0x50
PICC_SEL_CL1 = 0x93
PICC_SEL_CL2 = 0x95
PICC_AUTH_KEY_A = 0x60
PICC_READ = 0x30
PICC_NTAG_WRITE = 0xA2
CASCADE_TAG = 0x88

NTAG_ACK = 0x0A
TRANSCEIVE_TIMEOUT_S = 0.06


class MFRC522:
    reader_type = "MFRC522"
    connection = "SPI"
    # The MFRC522 keeps working across polls; only the PN5180 needs a full
    # hardware reset before each idle poll to avoid getting wedged.
    needs_reset_before_poll = False

    def __init__(self):
        # Last 4 UID bytes of the selected card — what MIFARE authentication uses
        self._auth_uid = b""
        self._spi = spidev.SpiDev()
        self._spi.open(SPI_BUS, SPI_DEVICE)
        self._spi.max_speed_hz = SPI_SPEED_HZ
        self._spi.mode = 0b00

        # Hardware reset line is optional — a soft reset is enough to bring the
        # chip up, so a board with RST tied high still works.
        self._chip = None
        self._lines = None
        try:
            import gpiod

            self._chip = find_gpio_chip()
            self._lines = self._chip.request_lines(
                consumer="mfrc522",
                config={
                    RST_PIN: gpiod.LineSettings(
                        direction=gpiod.line.Direction.OUTPUT,
                        output_value=gpiod.line.Value.ACTIVE,
                    )
                },
            )
        except Exception as e:
            logger.info("MFRC522 hardware reset line unavailable, using soft reset: %s", e)
            self._release_gpio()

        self.reset()

        version = self.read_reg(VersionReg)
        if version in (0x00, 0xFF):
            raise RuntimeError(f"MFRC522 not responding (VersionReg=0x{version:02X}) — check wiring, power and CS")
        logger.info("MFRC522 detected: VersionReg=0x%02X (%s)", version, _version_name(version))

    # -- low level --

    def read_reg(self, reg: int) -> int:
        return self._spi.xfer2([((reg << 1) & 0x7E) | 0x80, 0x00])[1]

    def write_reg(self, reg: int, val: int) -> None:
        self._spi.xfer2([(reg << 1) & 0x7E, val & 0xFF])

    def set_bits(self, reg: int, mask: int) -> None:
        self.write_reg(reg, self.read_reg(reg) | mask)

    def clear_bits(self, reg: int, mask: int) -> None:
        self.write_reg(reg, self.read_reg(reg) & ~mask)

    def close(self):
        try:
            self.rf_off()
        except Exception:
            pass
        try:
            self._spi.close()
        except Exception:
            pass
        self._release_gpio()

    def _release_gpio(self):
        for obj, method in ((self._lines, "release"), (self._chip, "close")):
            try:
                if obj is not None:
                    getattr(obj, method)()
            except Exception:
                pass
        self._lines = None
        self._chip = None

    # -- chip setup --

    def reset(self):
        """Hardware reset when the RST line is wired, soft reset otherwise."""
        if self._lines is not None:
            import gpiod

            self._lines.set_value(RST_PIN, gpiod.line.Value.INACTIVE)
            time.sleep(0.005)
            self._lines.set_value(RST_PIN, gpiod.line.Value.ACTIVE)
            time.sleep(0.050)
        else:
            self.write_reg(CommandReg, CMD_SOFTRESET)
            time.sleep(0.050)

        # PowerDown clears itself once the reset completes
        deadline = time.monotonic() + 0.5
        while self.read_reg(CommandReg) & CMD_POWERDOWN:
            if time.monotonic() > deadline:
                raise RuntimeError("MFRC522 stuck in power-down after reset")
            time.sleep(0.010)

        self._init_chip()

    def _init_chip(self):
        # Timer: prescaler 0x0D3E with TAuto, reload 30 -> ~15 ms timeout, which
        # is what bounds a transceive when no tag answers.
        self.write_reg(TModeReg, 0x8D)
        self.write_reg(TPrescalerReg, 0x3E)
        self.write_reg(TReloadRegH, 0x00)
        self.write_reg(TReloadRegL, 30)
        self.write_reg(TxASKReg, 0x40)  # Force100ASK
        self.write_reg(ModeReg, 0x3D)  # CRC preset 0x6363
        self.write_reg(RFCfgReg, (ANTENNA_GAIN & 0x07) << 4)
        self.rf_on()

    def load_rf_config(self, tx: int = 0, rx: int = 0) -> None:
        """No-op. Present so the PN5180 call sites work unchanged."""

    def set_transceive_mode(self) -> None:
        """No-op. Transceive mode is set per command, not held as state."""

    def rf_on(self):
        if (self.read_reg(TxControlReg) & 0x03) != 0x03:
            self.set_bits(TxControlReg, 0x03)
            time.sleep(0.005)

    def rf_off(self):
        self.clear_bits(TxControlReg, 0x03)

    def self_test(self) -> list[int]:
        """Run the built-in digital self test, returning the 64-byte result.

        Used by the diagnostic script: a healthy chip produces a varied
        signature, a dead one produces all zeros or all 0xFF.
        """
        self.write_reg(CommandReg, CMD_SOFTRESET)
        time.sleep(0.050)
        self.write_reg(FIFOLevelReg, FIFO_FLUSH)
        for _ in range(25):
            self.write_reg(FIFODataReg, 0x00)
        self.write_reg(CommandReg, CMD_MEM)
        self.write_reg(AutoTestReg, 0x09)
        self.write_reg(FIFODataReg, 0x00)
        self.write_reg(CommandReg, CMD_CALCCRC)

        deadline = time.monotonic() + 1.0
        while self.read_reg(FIFOLevelReg) < 64:
            if time.monotonic() > deadline:
                break
            time.sleep(0.010)
        result = [self.read_reg(FIFODataReg) for _ in range(64)]
        self.write_reg(AutoTestReg, 0x00)
        self.reset()
        return result

    # -- transceive --

    def calc_crc(self, data: list[int]) -> list[int]:
        """CRC_A over data using the on-chip coprocessor. Returns [low, high]."""
        self.write_reg(CommandReg, CMD_IDLE)
        self.write_reg(DivIrqReg, 0x04)
        self.write_reg(FIFOLevelReg, FIFO_FLUSH)
        for b in data:
            self.write_reg(FIFODataReg, b)
        self.write_reg(CommandReg, CMD_CALCCRC)

        deadline = time.monotonic() + 0.1
        while time.monotonic() < deadline:
            if self.read_reg(DivIrqReg) & 0x04:
                break
        self.write_reg(CommandReg, CMD_IDLE)
        return [self.read_reg(CRCResultRegL), self.read_reg(CRCResultRegH)]

    def transceive(self, data: list[int], valid_bits: int = 0) -> tuple[bytes, int] | None:
        """Send a frame and collect the answer.

        `valid_bits` is the number of bits to send from the last byte (0 = all
        eight), needed for the 7-bit REQA/WUPA short frames.

        Returns (payload, rx_valid_bits) where rx_valid_bits is the number of
        valid bits in the final received byte (0 = all eight), or None on
        timeout or a protocol error.
        """
        self.write_reg(CommandReg, CMD_IDLE)
        self.write_reg(ComIrqReg, 0x7F)
        self.write_reg(FIFOLevelReg, FIFO_FLUSH)
        for b in data:
            self.write_reg(FIFODataReg, b)
        self.write_reg(BitFramingReg, valid_bits & 0x07)
        self.write_reg(CommandReg, CMD_TRANSCEIVE)
        self.write_reg(BitFramingReg, START_SEND | (valid_bits & 0x07))

        deadline = time.monotonic() + TRANSCEIVE_TIMEOUT_S
        while True:
            irq = self.read_reg(ComIrqReg)
            if irq & (IRQ_RX | IRQ_IDLE):
                break
            if irq & IRQ_TIMER:
                self.write_reg(BitFramingReg, valid_bits & 0x07)
                return None
            if time.monotonic() > deadline:
                self.write_reg(BitFramingReg, valid_bits & 0x07)
                return None

        self.write_reg(BitFramingReg, valid_bits & 0x07)

        if self.read_reg(ErrorReg) & ERR_MASK:
            return None

        length = self.read_reg(FIFOLevelReg)
        payload = bytes(self.read_reg(FIFODataReg) for _ in range(length))
        return payload, self.read_reg(ControlReg) & 0x07

    def _transceive_crc(self, data: list[int]) -> bytes | None:
        """Transceive with CRC_A appended and the answer's CRC verified."""
        result = self.transceive(data + self.calc_crc(data))
        if result is None:
            return None
        payload, _ = result
        if len(payload) < 3:
            return None
        body, crc = payload[:-2], payload[-2:]
        if bytes(self.calc_crc(list(body))) != crc:
            logger.debug("CRC mismatch on %d-byte response", len(payload))
            return None
        return body

    # -- ISO 14443A --

    def activate_type_a(self) -> tuple[bytes, int] | None:
        """WUPA -> anticollision cascade -> SELECT. Returns (uid, sak) or None.

        The cascade runs to completion, so 7-byte UIDs come back whole rather
        than truncated to cascade level 1.
        """
        self.clear_bits(Status2Reg, STATUS2_CRYPTO1ON)
        self.clear_bits(CollReg, 0x80)  # clear ValuesAfterColl

        atqa = self.transceive([PICC_WUPA], valid_bits=7)
        if atqa is None or len(atqa[0]) != 2:
            atqa = self.transceive([PICC_REQA], valid_bits=7)
            if atqa is None or len(atqa[0]) != 2:
                return None

        uid = bytearray()
        sak = 0
        for select in (PICC_SEL_CL1, PICC_SEL_CL2):
            anticoll = self.transceive([select, 0x20])
            if anticoll is None or len(anticoll[0]) != 5:
                return None
            level = anticoll[0]
            four, bcc = level[:4], level[4]
            if four[0] ^ four[1] ^ four[2] ^ four[3] != bcc:
                logger.debug("BCC mismatch during anticollision")
                return None

            response = self._transceive_crc([select, 0x70, *four, bcc])
            if not response:
                return None
            sak = response[0]

            if four[0] == CASCADE_TAG:
                uid += four[1:4]  # first byte is the cascade tag, not UID data
            else:
                uid += four
                break
            if not sak & 0x04:  # bit 3 clear means the UID is complete
                break

        # Kept for MIFARE authentication, which always uses the last 4 UID bytes
        self._auth_uid = bytes(uid[-4:])
        return bytes(uid), sak

    def halt(self):
        """Send HLTA. The tag answers with silence, so no reply is expected."""
        frame = [PICC_HALT, 0x00]
        self.transceive(frame + self.calc_crc(frame))

    def reactivate_card(self) -> tuple[bytes, int] | None:
        """Drop the field and re-select the card, clearing any Crypto1 state."""
        self.halt()
        self.rf_off()
        time.sleep(0.010)
        self.clear_bits(Status2Reg, STATUS2_CRYPTO1ON)
        self.rf_on()
        time.sleep(0.020)
        return self.activate_type_a()

    # -- MIFARE Classic --

    def mfc_authenticate(self, block: int, key: bytes, uid: bytes) -> bool:
        """Authenticate a sector with Key A via the MFAuthent command.

        Crypto1 runs in hardware from here on, so subsequent reads are
        transparently encrypted and decrypted.
        """
        buf = [PICC_AUTH_KEY_A, block, *key[:6], *uid[-4:]]
        self.write_reg(CommandReg, CMD_IDLE)
        self.write_reg(ComIrqReg, 0x7F)
        self.write_reg(FIFOLevelReg, FIFO_FLUSH)
        for b in buf:
            self.write_reg(FIFODataReg, b)
        self.write_reg(CommandReg, CMD_MFAUTHENT)

        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            if self.read_reg(ComIrqReg) & IRQ_IDLE:
                break
            time.sleep(0.001)

        return bool(self.read_reg(Status2Reg) & STATUS2_CRYPTO1ON)

    def mfc_read_block(self, block: int) -> bytes | None:
        """Read one 16-byte block. The sector must be authenticated first."""
        body = self._transceive_crc([PICC_READ, block])
        if body is None or len(body) != 16:
            return None
        return body

    def read_bambu_tag(self, uid: bytes) -> dict[int, bytes] | None:
        """Read the Bambu data blocks using HKDF-derived per-sector keys."""
        keys = hkdf_derive_keys(uid)

        self.clear_bits(Status2Reg, STATUS2_CRYPTO1ON)
        result = self.reactivate_card()
        if result is None:
            logger.debug("Failed to reactivate card for Bambu tag read")
            return None
        uid_check, _ = result
        if uid_check != uid:
            logger.debug("UID mismatch after reactivation: %s != %s", uid_check.hex(), uid.hex())
            return None

        blocks: dict[int, bytes] = {}
        current_sector = -1
        for block in BAMBU_BLOCKS:
            sector = block // 4
            if sector != current_sector:
                if not self.mfc_authenticate(block, get_sector_key(keys, block), self._auth_uid):
                    logger.debug("Auth failed for block %d (sector %d)", block, sector)
                    return None
                current_sector = sector

            data = self.mfc_read_block(block)
            if data is None:
                logger.debug("Read failed for block %d", block)
                return None
            blocks[block] = data

        return blocks

    # -- NTAG --

    def ntag_read_pages(self, start_page: int, num_pages: int) -> bytes | None:
        """Read NTAG pages (4 bytes each). READ returns 4 pages per command."""
        out = bytearray()
        page = start_page
        while len(out) < num_pages * 4:
            body = self._transceive_crc([PICC_READ, page])
            if body is None or len(body) < 16:
                logger.debug("NTAG read failed at page %d", page)
                return None
            out += body[:16]
            page += 4
        return bytes(out[: num_pages * 4])

    def ntag_write_page(self, page: int, data: bytes) -> bool:
        """Write 4 bytes to one NTAG page and check the tag's 4-bit ACK."""
        if len(data) != 4:
            return False
        frame = [PICC_NTAG_WRITE, page, *data]
        result = self.transceive(frame + self.calc_crc(frame))
        if result is None:
            return False
        payload, valid_bits = result
        # The ACK is a 4-bit frame: 0x0A means accepted, anything else is a NAK
        if len(payload) != 1 or valid_bits not in (0, 4):
            return False
        return (payload[0] & 0x0F) == NTAG_ACK

    def ntag_write_pages(self, start_page: int, data: bytes) -> bool:
        """Write consecutive NTAG pages, stopping at the first NAK."""
        padded = bytearray(data)
        while len(padded) % 4:
            padded.append(0x00)

        num_pages = len(padded) // 4
        for i in range(num_pages):
            page = start_page + i
            if not self.ntag_write_page(page, bytes(padded[i * 4 : i * 4 + 4])):
                logger.warning("NTAG write rejected at page %d (of %d pages)", page, num_pages)
                return False
            time.sleep(0.002)

        logger.info("NTAG write complete (%d pages)", num_pages)
        return True

    def read_ntag(self, uid: bytes) -> bytes | None:
        """Read the NDEF data area, pages 4-20 (68 bytes)."""
        if self.reactivate_card() is None:
            logger.debug("Failed to reactivate card for NTAG read")
            return None
        return self.ntag_read_pages(start_page=4, num_pages=17)


def _version_name(version: int) -> str:
    return {0x88: "clone FM17522", 0x90: "v0.0", 0x91: "v1.0", 0x92: "v2.0"}.get(version, "unknown")
