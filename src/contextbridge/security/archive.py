"""ZIP archive safety verification against ZIP Slip, ZIP bombs, and unsafe members."""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path, PurePosixPath

from contextbridge.core.exceptions import ArchiveSecurityError, PathTraversalError
from contextbridge.security.limits import DEFAULT_LIMITS, SecurityLimits
from contextbridge.security.paths import ensure_within_root, validate_relative_path


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    """Detect POSIX symlink mode bits stored in ZIP external_attr."""
    unix_mode = info.external_attr >> 16
    return stat.S_ISLNK(unix_mode)


def inspect_zip_safety(
    zip_path: Path,
    *,
    limits: SecurityLimits = DEFAULT_LIMITS,
) -> list[PurePosixPath]:
    """Verify a ZIP archive against ZIP Slip, ZIP bombs, and symlink entries.

    Returns the validated relative paths of regular file entries.
    """
    if not zip_path.exists() or not zip_path.is_file():
        raise ArchiveSecurityError(f"Archive does not exist: {zip_path}")

    try:
        zf = zipfile.ZipFile(zip_path, "r")
    except zipfile.BadZipFile as exc:
        raise ArchiveSecurityError(f"Invalid or corrupted ZIP archive: {zip_path}") from exc

    validated_entries: list[PurePosixPath] = []
    seen_casefolded: dict[str, str] = {}
    total_uncompressed = 0

    with zf:
        infos = zf.infolist()
        if len(infos) > limits.max_zip_entries:
            raise ArchiveSecurityError(
                f"ZIP archive entry count ({len(infos)}) exceeds maximum "
                f"({limits.max_zip_entries})."
            )

        for info in infos:
            if _is_zip_symlink(info):
                raise ArchiveSecurityError(
                    "ZIP archive contains a symbolic link entry, which is forbidden: "
                    f"{info.filename!r}"
                )

            # Skip pure directory entries after still validating their path for ZIP Slip
            raw_name = info.filename
            trimmed = raw_name.rstrip("/")
            if trimmed:
                try:
                    rel_path = validate_relative_path(trimmed)
                except PathTraversalError as exc:
                    raise ArchiveSecurityError(
                        f"ZIP Slip / unsafe member path blocked: {raw_name!r}"
                    ) from exc
            else:
                raise ArchiveSecurityError(f"Unsafe empty or root archive entry: {raw_name!r}")

            if info.is_dir():
                continue

            folded = rel_path.as_posix().lower()
            if folded in seen_casefolded:
                raise ArchiveSecurityError(
                    "ZIP archive contains case-insensitive path collision between "
                    f"{seen_casefolded[folded]!r} and {rel_path.as_posix()!r}."
                )
            seen_casefolded[folded] = rel_path.as_posix()

            if info.file_size > limits.max_single_file_bytes:
                raise ArchiveSecurityError(
                    f"ZIP member {raw_name!r} uncompressed size ({info.file_size} bytes) "
                    f"exceeds maximum ({limits.max_single_file_bytes} bytes)."
                )

            total_uncompressed += info.file_size
            if total_uncompressed > limits.max_zip_uncompressed_bytes:
                raise ArchiveSecurityError(
                    f"ZIP total uncompressed size exceeds maximum "
                    f"({limits.max_zip_uncompressed_bytes} bytes); possible ZIP bomb."
                )

            if info.compress_size > 0:
                ratio = info.file_size / info.compress_size
                if ratio > limits.max_zip_compression_ratio and info.file_size > 65_536:
                    raise ArchiveSecurityError(
                        f"ZIP member {raw_name!r} compression ratio ({ratio:.1f}x) exceeds "
                        f"maximum ({limits.max_zip_compression_ratio:.1f}x); possible ZIP bomb."
                    )
            elif info.file_size > 0:
                raise ArchiveSecurityError(
                    f"ZIP member {raw_name!r} reports zero compressed size with non-zero payload."
                )

            validated_entries.append(rel_path)

    return validated_entries


def safe_extract_zip(
    zip_path: Path,
    destination: Path,
    *,
    limits: SecurityLimits = DEFAULT_LIMITS,
) -> list[Path]:
    """Safely extract a validated ZIP inside `destination`, enforcing byte limits at read time."""
    inspect_zip_safety(zip_path, limits=limits)
    destination.mkdir(parents=True, exist_ok=True)
    resolved_dest = destination.resolve()
    extracted: list[Path] = []
    total_written = 0

    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel_path = validate_relative_path(info.filename)
            target_file = resolved_dest.joinpath(*rel_path.parts)
            ensure_within_root(resolved_dest, target_file)
            target_file.parent.mkdir(parents=True, exist_ok=True)

            file_written = 0
            with zf.open(info, "r") as src, target_file.open("wb") as dst:
                while chunk := src.read(65_536):
                    file_written += len(chunk)
                    total_written += len(chunk)
                    if file_written > limits.max_single_file_bytes:
                        raise ArchiveSecurityError(
                            f"Actual extracted bytes for {info.filename!r} exceeded "
                            "single-file limit."
                        )
                    if total_written > limits.max_zip_uncompressed_bytes:
                        raise ArchiveSecurityError(
                            "Actual total extracted bytes exceeded archive limit; "
                            "possible ZIP bomb."
                        )
                    dst.write(chunk)
            extracted.append(target_file)

    return extracted
