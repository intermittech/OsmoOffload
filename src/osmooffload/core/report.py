"""Session reports: one nicely-formatted HTML page per camera session
(transfer, refresh, delete, USB ingest) plus a CSV for transfers.

The HTML carries the human story — summary, per-file table with original ->
destination names and folders, speeds — and the full DEBUG session log at the
bottom inside a collapsed <details> fold, so it's there when needed and
invisible when not.
"""

from __future__ import annotations

import csv
import html
import time
from dataclasses import dataclass, field
from pathlib import Path

ACTION_TITLES = {
    "transfer": "Transfer",
    "refresh": "Refresh",
    "delete": "Delete from camera",
    "usb": "USB ingest",
}


@dataclass
class ReportFile:
    original: str
    saved_as: str
    dest_path: str
    size: int
    hash_hex: str | None
    storage: str | None
    kind: str
    verified: bool
    duration_s: float | None = None
    resolution: str | None = None
    speed_mbs: float | None = None
    note: str | None = None  # e.g. "deleted from camera", "failed: …"


@dataclass
class SessionReport:
    camera: str
    started_at: float
    seconds: float
    rate_mbs: float
    action: str = "transfer"
    files: list[ReportFile] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # failures, extra context

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def videos(self) -> list[ReportFile]:
        return [f for f in self.files if f.kind == "video"]

    @property
    def photos(self) -> list[ReportFile]:
        return [f for f in self.files if f.kind == "photo"]

    @property
    def total_runtime_s(self) -> float:
        return sum(f.duration_s or 0 for f in self.videos)

    def resolution_breakdown(self) -> dict[str, int]:
        # files whose resolution couldn't be read are simply left out —
        # "unknown" is noise, not information
        out: dict[str, int] = {}
        for f in self.videos:
            if f.resolution:
                out[f.resolution] = out.get(f.resolution, 0) + 1
        return out

    def summary_line(self) -> str:
        if self.action == "delete":
            return (f"{len(self.files)} file(s) deleted from the camera, "
                    f"{self.total_bytes / 1e9:.2f} GB freed")
        if self.action == "refresh":
            return f"{len(self.files)} file(s) on camera"
        parts = []
        if self.videos:
            rt = self.total_runtime_s
            parts.append(f"{len(self.videos)} clip(s), {rt / 60:.1f} min footage")
        if self.photos:
            parts.append(f"{len(self.photos)} photo(s)")
        parts.append(f"{self.total_bytes / 1e9:.2f} GB at {self.rate_mbs:.0f} MB/s")
        return " · ".join(parts)


def _fmt_dur(s: float | None) -> str:
    if not s:
        return "—"
    m, sec = divmod(int(round(s)), 60)
    return f"{m}:{sec:02d}"


def _fmt_size(n: int) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.1f} MB"


def write_reports(
    report: SessionReport,
    out_dir: Path,
    log_text: str | None = None,
) -> tuple[Path, Path | None]:
    """Write session_<stamp>.html (always) and .csv (when files exist).
    Returns (html_path, csv_path|None)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S", time.localtime(report.started_at))
    base = f"session_{stamp}_{report.action}"
    csv_path: Path | None = None

    if report.files:
        csv_path = out_dir / f"{base}.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["original", "saved_as", "dest_path", "bytes", "blake2b_128",
                        "storage", "kind", "duration_s", "resolution", "speed_mbs",
                        "verified", "note"])
            for r in report.files:
                w.writerow([r.original, r.saved_as, r.dest_path, r.size, r.hash_hex or "",
                            r.storage or "", r.kind,
                            f"{r.duration_s:.1f}" if r.duration_s else "",
                            r.resolution or "",
                            f"{r.speed_mbs:.1f}" if r.speed_mbs else "",
                            "yes" if r.verified else "no", r.note or ""])

    res = " · ".join(f"{k}: {v}" for k, v in report.resolution_breakdown().items())
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(report.started_at))
    title = ACTION_TITLES.get(report.action, report.action.title())

    if report.files:
        rows = "\n".join(
            f"<tr><td>{html.escape(r.original)}</td>"
            f"<td>{html.escape(r.saved_as if r.saved_as != r.original else '(unchanged)') if r.saved_as else '—'}</td>"
            f"<td class='path'>{html.escape(str(Path(r.dest_path).parent) if r.dest_path else '—')}</td>"
            f"<td class='num'>{_fmt_size(r.size)}</td><td>{_fmt_dur(r.duration_s)}</td>"
            f"<td>{html.escape(r.resolution or '—')}</td><td>{html.escape(r.storage or '—')}</td>"
            f"<td class='num'>{f'{r.speed_mbs:.0f} MB/s' if r.speed_mbs else '—'}</td>"
            f"<td>{'✔' if r.verified else '✘'}{(' · ' + html.escape(r.note)) if r.note else ''}</td></tr>"
            for r in report.files
        )
        table = f"""<table><tr><th>Original</th><th>Saved as</th><th>Destination folder</th>
<th>Size</th><th>Length</th><th>Resolution</th><th>Store</th><th>Speed</th><th>OK</th></tr>
{rows}</table>"""
    else:
        table = "<p class='sub'>No files were involved in this session.</p>"

    notes_html = ""
    if report.notes:
        items = "".join(f"<li>{html.escape(n)}</li>" for n in report.notes)
        notes_html = f"<div class='notes'><b>Notes</b><ul>{items}</ul></div>"

    log_html = ""
    if log_text:
        log_html = f"""<details class="log"><summary>Debug log
({len(log_text.splitlines())} lines — click to unfold)</summary>
<pre>{html.escape(log_text)}</pre></details>"""

    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Osmo Offload — {html.escape(title)} — {when}</title>
<style>
body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #141619; color: #e9ebee;
       margin: 2.5rem auto; max-width: 68rem; padding: 0 1rem; }}
h1 {{ font-size: 1.3rem; }} .sub {{ color: #9ba3ad; margin-bottom: 1.5rem; }}
.summary {{ background: #212429; border: 1px solid #343a42; border-radius: 10px;
            padding: 1rem 1.2rem; margin-bottom: 1.5rem; }}
.notes {{ background: #2a2118; border: 1px solid #4a3d26; border-radius: 10px;
          padding: 0.8rem 1.2rem; margin-bottom: 1.5rem; }}
.notes ul {{ margin: 0.4rem 0 0 1.2rem; padding: 0; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
th, td {{ text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid #262a30;
          vertical-align: top; }}
th {{ color: #9ba3ad; font-weight: 600; }} td.num {{ text-align: right; white-space: nowrap; }}
td.path {{ color: #9ba3ad; font-size: 0.82rem; word-break: break-all; }}
details.log {{ margin-top: 2rem; border-top: 1px solid #343a42; padding-top: 1rem; }}
details.log summary {{ color: #9ba3ad; cursor: pointer; font-weight: 600; }}
details.log pre {{ background: #0f1113; border: 1px solid #262a30; border-radius: 8px;
                   padding: 1rem; font-size: 0.75rem; overflow-x: auto; color: #9ba3ad;
                   max-height: 70vh; overflow-y: auto; }}
code {{ color: #e0a23e; }}
</style></head><body>
<h1>Osmo Offload — {html.escape(title)}</h1>
<div class="sub">{html.escape(report.camera)} · {when} · {report.seconds:.0f}s</div>
<div class="summary"><b>{html.escape(report.summary_line())}</b>
{f"<br>Resolutions — {html.escape(res)}" if res else ""}</div>
{notes_html}
{table}
{log_html}
</body></html>"""
    html_path = out_dir / f"{base}.html"
    html_path.write_text(doc, encoding="utf-8")
    return html_path, csv_path
