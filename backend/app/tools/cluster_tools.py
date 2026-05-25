from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core import db
from app.core.paths import resolve_authorized
from app.indexer.clustering import cluster_texts, hashing_vectorize, kmeans
from app.indexer.embedding_service import embed_texts_sync
from app.indexer.ocr_service import IMAGE_EXTENSIONS
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition

_VALID_CLUSTER_BY = {"auto", "scene", "people", "time", "location"}


_GENERIC_CATEGORIES = {
    "media": {".mp4", ".mov", ".mkv", ".mp3", ".wav", ".png", ".jpg", ".jpeg", ".gif"},
    "documents": {".pdf", ".docx", ".doc", ".txt", ".md", ".rtf"},
    "spreadsheets": {".xlsx", ".xls", ".csv"},
    "slides": {".pptx", ".ppt", ".key"},
    "archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "code": {".py", ".ts", ".tsx", ".js", ".cpp", ".go", ".rs", ".java"},
    "installers": {".msi", ".exe", ".dmg"},
}


def _category_for_extension(extension: str) -> str:
    extension = extension.lower()
    for name, members in _GENERIC_CATEGORIES.items():
        if extension in members:
            return name
    return "other"


def _iter_indexed_files(context: dict[str, Any]):
    try:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT id, normalized_path AS path, name, extension, size FROM indexed_files LIMIT 2000"
            ).fetchall()
        if rows:
            for row in rows:
                yield {
                    "id": row["id"],
                    "path": row["path"],
                    "name": row["name"],
                    "extension": row["extension"],
                    "size": row["size"],
                }
            return
    except Exception:
        pass
    # Fallback: walk authorized directories when nothing is indexed yet.
    for base in context.get("allowed_directories") or []:
        try:
            root = resolve_authorized(base, list(context.get("allowed_directories") or []))
        except Exception:
            continue
        if root.is_file():
            yield _row_from_path(root)
            continue
        for path in root.rglob("*"):
            if path.is_file():
                yield _row_from_path(path)


def _row_from_path(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "id": str(path),
        "path": str(path),
        "name": path.name,
        "extension": path.suffix.lower(),
        "size": stat.st_size,
        "modified_at": stat.st_mtime,
    }


def cluster_by_content(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    k = int(args.get("k") or 0) or None
    files = list(_iter_indexed_files(context))
    if not files:
        return {"ok": True, "clusters": [], "count": 0}
    labels = [f"{row.get('name', '')} {row.get('extension', '')}" for row in files]
    groups = cluster_texts(labels, k=k)
    cluster_payload = []
    for cluster_id, indices in groups.items():
        members = [files[i] for i in indices]
        cluster_payload.append(
            {
                "cluster_id": cluster_id,
                "size": len(members),
                "preview": [member.get("path") for member in members[:3]],
                "suggested_name": _suggest_cluster_name(members),
            }
        )
    cluster_payload.sort(key=lambda c: -c["size"])
    return {"ok": True, "clusters": cluster_payload, "count": len(cluster_payload)}


def cluster_apps(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    from app.tools import app_tools

    listing = app_tools.list_installed({}, context)
    apps = listing.get("apps") or []
    buckets: dict[str, list[dict[str, Any]]] = {}
    for app in apps:
        category = _category_from_app(app)
        buckets.setdefault(category, []).append(app)
    clusters = [
        {"category": category, "count": len(items), "apps": items[:8]} for category, items in buckets.items()
    ]
    clusters.sort(key=lambda item: -item["count"])
    return {"ok": True, "clusters": clusters, "total": len(apps)}


def cluster_images(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    from app.tools import vision_tools

    limit = _int_arg(args.get("limit"), default=500)
    cluster_by = str(args.get("cluster_by") or "auto").strip().lower()
    if cluster_by not in _VALID_CLUSTER_BY:
        cluster_by = "auto"

    image_paths = list(_iter_image_files(args, context))[:limit]
    if not image_paths:
        return {"ok": True, "clusters": [], "count": 0, "total": 0}

    profiles: list[dict[str, Any]] = []
    for image_path in image_paths:
        profile = vision_tools.describe_image({"path": str(image_path)}, context)
        if not profile.get("ok"):
            metadata = vision_tools.extract_image_metadata(image_path)
            profile = {
                "ok": True,
                "path": str(image_path),
                "description": "",
                "tags": ["image"],
                "structured_labels": vision_tools.structure_image_labels("", metadata),
                "metadata": metadata,
            }
        profiles.append(profile)

    # Dispatch to dimension-specific clustering when requested
    if cluster_by == "time":
        return _time_based_clustering(args, profiles)
    if cluster_by == "location":
        return _location_based_clustering(args, profiles)

    label_texts = [vision_tools.image_label_text(profile) for profile in profiles]
    embedder = args.get("embedder") or context.get("embedder")
    try:
        semantic_vectors = embed_texts_sync(label_texts, embedder=embedder)
    except Exception:
        semantic_vectors = hashing_vectorize(label_texts, dim=64)
    if len(semantic_vectors) != len(profiles):
        semantic_vectors = hashing_vectorize(label_texts, dim=64)

    # Adjust weights and k based on cluster_by dimension
    if cluster_by == "scene":
        metadata_weight = _float_arg(args.get("metadata_weight"), default=0.2)
    elif cluster_by == "people":
        metadata_weight = _float_arg(args.get("metadata_weight"), default=2.0)
    else:
        metadata_weight = _float_arg(args.get("metadata_weight"), default=1.0)

    vectors = _combine_image_vectors(
        semantic_vectors, profiles, metadata_weight=metadata_weight, cluster_by=cluster_by,
    )

    if cluster_by == "people":
        people_values = set()
        for profile in profiles:
            labels = profile.get("structured_labels") or {}
            people_values.add(_safe_float(labels.get("people_count")) or 0.0)
        target_k = _int_arg(args.get("k"), default=0) or max(1, min(8, len(people_values)))
    else:
        target_k = _int_arg(args.get("k"), default=0) or max(1, min(8, len(profiles) // 3 or 2))

    assignments = kmeans(vectors, target_k)

    groups: dict[int, list[int]] = {}
    for index, cluster_id in enumerate(assignments):
        groups.setdefault(cluster_id, []).append(index)

    clusters = []
    for cluster_id, indices in groups.items():
        members = [profiles[index] for index in indices]
        clusters.append(
            {
                "cluster_id": cluster_id,
                "size": len(members),
                "suggested_name": _generate_cluster_name(members),
                "preview": [member.get("path") for member in members[:3]],
                "images": [_image_cluster_member(member) for member in members],
            }
        )
    clusters.sort(key=lambda cluster: (-cluster["size"], str(cluster["suggested_name"])))
    return {
        "ok": True,
        "clusters": clusters,
        "count": len(clusters),
        "total": len(profiles),
        "method": "semantic_label_embedding_with_metadata",
        "metadata_weight": metadata_weight,
        "cluster_by": cluster_by,
    }


def suggest_folder_structure(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    files = list(_iter_indexed_files(context))
    suggestions: dict[str, list[str]] = {}
    for row in files:
        category = _category_for_extension(row.get("extension") or "")
        suggestions.setdefault(category, []).append(row.get("path") or row.get("name") or "")
    return {
        "ok": True,
        "suggestions": [
            {"folder": category, "count": len(paths), "preview": paths[:5]} for category, paths in suggestions.items()
        ],
    }


def _suggest_cluster_name(members: list[dict[str, Any]]) -> str:
    extensions: dict[str, int] = {}
    for row in members:
        ext = (row.get("extension") or "").lower()
        if ext:
            extensions[ext] = extensions.get(ext, 0) + 1
    if not extensions:
        return "mixed"
    top_ext, _ = max(extensions.items(), key=lambda item: item[1])
    return _category_for_extension(top_ext)


def _category_from_app(app: dict[str, Any]) -> str:
    name = (app.get("name") or "").lower()
    for category, hints in (
        ("development", ("code", "studio", "git", "python", "node", "docker")),
        ("media", ("vlc", "potplayer", "premiere", "spotify", "audacity")),
        ("communication", ("wechat", "qq", "telegram", "slack", "teams", "discord")),
        ("office", ("office", "word", "excel", "powerpoint", "wps", "notion")),
        ("browsers", ("chrome", "edge", "firefox", "brave", "opera")),
        ("games", ("steam", "epic", "battle", "game")),
        ("utilities", ("clean", "manager", "utility", "compress", "rar", "zip")),
    ):
        if any(hint in name for hint in hints):
            return category
    return "other"


def _iter_image_files(args: dict[str, Any], context: dict[str, Any]):
    requested = _requested_image_paths(args, context)
    if requested:
        yield from requested
        return

    yielded = False
    try:
        placeholders = ",".join("?" for _ in IMAGE_EXTENSIONS)
        with db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT normalized_path AS path
                FROM indexed_files
                WHERE lower(extension) IN ({placeholders})
                LIMIT 2000
                """,
                tuple(sorted(IMAGE_EXTENSIONS)),
            ).fetchall()
        for row in rows:
            path = Path(row["path"])
            if path.exists() and path.suffix.lower() in IMAGE_EXTENSIONS:
                yielded = True
                yield path
    except Exception:
        pass
    if yielded:
        return

    for base in context.get("allowed_directories") or []:
        try:
            root = resolve_authorized(base, list(context.get("allowed_directories") or []))
        except Exception:
            continue
        if root.is_file() and root.suffix.lower() in IMAGE_EXTENSIONS:
            yield root
            continue
        if root.is_dir():
            for path in root.rglob("*"):
                if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
                    yield path


def _requested_image_paths(args: dict[str, Any], context: dict[str, Any]) -> list[Path]:
    raw_paths = args.get("paths") or args.get("image_paths") or args.get("images")
    if raw_paths is None and args.get("path"):
        raw_paths = [args["path"]]
    if raw_paths is None:
        return []
    if isinstance(raw_paths, (str, Path)):
        raw_paths = [raw_paths]

    allowed = list(context.get("allowed_directories") or [])
    paths: list[Path] = []
    for raw_path in raw_paths:
        try:
            path = resolve_authorized(raw_path, allowed)
        except Exception:
            path = Path(raw_path)
        if path.is_dir():
            paths.extend(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file() and candidate.suffix.lower() in IMAGE_EXTENSIONS
            )
        elif path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            paths.append(path)
    return sorted(dict.fromkeys(paths), key=lambda path: str(path).lower())


def _combine_image_vectors(
    semantic_vectors: list[list[float]],
    profiles: list[dict[str, Any]],
    *,
    metadata_weight: float,
    cluster_by: str = "auto",
) -> list[list[float]]:
    metadata_vectors = _metadata_feature_vectors(profiles, cluster_by=cluster_by)
    combined = []
    for semantic, metadata in zip(semantic_vectors, metadata_vectors, strict=False):
        combined.append([float(value) for value in semantic] + [float(value) * metadata_weight for value in metadata])
    return combined


def _metadata_feature_vectors(
    profiles: list[dict[str, Any]], *, cluster_by: str = "auto",
) -> list[list[float]]:
    timestamps = [_captured_timestamp(profile) for profile in profiles]
    available_times = [timestamp for timestamp in timestamps if timestamp is not None]
    min_time = min(available_times) if available_times else None
    max_time = max(available_times) if available_times else None
    time_range = (max_time - min_time) if min_time is not None and max_time is not None else 0.0

    people_scale = 5.0 if cluster_by == "people" else 1.0

    vectors = []
    for profile, timestamp in zip(profiles, timestamps, strict=False):
        metadata = profile.get("metadata") or {}
        labels = profile.get("structured_labels") or {}
        gps = metadata.get("gps") or (labels.get("metadata") or {}).get("gps") or {}
        latitude = _safe_float(gps.get("latitude")) if isinstance(gps, dict) else None
        longitude = _safe_float(gps.get("longitude")) if isinstance(gps, dict) else None
        if timestamp is not None and min_time is not None and time_range > 0:
            normalized_time = (timestamp - min_time) / time_range
        else:
            normalized_time = 0.0
        people_count = _safe_float(labels.get("people_count")) or 0.0
        vectors.append(
            [
                (latitude / 90.0) if latitude is not None else 0.0,
                (longitude / 180.0) if longitude is not None else 0.0,
                0.1 if latitude is not None and longitude is not None else 0.0,
                normalized_time,
                0.1 if timestamp is not None else 0.0,
                min(people_count, 10.0) / 10.0 * people_scale,
            ]
        )
    return vectors


def _captured_timestamp(profile: dict[str, Any]) -> float | None:
    metadata = profile.get("metadata") or {}
    labels = profile.get("structured_labels") or {}
    raw = metadata.get("captured_at") or (labels.get("metadata") or {}).get("captured_at")
    if not raw:
        return None
    text = str(raw).strip()
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate).timestamp()
        except ValueError:
            continue
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    return None


def _image_cluster_member(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": profile.get("path"),
        "description": profile.get("description") or "",
        "tags": profile.get("tags") or [],
        "structured_labels": profile.get("structured_labels") or {},
        "metadata": profile.get("metadata") or {},
    }


def _suggest_image_cluster_name(members: list[dict[str, Any]]) -> str:
    """Legacy name function -- delegates to _generate_cluster_name."""
    return _generate_cluster_name(members)


def _generate_cluster_name(members: list[dict[str, Any]]) -> str:
    """Generate a descriptive name from the dominant attributes."""
    # Count scene types
    scene_counts: Counter[str] = Counter()
    for member in members:
        labels = member.get("structured_labels") or {}
        scene = str(labels.get("scene_type") or "unknown").lower()
        scene_counts[scene] += 1
    top_scene = scene_counts.most_common(1)[0][0] if scene_counts else "mixed"
    if top_scene == "unknown":
        top_scene = "mixed"

    # Average people count
    people_counts = []
    for member in members:
        labels = member.get("structured_labels") or {}
        people_counts.append(_safe_float(labels.get("people_count")) or 0.0)
    avg_people = sum(people_counts) / max(len(people_counts), 1)

    # Time range
    _MONTH_NAMES = {
        1: "1月", 2: "2月", 3: "3月", 4: "4月", 5: "5月", 6: "6月",
        7: "7月", 8: "8月", 9: "9月", 10: "10月", 11: "11月", 12: "12月",
    }
    time_label = ""
    timestamps: list[datetime] = []
    for member in members:
        ts = _captured_timestamp(member)
        if ts is not None:
            try:
                timestamps.append(datetime.fromtimestamp(ts))
            except (OSError, ValueError, OverflowError):
                pass
    if timestamps:
        month_counts: Counter[tuple[int, int]] = Counter()
        for dt in timestamps:
            month_counts[(dt.year, dt.month)] += 1
        dominant = month_counts.most_common(1)[0][0]
        time_label = f"{dominant[0]}-{_MONTH_NAMES.get(dominant[1], str(dominant[1]))}"

    parts = [top_scene]
    if avg_people >= 2:
        parts.append(f"{int(avg_people)}人")
    if time_label:
        parts.append(time_label)
    return " · ".join(parts)


def _time_based_clustering(
    args: dict[str, Any], profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    """Cluster images by time (month or quarter)."""
    # Extract timestamps and group by month
    month_groups: dict[str, list[int]] = {}
    no_time_indices: list[int] = []
    parsed_months: set[tuple[int, int]] = set()

    for index, profile in enumerate(profiles):
        ts = _captured_timestamp(profile)
        if ts is None:
            no_time_indices.append(index)
            continue
        try:
            dt = datetime.fromtimestamp(ts)
        except (OSError, ValueError, OverflowError):
            no_time_indices.append(index)
            continue
        parsed_months.add((dt.year, dt.month))
        key = f"{dt.year}-{dt.month:02d}"
        month_groups.setdefault(key, []).append(index)

    # If fewer than 3 distinct months, regroup by quarter
    use_quarter = len(parsed_months) < 3 and len(parsed_months) > 0
    if use_quarter:
        quarter_groups: dict[str, list[int]] = {}
        for index, profile in enumerate(profiles):
            if index in set(no_time_indices):
                continue
            ts = _captured_timestamp(profile)
            if ts is None:
                continue
            try:
                dt = datetime.fromtimestamp(ts)
            except (OSError, ValueError, OverflowError):
                continue
            quarter = (dt.month - 1) // 3 + 1
            key = f"{dt.year}-Q{quarter}"
            quarter_groups.setdefault(key, []).append(index)
        time_groups = quarter_groups
    else:
        time_groups = month_groups

    # Fallback: if most images lack timestamps, use regular k-means
    if len(no_time_indices) > len(profiles) * 0.7:
        return _fallback_kmeans_clustering(args, profiles, cluster_by="time")

    # Within each time group, optionally sub-cluster with k-means
    cluster_id = 0
    clusters: list[dict[str, Any]] = []

    for time_key, indices in sorted(time_groups.items()):
        sub_groups = _sub_cluster_indices(profiles, indices, args)
        for sub_indices in sub_groups:
            members = [profiles[i] for i in sub_indices]
            clusters.append(
                {
                    "cluster_id": f"time-{time_key}-{cluster_id}",
                    "size": len(members),
                    "suggested_name": f"{time_key} · {_generate_cluster_name(members)}",
                    "preview": [m.get("path") for m in members[:3]],
                    "images": [_image_cluster_member(m) for m in members],
                }
            )
            cluster_id += 1

    # Handle images without timestamps
    if no_time_indices:
        members = [profiles[i] for i in no_time_indices]
        clusters.append(
            {
                "cluster_id": f"time-unknown-{cluster_id}",
                "size": len(members),
                "suggested_name": f"unknown time · {_generate_cluster_name(members)}",
                "preview": [m.get("path") for m in members[:3]],
                "images": [_image_cluster_member(m) for m in members],
            }
        )

    clusters.sort(key=lambda c: (-c["size"], str(c["suggested_name"])))
    return {
        "ok": True,
        "clusters": clusters,
        "count": len(clusters),
        "total": len(profiles),
        "method": "time_based",
        "cluster_by": "time",
    }


def _location_based_clustering(
    args: dict[str, Any], profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    """Cluster images by GPS location using grid-based pre-grouping."""
    grid_groups: dict[str, list[int]] = {}
    no_gps_indices: list[int] = []

    for index, profile in enumerate(profiles):
        metadata = profile.get("metadata") or {}
        labels = profile.get("structured_labels") or {}
        gps = metadata.get("gps") or (labels.get("metadata") or {}).get("gps") or {}
        lat = _safe_float(gps.get("latitude")) if isinstance(gps, dict) else None
        lon = _safe_float(gps.get("longitude")) if isinstance(gps, dict) else None
        if lat is None or lon is None:
            no_gps_indices.append(index)
            continue
        # Round to 0.1 degree (~11km grid cells)
        grid_lat = round(lat, 1)
        grid_lon = round(lon, 1)
        key = f"{grid_lat:.1f},{grid_lon:.1f}"
        grid_groups.setdefault(key, []).append(index)

    # Fallback: if most images lack GPS, use regular k-means
    if len(no_gps_indices) > len(profiles) * 0.7:
        return _fallback_kmeans_clustering(args, profiles, cluster_by="location")

    cluster_id = 0
    clusters: list[dict[str, Any]] = []

    for grid_key, indices in sorted(grid_groups.items()):
        sub_groups = _sub_cluster_indices(profiles, indices, args)
        for sub_indices in sub_groups:
            members = [profiles[i] for i in sub_indices]
            clusters.append(
                {
                    "cluster_id": f"loc-{grid_key}-{cluster_id}",
                    "size": len(members),
                    "suggested_name": f"GPS({grid_key}) · {_generate_cluster_name(members)}",
                    "preview": [m.get("path") for m in members[:3]],
                    "images": [_image_cluster_member(m) for m in members],
                }
            )
            cluster_id += 1

    # Handle images without GPS
    if no_gps_indices:
        members = [profiles[i] for i in no_gps_indices]
        clusters.append(
            {
                "cluster_id": f"loc-unknown-{cluster_id}",
                "size": len(members),
                "suggested_name": f"unknown location · {_generate_cluster_name(members)}",
                "preview": [m.get("path") for m in members[:3]],
                "images": [_image_cluster_member(m) for m in members],
            }
        )

    clusters.sort(key=lambda c: (-c["size"], str(c["suggested_name"])))
    return {
        "ok": True,
        "clusters": clusters,
        "count": len(clusters),
        "total": len(profiles),
        "method": "location_based",
        "cluster_by": "location",
    }


def _sub_cluster_indices(
    profiles: list[dict[str, Any]], indices: list[int], args: dict[str, Any],
) -> list[list[int]]:
    """Sub-cluster a group of indices using k-means if the group is large enough."""
    if len(indices) <= 5:
        return [indices]
    # Build simple label vectors for the sub-group
    sub_labels = []
    for i in indices:
        labels = profiles[i].get("structured_labels") or {}
        scene = str(labels.get("scene_type") or "unknown")
        people = str(labels.get("people_count") or 0)
        sub_labels.append(f"{scene} people_{people}")
    sub_vectors = hashing_vectorize(sub_labels, dim=32)
    sub_k = _int_arg(args.get("k"), default=0) or max(1, min(4, len(indices) // 3))
    sub_assignments = kmeans(sub_vectors, sub_k)
    sub_groups: dict[int, list[int]] = {}
    for local_idx, cluster_id in enumerate(sub_assignments):
        sub_groups.setdefault(cluster_id, []).append(indices[local_idx])
    return list(sub_groups.values())


def _fallback_kmeans_clustering(
    args: dict[str, Any], profiles: list[dict[str, Any]], *, cluster_by: str,
) -> dict[str, Any]:
    """Fall back to standard k-means when dimension-specific data is sparse."""
    from app.tools import vision_tools

    label_texts = [vision_tools.image_label_text(profile) for profile in profiles]
    semantic_vectors = hashing_vectorize(label_texts, dim=64)
    metadata_weight = _float_arg(args.get("metadata_weight"), default=1.0)
    vectors = _combine_image_vectors(
        semantic_vectors, profiles, metadata_weight=metadata_weight, cluster_by="auto",
    )
    target_k = _int_arg(args.get("k"), default=0) or max(1, min(8, len(profiles) // 3 or 2))
    assignments = kmeans(vectors, target_k)

    groups: dict[int, list[int]] = {}
    for index, cid in enumerate(assignments):
        groups.setdefault(cid, []).append(index)

    clusters = []
    for cid, indices in groups.items():
        members = [profiles[i] for i in indices]
        clusters.append(
            {
                "cluster_id": cid,
                "size": len(members),
                "suggested_name": _generate_cluster_name(members),
                "preview": [m.get("path") for m in members[:3]],
                "images": [_image_cluster_member(m) for m in members],
            }
        )
    clusters.sort(key=lambda c: (-c["size"], str(c["suggested_name"])))
    return {
        "ok": True,
        "clusters": clusters,
        "count": len(clusters),
        "total": len(profiles),
        "method": f"fallback_kmeans (sparse {cluster_by} data)",
        "cluster_by": cluster_by,
    }


def _int_arg(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_arg(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def register(registry) -> None:
    _image_cluster_schema = {
        "type": "object",
        "properties": {
            "cluster_by": {
                "type": "string",
                "enum": ["auto", "scene", "people", "time", "location"],
                "description": (
                    "Clustering dimension. 'auto' uses combined semantic+metadata (default). "
                    "'scene' emphasises visual scene similarity. "
                    "'people' groups by number of people. "
                    "'time' groups by capture month/quarter. "
                    "'location' groups by GPS grid cells (~11km)."
                ),
                "default": "auto",
            },
            "k": {
                "type": "integer",
                "description": "Target number of clusters (0 = auto-detect).",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of images to process.",
                "default": 500,
            },
            "metadata_weight": {
                "type": "number",
                "description": "Weight for metadata features vs semantic embeddings.",
            },
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific image paths or directories to cluster.",
            },
        },
    }
    defs = [
        ("file.cluster_by_content", cluster_by_content, {}),
        ("app.cluster_installed", cluster_apps, {}),
        ("image.cluster", cluster_images, _image_cluster_schema),
        ("image.cluster_images", cluster_images, _image_cluster_schema),
        ("file.suggest_folder_structure", suggest_folder_structure, {}),
    ]
    for name, fn, schema in defs:
        registry.register(
            ToolDefinition(
                name=name,
                description=name.replace(".", " "),
                input_schema=schema,
                output_schema={},
                risk_level=RiskLevel.R0_READ_ONLY,
                agent_owner="AppAgent" if name.startswith("app.") else "FileAgent",
                supports_dry_run=False,
                requires_authorized_path=True,
                execute=fn,
            )
        )
