from __future__ import annotations

import re

from app.connectors.base import Connector

_VERSION_RE = re.compile(r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$")


class ConnectorRegistry:
    """In-memory connector registry with explicit, immutable version selection."""

    def __init__(self) -> None:
        self._connectors: dict[str, dict[str, Connector]] = {}

    def register(self, connector: Connector) -> None:
        descriptor = connector.descriptor
        _version_key(descriptor.version)
        versions = self._connectors.setdefault(descriptor.connector_id, {})
        if descriptor.version in versions:
            raise ValueError(
                f"Connector {descriptor.connector_id!r} version {descriptor.version!r} is already registered."
            )
        versions[descriptor.version] = connector

    def resolve(self, connector_id: str, version: str | None = None) -> Connector:
        versions = self._connectors.get(connector_id)
        if not versions:
            raise KeyError(f"Unknown connector: {connector_id}")
        selected_version = version or max(versions, key=_version_key)
        try:
            return versions[selected_version]
        except KeyError as exc:
            raise KeyError(f"Unknown connector version: {connector_id}@{selected_version}") from exc

    def list_versions(self, connector_id: str) -> list[str]:
        versions = self._connectors.get(connector_id, {})
        return sorted(versions, key=_version_key)

    def manifest(self) -> list[dict[str, object]]:
        descriptors = [
            connector.descriptor.model_dump(mode="json")
            for connector_id in sorted(self._connectors)
            for connector in self._connectors[connector_id].values()
        ]
        return sorted(descriptors, key=lambda item: (str(item["connector_id"]), _version_key(str(item["version"]))))


def _version_key(version: str) -> tuple[int, int, int]:
    match = _VERSION_RE.fullmatch(version)
    if match is None:
        raise ValueError(f"Connector version must use MAJOR.MINOR.PATCH: {version!r}")
    return tuple(int(match.group(name)) for name in ("major", "minor", "patch"))
