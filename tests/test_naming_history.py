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
    tid = db.start_transfer(sid, cam, "DCIM/DJI_001/DJI_X", "DJI_X.MP4", "video", 123, "internal", "D:/out/x.mp4")
    assert not db.is_offloaded(cam, "DCIM/DJI_001/DJI_X", 123)  # pending doesn't count
    db.finish_transfer(tid, "done", "aabb", verified=True)
    assert db.is_offloaded(cam, "DCIM/DJI_001/DJI_X", 123)
    assert db.deletable(cam, "DCIM/DJI_001/DJI_X", 123)
    assert not db.deletable(cam, "DCIM/DJI_001/DJI_X", 999)  # size mismatch
    db.finish_session(sid, 1, 123)
    rows = db.recent()
    assert len(rows) == 1 and rows[0].status == "done" and rows[0].verified
    db.mark_deleted_from_camera(cam, "DCIM/DJI_001/DJI_X")
    db.close()


def test_media_url_quoting():
    assert (
        CameraHttp.media_url(1, "DCIM/DJI_001/DJI_20260820143000_0012_D.MP4")
        == "/v2?storage=1&path=DCIM/DJI_001/DJI_20260820143000_0012_D.MP4"
    )
    assert CameraHttp.media_url(0, "MISC/THM/100/th x.scr") == "/v2?storage=0&path=MISC/THM/100/th%20x.scr"
