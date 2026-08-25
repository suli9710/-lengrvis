"""Immutable report profile used by formal real-LLM release evaluation."""

from __future__ import annotations

import json
import os
import stat
import uuid
from argparse import Namespace
from pathlib import Path
from typing import Any

from scripts.real_llm_evidence_schema import (
    MAX_REPORT_BYTES,
    RELEASE_QUALITY_PROFILE,
)


def validate_release_evidence_profile(
    args: Namespace,
    *,
    default_report_dir: Path,
) -> None:
    if not args.release_evidence:
        return
    if not args.quality_gate:
        raise SystemExit("--release-evidence requires --quality-gate")
    report_dir = Path(args.report_dir).resolve(strict=False)
    if report_dir != default_report_dir.resolve(strict=False):
        raise SystemExit(
            "--release-evidence requires the default versioned report directory"
        )
    changed = [
        name
        for name, expected in RELEASE_QUALITY_PROFILE.items()
        if getattr(args, name) != expected
    ]
    if changed:
        raise SystemExit(
            "--release-evidence forbids release-profile overrides: "
            + ", ".join(sorted(changed))
        )


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(file_stat.st_mode) or bool(
        getattr(file_stat, "st_file_attributes", 0) & reparse_attribute
    )


def ensure_safe_directory(directory: Path, *, trusted_root: Path) -> None:
    try:
        relative = directory.absolute().relative_to(trusted_root.absolute())
    except ValueError as exc:
        raise SystemExit(
            "formal report directory must stay within the repository"
        ) from exc

    current = trusted_root.absolute()
    for part in ("", *relative.parts):
        if part:
            current /= part
        try:
            current_stat = current.lstat()
        except FileNotFoundError:
            try:
                current.mkdir()
                current_stat = current.lstat()
            except OSError as exc:
                raise SystemExit(
                    f"unable to create formal report directory safely: {exc}"
                ) from exc
        except OSError as exc:
            raise SystemExit(
                f"unable to inspect formal report directory: {exc}"
            ) from exc
        if _is_reparse_point(current_stat) or not stat.S_ISDIR(current_stat.st_mode):
            raise SystemExit(
                "formal report directory must not contain symlinks or reparse points"
            )


def _regular_file_identity(path: Path) -> os.stat_result:
    file_stat = path.lstat()
    if _is_reparse_point(file_stat) or not stat.S_ISREG(file_stat.st_mode):
        raise SystemExit("formal real-LLM report must be a regular file")
    return file_stat


def write_report(
    report_path: Path,
    report: dict[str, Any],
    *,
    exclusive: bool,
    trusted_root: Path | None = None,
) -> None:
    encoded = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_REPORT_BYTES:
        raise SystemExit(f"real-LLM report exceeds {MAX_REPORT_BYTES} bytes")
    if not exclusive:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_bytes(encoded)
        return

    root = report_path.parent if trusted_root is None else trusted_root
    ensure_safe_directory(report_path.parent, trusted_root=root)
    temporary = report_path.with_name(f".{report_path.name}.{uuid.uuid4().hex}.tmp")
    created_temporary = False
    published = False
    try:
        with temporary.open("xb") as report_file:
            created_temporary = True
            report_file.write(encoded)
            report_file.flush()
            os.fsync(report_file.fileno())
        temporary_stat = _regular_file_identity(temporary)
        try:
            os.link(temporary, report_path)
            published = True
        except FileExistsError as exc:
            raise SystemExit(
                f"refusing to overwrite formal real-LLM report: {report_path}"
            ) from exc
        except OSError as exc:
            raise SystemExit(
                f"unable to publish formal real-LLM report atomically: {exc}"
            ) from exc
        final_stat = _regular_file_identity(report_path)
        if not os.path.samestat(temporary_stat, final_stat):
            raise SystemExit(
                "formal real-LLM report must be the newly published regular file"
            )
        ensure_safe_directory(report_path.parent, trusted_root=root)
    except BaseException:
        if published:
            try:
                final_stat = report_path.lstat()
                temporary_stat = temporary.lstat()
                if os.path.samestat(final_stat, temporary_stat):
                    report_path.unlink()
            except OSError:
                pass
        raise
    finally:
        if created_temporary:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
