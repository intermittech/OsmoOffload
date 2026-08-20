import time
from pathlib import Path

import pytest

from osmooffload.core import mp4meta
from osmooffload.core.report import ReportFile, SessionReport, write_reports

REAL_CLIPS = list(Path("D:/DJI-Offload").glob("**/*.MP4"))


@pytest.mark.skipif(not REAL_CLIPS, reason="no offloaded clips on disk")
def test_probe_real_dji_clips():
    for clip in REAL_CLIPS[:3]:
        meta = mp4meta.probe(clip)
        assert meta.duration_s and meta.duration_s > 0.2, clip
        assert meta.width and meta.width >= 1280, clip
        assert meta.height and meta.height >= 720, clip


def test_probe_missing_file_is_graceful(tmp_path):
    meta = mp4meta.probe(tmp_path / "nope.mp4")
    assert meta.duration_s is None and meta.width is None


def test_update_version_compare():
    from osmooffload import update

    assert update._ver_tuple("0.9.0") < update._ver_tuple("0.10.0")
    assert update._ver_tuple("v1.2.3") == (1, 2, 3)
    assert update.check("0.9.0", "") is None  # disabled without a repo
    assert update.check("0.9.0", "not-a-repo") is None


def test_usb_volume_detection(tmp_path):
    from osmooffload.core import usb_ingest

    # random stick: DCIM alone is not enough
    (tmp_path / "DCIM" / "randomdir").mkdir(parents=True)
    assert not usb_ingest.looks_like_dji(tmp_path)
    # DJI-shaped: DCIM/DJI_001 with DJI-named media
    dji = tmp_path / "DCIM" / "DJI_001"
    dji.mkdir()
    (dji / "DJI_20260820133506_0001_D.MP4").write_bytes(b"x" * 128)
    (dji / "DJI_20260820133506_0001_D.LRF").write_bytes(b"x")
    assert usb_ingest.looks_like_dji(tmp_path)
    vol = usb_ingest.enumerate_volume(tmp_path)
    names = [f.name for f in vol.files]
    assert "DJI_20260820133506_0001_D.MP4" in names
    assert all(not n.endswith(".LRF") for n in names)  # proxies skipped
    assert vol.files[0].media_path == "DCIM/DJI_001/DJI_20260820133506_0001_D"


def test_report_writing(tmp_path):
    rep = SessionReport(
        camera="Pocket4P",
        started_at=time.time(),
        seconds=24.0,
        rate_mbs=61.0,
        files=[
            ReportFile("DJI_A.MP4", "a.mp4", "D:/x/a.mp4", 1_500_000_000, "aabb",
                       "sd", "video", True, duration_s=93.0, resolution="3840x2160"),
            ReportFile("DJI_B.JPG", "b.jpg", "D:/x/b.jpg", 8_000_000, "ccdd",
                       "internal", "photo", True),
        ],
    )
    assert "1 clip(s)" in rep.summary_line()
    assert "1 photo(s)" in rep.summary_line()
    assert rep.resolution_breakdown() == {"3840x2160": 1}
    html_path, csv_path = write_reports(rep, tmp_path)
    html = html_path.read_text(encoding="utf-8")
    assert "DJI_A.MP4" in html and "a.mp4" in html and "3840x2160" in html
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "DJI_B.JPG" in csv_text and "blake2b_128" in csv_text
