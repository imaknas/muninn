"""Plans live in <data>/domains/<id>/plan.yaml; each domain's tracker rows in a
sibling file (plan key `tracker`, default applications.yaml). Writes go through
ruamel round-trip so the human comments in those files survive. An archived
domain is moved, whole, to <data>/domains/.archived/<id>/."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from muninn.core.models import DomainPlan
from muninn.core.plan import PlanError, parse_plan

log = logging.getLogger(__name__)


def _yaml() -> YAML:
    y = YAML()  # round-trip
    y.preserve_quotes = True
    y.width = 4096
    return y


def _stamp(path: Path) -> tuple[int, int] | None:
    """Which version of the file this is, without reading it; None when there is no such file."""
    try:
        s = path.stat()
    except OSError:
        return None
    return s.st_mtime_ns, s.st_size


class YamlPlanRepo:
    def __init__(self, domains_dir: Path, default_timezone: str):
        self._dir = Path(domains_dir)
        self._tz = default_timezone
        # a plan as last parsed, with the files it came from: parsing YAML is by far the slowest thing a screen's read
        # does and one read asks for the plans many times, while the files (changed by any process) rarely change
        self._parsed: dict[Path, tuple[tuple[Any, Any], Path, DomainPlan]] = {}

    def _domain_dirs(self) -> list[Path]:
        if not self._dir.is_dir():
            return []
        return sorted(p for p in self._dir.iterdir() if (p / "plan.yaml").is_file())

    def _tracker_path(self, d: Path, raw: dict[str, Any]) -> Path:
        return d / str(raw.get("tracker") or "applications.yaml")

    def load(self) -> list[DomainPlan]:
        plans = []
        for d in self._domain_dirs():
            try:
                plans.append(self._load_plan(d))
            except (PlanError, OSError, ValueError, KeyError) as e:
                # One broken plan must not silence the others.
                log.error("skipping plan %s: %s", d.name, e)
        return plans

    def _load_plan(self, d: Path) -> DomainPlan:
        kept = self._parsed.get(d)
        if kept is not None and kept[0] == (_stamp(d / "plan.yaml"), _stamp(kept[1])):
            return kept[2]
        plan_stamp = _stamp(d / "plan.yaml")  # taken before reading: a change meanwhile is read again next time
        raw = _yaml().load((d / "plan.yaml").read_text()) or {}
        tracker_path = self._tracker_path(d, raw)
        tracker_stamp = _stamp(tracker_path)
        rows = (_yaml().load(tracker_path.read_text()) or []) if tracker_path.is_file() else []
        plan = parse_plan(raw, rows, self._tz)
        self._parsed[d] = ((plan_stamp, tracker_stamp), tracker_path, plan)
        return plan

    def set_tracker_status(
        self, domain: str, target: str, status: str, applied: date | None, *, replace_applied: bool = False
    ) -> dict[str, Any]:
        d = self._dir / domain
        raw = _yaml().load((d / "plan.yaml").read_text()) or {}
        path = self._tracker_path(d, raw)
        y = _yaml()
        rows = (y.load(path.read_text()) if path.is_file() else None) or []
        row = next((r for r in rows if str(r.get("target")) == target), None)
        if row is None:
            raise KeyError(f"{domain}: no tracker row for {target}")
        before = {"status": str(row.get("status", "todo")), "applied": str(row["applied"]) if row.get("applied") else None}
        row["status"] = status
        if replace_applied:
            if applied is None:
                row.pop("applied", None)
            else:
                row["applied"] = applied
        elif applied is not None and not row.get("applied"):
            row["applied"] = applied
        self._atomic_dump(y, rows, path)
        return before

    def edit(self, domain: str, edit: Any) -> Any:
        d = self._dir / domain
        y = _yaml()
        plan_path = d / "plan.yaml"
        doc = y.load(plan_path.read_text()) or {}
        tracker = self._tracker_path(d, doc)
        rows = (y.load(tracker.read_text()) if tracker.is_file() else None) or []
        inverse = edit.apply(doc, rows)
        parse_plan(doc, rows, self._tz)  # never write a plan that no longer parses
        self._atomic_dump(y, doc, plan_path)
        self._atomic_dump(y, rows, tracker)
        return inverse

    def create(self, doc: dict[str, Any]) -> str:
        domain = str((doc.get("domain") or {}).get("id") or "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", domain):
            raise ValueError(f"a domain id is lowercase letters, digits and dashes: {domain!r}")
        parse_plan(doc, [], self._tz)  # never write a plan that does not parse
        d, archived = self._dir / domain, self._dir / ".archived" / domain
        if d.exists() or archived.exists():
            raise ValueError(f"there is already a domain {domain}")
        d.mkdir(parents=True)
        os.chmod(d, 0o700)
        self._atomic_dump(_yaml(), doc, d / "plan.yaml")
        return domain

    def archive(self, domain: str) -> None:
        d = self._dir / domain
        if not (d / "plan.yaml").is_file():
            raise KeyError(f"no domain {domain}")
        (self._dir / ".archived").mkdir(exist_ok=True)
        os.replace(d, self._dir / ".archived" / domain)

    def restore(self, domain: str) -> None:
        archived = self._dir / ".archived" / domain
        if not archived.is_dir():
            raise KeyError(f"no archived domain {domain}")
        if (self._dir / domain).exists():
            raise ValueError(f"there is already a domain {domain}")
        os.replace(archived, self._dir / domain)

    @staticmethod
    def _atomic_dump(y: YAML, data: Any, path: Path) -> None:
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(fd, "w") as f:
                y.dump(data, f)
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        except BaseException:
            os.unlink(tmp)
            raise
