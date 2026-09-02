"""Tests for daemon.mfrc522 — UID cascade, NTAG ACK handling, Bambu tag decode.

The block data and UIDs below were captured from real hardware: a Bambu PLA
Basic spool and an NTAG215, both read over SPI by this driver.
"""

import pytest
from daemon.bambu_keys import TRAY_UUID_BLOCK
from daemon.mfrc522 import MFRC522, NTAG_ACK
from daemon.nfc_reader import _extract_tray_uuid

# Captured from a real Bambu Lab PLA Basic spool (white, 1 kg, 1.75 mm)
BAMBU_UID = bytes.fromhex("E35FACA1")
BAMBU_BLOCK_DUMP = {
    1: bytes.fromhex("4130302D573100004746413030000000"),  # "A00-W1", "GFA00"
    2: bytes.fromhex("504C4100000000000000000000000000"),  # "PLA"
    4: bytes.fromhex("504C4120426173696300000000000000"),  # "PLA Basic"
    5: bytes.fromhex("FFFFFFFFE80300000000E03F00000000"),  # white, 1000 g, 1.75 mm
    9: bytes.fromhex("6B9170BB5BD84DDBA748713C159367DF"),  # tray UID
}
BAMBU_TRAY_UUID = "6B9170BB5BD84DDBA748713C159367DF"

# Captured from a real NTAG215
NTAG_UID = bytes.fromhex("04149F193F6180")


def bare_reader() -> MFRC522:
    """An MFRC522 instance with no hardware behind it."""
    reader = object.__new__(MFRC522)
    reader._auth_uid = b""
    reader.clear_bits = lambda reg, mask: None
    reader.set_bits = lambda reg, mask: None
    return reader


def bcc(uid_part: bytes) -> int:
    return uid_part[0] ^ uid_part[1] ^ uid_part[2] ^ uid_part[3]


def script_cascade(reader: MFRC522, levels: list[tuple[bytes, int]]):
    """Wire transceive/_transceive_crc to replay an anticollision cascade.

    `levels` is one (four_uid_bytes, sak) pair per cascade level.
    """
    anticoll = iter(levels)
    selects = iter(levels)

    def transceive(data, valid_bits=0):
        if valid_bits == 7:
            return b"\x44\x00", 0  # ATQA
        if len(data) == 2 and data[1] == 0x20:
            four, _ = next(anticoll)
            return four + bytes([bcc(four)]), 0
        raise AssertionError(f"unexpected transceive {data}")

    def transceive_crc(data):
        assert data[1] == 0x70, "SELECT frame expected"
        _, sak = next(selects)
        return bytes([sak])

    reader.transceive = transceive
    reader._transceive_crc = transceive_crc


class TestDriverIdentity:
    def test_reports_itself_to_the_backend(self):
        assert MFRC522.reader_type == "MFRC522"
        assert MFRC522.connection == "SPI"

    def test_reader_type_fits_backend_column(self):
        # spoolbuddy_devices.nfc_reader_type is VARCHAR(20)
        assert len(MFRC522.reader_type) <= 20

    def test_opts_out_of_the_pn5180_pre_poll_reset(self):
        assert MFRC522.needs_reset_before_poll is False


class TestActivateTypeA:
    def test_four_byte_uid_single_level(self):
        reader = bare_reader()
        script_cascade(reader, [(BAMBU_UID, 0x08)])
        uid, sak = reader.activate_type_a()
        assert uid == BAMBU_UID
        assert sak == 0x08

    def test_seven_byte_uid_runs_full_cascade(self):
        """The cascade tag is dropped and both levels are concatenated."""
        reader = bare_reader()
        level1 = bytes([0x88]) + NTAG_UID[:3]
        level2 = NTAG_UID[3:]
        script_cascade(reader, [(level1, 0x04), (level2, 0x00)])
        uid, sak = reader.activate_type_a()
        assert uid == NTAG_UID
        assert len(uid) == 7
        assert sak == 0x00

    def test_authentication_uid_is_last_four_bytes(self):
        reader = bare_reader()
        level1 = bytes([0x88]) + NTAG_UID[:3]
        script_cascade(reader, [(level1, 0x04), (NTAG_UID[3:], 0x00)])
        reader.activate_type_a()
        assert reader._auth_uid == NTAG_UID[-4:]

    def test_no_tag_returns_none(self):
        reader = bare_reader()
        reader.transceive = lambda data, valid_bits=0: None
        assert reader.activate_type_a() is None

    def test_bad_bcc_rejected(self):
        reader = bare_reader()

        def transceive(data, valid_bits=0):
            if valid_bits == 7:
                return b"\x44\x00", 0
            return BAMBU_UID + b"\x00", 0  # deliberately wrong checksum

        reader.transceive = transceive
        assert reader.activate_type_a() is None

    def test_failed_select_returns_none(self):
        reader = bare_reader()

        def transceive(data, valid_bits=0):
            if valid_bits == 7:
                return b"\x44\x00", 0
            return BAMBU_UID + bytes([bcc(BAMBU_UID)]), 0

        reader.transceive = transceive
        reader._transceive_crc = lambda data: None
        assert reader.activate_type_a() is None


class TestNtagWrite:
    def _reader(self, response):
        reader = bare_reader()
        reader.calc_crc = lambda data: [0x00, 0x00]
        reader.transceive = lambda data, valid_bits=0: response
        return reader

    def test_ack_accepted(self):
        reader = self._reader((bytes([NTAG_ACK]), 4))
        assert reader.ntag_write_page(4, b"\x01\x02\x03\x04") is True

    def test_nak_rejected(self):
        """Unlike the PN5180 path, a refused write is actually reported."""
        reader = self._reader((bytes([0x00]), 4))
        assert reader.ntag_write_page(4, b"\x01\x02\x03\x04") is False

    def test_silence_rejected(self):
        reader = self._reader(None)
        assert reader.ntag_write_page(4, b"\x01\x02\x03\x04") is False

    def test_wrong_payload_size_rejected(self):
        reader = self._reader((bytes([NTAG_ACK, NTAG_ACK]), 0))
        assert reader.ntag_write_page(4, b"\x01\x02\x03\x04") is False

    def test_rejects_wrong_page_length(self):
        reader = self._reader((bytes([NTAG_ACK]), 4))
        assert reader.ntag_write_page(4, b"\x01\x02\x03") is False

    def test_pages_padded_to_four_bytes(self):
        reader = bare_reader()
        written = []
        reader.ntag_write_page = lambda page, data: written.append((page, data)) or True
        assert reader.ntag_write_pages(4, b"\x01\x02\x03\x04\x05") is True
        assert written == [(4, b"\x01\x02\x03\x04"), (5, b"\x05\x00\x00\x00")]

    def test_stops_at_first_rejected_page(self):
        reader = bare_reader()
        attempts = []

        def write_page(page, data):
            attempts.append(page)
            return page == 4

        reader.ntag_write_page = write_page
        assert reader.ntag_write_pages(4, b"\x00" * 12) is False
        assert attempts == [4, 5]


class TestReadBambuTag:
    def _reader(self):
        reader = bare_reader()
        reader.reactivate_card = lambda: (BAMBU_UID, 0x08)
        reader.mfc_authenticate = lambda block, key, uid: True
        reader.mfc_read_block = lambda block: BAMBU_BLOCK_DUMP.get(block)
        reader._auth_uid = BAMBU_UID
        return reader

    def test_reads_all_configured_blocks(self):
        blocks = self._reader().read_bambu_tag(BAMBU_UID)
        assert blocks == BAMBU_BLOCK_DUMP

    def test_authenticates_once_per_sector(self):
        reader = self._reader()
        sectors = []
        reader.mfc_authenticate = lambda block, key, uid: sectors.append(block // 4) or True
        reader.read_bambu_tag(BAMBU_UID)
        assert sectors == sorted(set(sectors)), "re-authenticated within a sector"
        assert sectors == [0, 1, 2]

    def test_auth_failure_aborts(self):
        reader = self._reader()
        reader.mfc_authenticate = lambda block, key, uid: False
        assert reader.read_bambu_tag(BAMBU_UID) is None

    def test_uid_change_aborts(self):
        reader = self._reader()
        reader.reactivate_card = lambda: (bytes.fromhex("DEADBEEF"), 0x08)
        assert reader.read_bambu_tag(BAMBU_UID) is None

    def test_missing_card_aborts(self):
        reader = self._reader()
        reader.reactivate_card = lambda: None
        assert reader.read_bambu_tag(BAMBU_UID) is None


class TestTrayUuidExtraction:
    def test_uses_block_nine(self):
        assert _extract_tray_uuid(BAMBU_BLOCK_DUMP) == BAMBU_TRAY_UUID

    def test_does_not_fall_back_to_filament_type(self):
        """Blocks 4-5 are identical across every spool of a product.

        The previous implementation returned block 4 as hex, so every "PLA
        Basic" spool collided on the same identifier.
        """
        without_uid = {k: v for k, v in BAMBU_BLOCK_DUMP.items() if k != TRAY_UUID_BLOCK}
        assert _extract_tray_uuid(without_uid) is None

    @pytest.mark.parametrize("filler", [b"\x00" * 16, b"\xff" * 16])
    def test_blank_block_is_not_an_identifier(self, filler):
        assert _extract_tray_uuid({TRAY_UUID_BLOCK: filler}) is None

    def test_short_block_rejected(self):
        assert _extract_tray_uuid({TRAY_UUID_BLOCK: b"\x01\x02"}) is None
