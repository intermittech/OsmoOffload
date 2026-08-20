"""Session ingest reports: CSV (machines) + HTML (humans), Slate-styled.

Written per transfer session into <base>/<camera>/_reports/. The CSV carries
one row per file (original name, saved-as, size, hash, store, duration,
resolution, verified); the HTML adds the shoot summary up top.
"""

from __future__ import annotations

import csv
import html
import time
from dataclasses import dataclass, field
from pathlib import Path


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


@dataclass
class SessionReport:
    camera: str
    started_at: float
    seconds: float
    rate_mbs: float
    files: list[ReportFile] = field(default_factory=list)

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
        out: dict[str, int] = {}
        for f in self.videos:
            key = f.resolution or "unknown"
            out[key] = out.get(key, 0) + 1
        return out

    def summary_line(self) -> str:
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


def write_reports(report: SessionReport, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S", time.localtime(report.started_at))
    csv_path = out_dir / f"session_{stamp}.csv"
    html_path = out_dir / f"session_{stamp}.html"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["original", "saved_as", "dest_path", "bytes", "blake2b_128",
                    "storage", "kind", "duration_s", "resolution", "verified"])
        for r in report.files:
            w.writerow([r.original, r.saved_as, r.dest_path, r.size, r.hash_hex or "",
                        r.storage or "", r.kind, f"{r.duration_s:.1f}" if r.duration_s else "",
                        r.resolution or "", "yes" if r.verified else "no"])

    res = " · ".join(f"{k}: {v}" for k, v in report.resolution_breakdown().items())
    rows = "\n".join(
        f"<tr><td>{html.escape(r.original)}</td><td>{html.escape(r.saved_as)}</td>"
        f"<td class='num'>{r.size / 1e6:.1f} MB</td><td>{_fmt_dur(r.duration_s)}</td>"
        f"<td>{html.escape(r.resolution or '—')}</td><td>{html.escape(r.storage or '—')}</td>"
        f"<td><code>{html.escape((r.hash_hex or '')[:16])}</code></td>"
        f"<td>{'✔' if r.verified else '✘'}</td></tr>"
        for r in report.files
    )
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(report.started_at))
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Osmo Offload — {html.escape(report.camera)} — {when}</title>
<style>
body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #141619; color: #e9ebee;
       margin: 2.5rem auto; max-width: 60rem; padding: 0 1rem; }}
h1 {{ font-size: 1.3rem; }} .sub {{ color: #9ba3ad; margin-bottom: 1.5rem; }}
.summary {{ background: #212429; border: 1px solid #343a42; border-radius: 10px;
            padding: 1rem 1.2rem; margin-bottom: 1.5rem; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.92rem; }}
th, td {{ text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid #262a30; }}
th {{ color: #9ba3ad; font-weight: 600; }} td.num {{ text-align: right; }}
code {{ color: #e0a23e; }}
</style></head><body>
<h1>Osmo Offload — session report</h1>
<div class="sub">{html.escape(report.camera)} · {when} · {report.seconds:.0f}s</div>
<div class="summary"><b>{html.escape(report.summary_line())}</b>
{f"<br>Resolutions — {html.escape(res)}" if res else ""}</div>
<table><tr><th>Original</th><th>Saved as</th><th>Size</th><th>Length</th>
<th>Resolution</th><th>Store</th><th>Hash</th><th>OK</th></tr>
{rows}</table></body></html>"""
    html_path.write_text(doc, encoding="utf-8")
    return html_path, csv_path
