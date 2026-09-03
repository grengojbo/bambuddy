"""Legacy tray_uuid values a SpoolBuddy daemon derived from the wrong tag blocks.

A daemon that reads tray_uuid from Bambu tag blocks 4-5 picks up the filament
description ("PLA Basic", colour, weight) rather than the per-spool UUID in
block 9. Every spool of one product therefore reports the same tray_uuid --
504C4120426173696300000000000000 for white PLA Basic -- and the backend, which
identifies spools by tray_uuid, merges them into one row (#984).

Fixing the daemon corrects new scans, but installs already carry these values
in ``spool.tray_uuid``. They are valid 32-char hex, so no existing validation
rejects them. What sets them apart is that they decode to printable ASCII
followed by NUL padding, which a random 16-byte UUID practically never does.
That shape is the marker used here to recognise them, ignore them at lookup,
and clear them on upgrade.
"""

from sqlalchemy import text

from backend.app.models.spool import Spool
from backend.app.services.spool_tag_matcher import get_spool_by_tag
from backend.app.utils.tag_normalization import is_text_artifact_tray_uuid

# "PLA Basic" + NUL padding — what block 4 of a white PLA Basic tag contains.
PLA_BASIC_ARTIFACT = "504C4120426173696300000000000000"
# Block 9 of a real tag (MIFARE Classic 1K, UID E35FACA1).
REAL_TRAY_UUID = "6B9170BB5BD84DDBA748713C159367DF"

# The migration runs on raw SQL, so the inserts below have to satisfy the
# NOT NULL columns the ORM would otherwise default for us.
_SPOOL_COLUMNS = (
    "id, material, label_weight, core_weight, weight_used, weight_used_baseline, weight_locked, tag_uid, tray_uuid"
)
_SPOOL_DEFAULTS = "'PLA', 1000, 250, 0, 0, 0"


class TestIsTextArtifactTrayUuid:
    def test_recognises_the_pla_basic_artifact(self):
        assert is_text_artifact_tray_uuid(PLA_BASIC_ARTIFACT) is True

    def test_recognises_other_filament_descriptions(self):
        petg = b"PETG HF".ljust(16, b"\x00").hex().upper()

        assert is_text_artifact_tray_uuid(petg) is True

    def test_accepts_lowercase_and_whitespace(self):
        assert is_text_artifact_tray_uuid(f"  {PLA_BASIC_ARTIFACT.lower()} ") is True

    def test_real_uuid_is_not_an_artifact(self):
        assert is_text_artifact_tray_uuid(REAL_TRAY_UUID) is False

    def test_all_zeroes_is_not_an_artifact(self):
        """Zeroed UUIDs are already handled as invalid elsewhere."""
        assert is_text_artifact_tray_uuid("0" * 32) is False

    def test_empty_is_not_an_artifact(self):
        assert is_text_artifact_tray_uuid("") is False
        assert is_text_artifact_tray_uuid(None) is False

    def test_wrong_length_is_not_an_artifact(self):
        assert is_text_artifact_tray_uuid("504C4120") is False

    def test_text_after_padding_is_not_an_artifact(self):
        """Real UUIDs may contain 0x00 bytes; only trailing padding counts."""
        interleaved = (b"PLA\x00Basic\x00\x00\x00\x00\x00\x00\x00").hex().upper()

        assert is_text_artifact_tray_uuid(interleaved) is False


class TestTagScannedRequestDropsArtifacts:
    """A daemon that predates the fix keeps sending the description as a UUID."""

    def test_artifact_tray_uuid_is_dropped(self):
        from backend.app.schemas.spoolbuddy import TagScannedRequest

        req = TagScannedRequest(device_id="sb1", tag_uid="E35FACA1", tray_uuid=PLA_BASIC_ARTIFACT)

        assert req.tray_uuid is None

    def test_real_tray_uuid_is_kept(self):
        from backend.app.schemas.spoolbuddy import TagScannedRequest

        req = TagScannedRequest(device_id="sb1", tag_uid="E35FACA1", tray_uuid=REAL_TRAY_UUID)

        assert req.tray_uuid == REAL_TRAY_UUID


class TestGetSpoolByTagIgnoresArtifacts:
    async def _spool(self, db_session, *, tag_uid, tray_uuid):
        spool = Spool(material="PLA", subtype="Basic", tag_uid=tag_uid, tray_uuid=tray_uuid)
        db_session.add(spool)
        await db_session.commit()
        await db_session.refresh(spool)
        return spool

    async def test_falls_back_to_tag_uid_when_uuid_is_an_artifact(self, db_session):
        """Two spools share the bogus UUID; only the tag UID tells them apart."""
        first = await self._spool(db_session, tag_uid="E35FACA1", tray_uuid=PLA_BASIC_ARTIFACT)
        second = await self._spool(db_session, tag_uid="AABBCCDD", tray_uuid=PLA_BASIC_ARTIFACT)

        found = await get_spool_by_tag(db_session, "AABBCCDD", PLA_BASIC_ARTIFACT)

        assert found is not None
        assert found.id == second.id
        assert found.id != first.id

    async def test_real_uuid_still_matches(self, db_session):
        spool = await self._spool(db_session, tag_uid="E35FACA1", tray_uuid=REAL_TRAY_UUID)

        found = await get_spool_by_tag(db_session, "", REAL_TRAY_UUID)

        assert found is not None
        assert found.id == spool.id


class TestArtifactTrayUuidMigration:
    async def test_clears_artifacts_and_keeps_real_uuids(self, test_engine):
        from backend.app.core.database import _migrate_clear_text_artifact_tray_uuids

        async with test_engine.begin() as conn:
            await conn.execute(
                text(
                    f"INSERT INTO spool ({_SPOOL_COLUMNS}) VALUES "
                    f"(1, {_SPOOL_DEFAULTS}, 'E35FACA1', :artifact), "
                    f"(2, {_SPOOL_DEFAULTS}, 'AABBCCDD', :artifact), "
                    f"(3, {_SPOOL_DEFAULTS}, 'DDEEFF00', :real), "
                    f"(4, {_SPOOL_DEFAULTS}, '11223344', NULL)"
                ),
                {"artifact": PLA_BASIC_ARTIFACT, "real": REAL_TRAY_UUID},
            )
            await _migrate_clear_text_artifact_tray_uuids(conn)

        async with test_engine.connect() as conn:
            rows = dict((await conn.execute(text("SELECT id, tray_uuid FROM spool ORDER BY id"))).all())

        assert rows == {1: None, 2: None, 3: REAL_TRAY_UUID, 4: None}

    async def test_is_idempotent(self, test_engine):
        from backend.app.core.database import _migrate_clear_text_artifact_tray_uuids

        async with test_engine.begin() as conn:
            await conn.execute(
                text(f"INSERT INTO spool ({_SPOOL_COLUMNS}) VALUES (1, {_SPOOL_DEFAULTS}, NULL, :real)"),
                {"real": REAL_TRAY_UUID},
            )
            await _migrate_clear_text_artifact_tray_uuids(conn)
            await _migrate_clear_text_artifact_tray_uuids(conn)

        async with test_engine.connect() as conn:
            remaining = (await conn.execute(text("SELECT tray_uuid FROM spool WHERE id = 1"))).scalar_one()

        assert remaining == REAL_TRAY_UUID
