import time
from pathlib import Path

from osmooffload import naming
from osmooffload.history import HistoryDB
from osmooffload.net.downloader import CameraHttp


def test_parse_dji_name():
    p = naming.parse_name("DJI_20260820143000_0012_D.MP4")
    assert p.ext == "MP4" and p.is_video and p.kind == "video"
    assert p.timestamp is not None and p.timestamp.year == 2026
    assert p.seq == "0012"
    assert naming.date_folder(p) == "2026-08-20"


def test_parse_custom_and_photo():
    p = naming.parse_name("DJI_20251231235959_0001_D.JPG")
    assert p.kind == "photo"
    assert naming.date_folder(p) == "2025-12-31"
    q = naming.parse_name("WEIRD_NAME.MP4")
    assert q.timestamp is None
    assert naming.date_folder(q) == "undated"


def test_template():
    p = naming.parse_name("DJI_20260820143000_0012_D.MP4")
    out = naming.apply_template(
        "{date}_{time}_{camera}_{seq}", p, "Pocket4P", "DJI_20260820143000_0012_D.MP4"
    )
    assert out == "2026-08-20_143000_Pocket4P_0012.mp4"
    # default template = original name
    out2 = naming.apply_template("{original}", p, "cam", "DJI_20260820143000_0012_D.MP4")
    assert out2 == "DJI_20260820143000_0012_D.MP4"


def test_template_sanitizes():
    p = naming.parse_name("DJI_20260820143000_0012_D.MP4")
    out = naming.apply_template("{camera}/{seq}", p, 'bad<name>:"x"', "orig")
    assert "<" not in out and ">" not in out and '"' not in out


def test_history_roundtrip(tmp_path: Path):
    db = HistoryDB(tmp_path / "hist.sqlite3")
    cam = "Pocket4P-88A6"
    sid = db.start_session(cam)
    assert not db.is_offloaded(cam, "DCIM/DJI_001/DJI_X", 123)
    tid = db.start_transfer(sid, cam, "DCIM/DJI_001/DJI_X", "DJI_X.MP4", "video", 123,
                            "internal", "D:/out/x.mp4", dest_name="x.mp4")
    assert not db.is_offloaded(cam, "DCIM/DJI_001/DJI_X", 123)  # pending doesn't count
    db.finish_transfer(tid, "done", "aabb", verified=True)
    assert db.is_offloaded(cam, "DCIM/DJI_001/DJI_X", 123)
    assert db.deletable(cam, "DCIM/DJI_001/DJI_X", 123)
    assert not db.deletable(cam, "DCIM/DJI_001/DJI_X", 999)  # size mismatch
    db.finish_session(sid, 1, 123)
    rows = db.recent()
    assert len(rows) == 1 and rows[0].status == "done" and rows[0].verified
    assert rows[0].name == "DJI_X.MP4" and rows[0].dest_name == "x.mp4"
    db.mark_deleted_from_camera(cam, "DCIM/DJI_001/DJI_X")
    db.close()


def test_derive_missing_handles():
    from types import SimpleNamespace

    from osmooffload.ui.controller import CameraController

    def item(name, handle, storage):
        return SimpleNamespace(record=SimpleNamespace(
            name=name, handle=handle, storage=storage))

    ctrl = CameraController.__new__(CameraController)

    # 3 known handles (step 0x40) let us derive the 0007 still -> 0x401001c0
    plan = [
        item("DJI_20260820_0004_D.MP4", 0x40100100, 1),
        item("DJI_20260820_0005_D.MP4", 0x40100140, 1),
        item("DJI_20260820_0006_D.MP4", 0x40100180, 1),
        item("DJI_20260820_0007_D.JPG", 0, 1),
    ]
    assert ctrl._derive_missing_handles(plan) == {"DJI_20260820_0007_D.JPG": 0x401001C0}

    # only 2 known handles -> not enough to trust a step -> derive nothing
    plan2 = [
        item("DJI_0004_D.MP4", 0x40100100, 1),
        item("DJI_0005_D.MP4", 0x40100140, 1),
        item("DJI_0006_D.JPG", 0, 1),
    ]
    assert ctrl._derive_missing_handles(plan2) == {}

    # 3 handles that are NOT collinear (no single step) -> refuse to fit
    plan3 = [
        item("DJI_0004_D.MP4", 0x40100100, 1),
        item("DJI_0005_D.MP4", 0x40100140, 1),
        item("DJI_0006_D.MP4", 0x40100199, 1),  # breaks the step
        item("DJI_0007_D.JPG", 0, 1),
    ]
    assert ctrl._derive_missing_handles(plan3) == {}


def test_delete_payload_matches_capture():
    from osmooffload.camera.session import CameraDatalink

    # DUML example from MEDIA_PROTOCOL.md §2: delete handle 0x40104480
    assert (
        CameraDatalink.delete_payload([0x40104480]).hex()
        == "018044104001000000000100000001010000"
    )


def test_media_url_quoting():
    assert (
        CameraHttp.media_url(1, "DCIM/DJI_001/DJI_20260820143000_0012_D.MP4")
        == "/v2?storage=1&path=DCIM/DJI_001/DJI_20260820143000_0012_D.MP4"
    )
    assert CameraHttp.media_url(0, "MISC/THM/100/th x.scr") == "/v2?storage=0&path=MISC/THM/100/th%20x.scr"
