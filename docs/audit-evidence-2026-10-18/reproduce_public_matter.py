"""Reproduce public_matter redaction destroying structured interaction identifiers."""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("LENGRVIS_AUDIT_HMAC_SECRET", "review-repro-secret")
os.environ["LENGRVIS_DATA_DIR"] = tempfile.mkdtemp(prefix="lengrvis-public-matter-")

from app.core import butler_store as store  # noqa: E402
from app.core import db  # noqa: E402
from app.core.butler_models import ButlerMatter, MatterInteraction, MatterStatus  # noqa: E402
from app.services.butler_service import public_matter  # noqa: E402

db.init_db()
ask = store.save(
    ButlerMatter(
        goal="book trip",
        status=MatterStatus.WAITING_INPUT,
        interaction=MatterInteraction(message="need origin/date", fields=["origin", "date"]),
    )
)
shown = public_matter(ask)
print("shown_fields=", shown["interaction"]["fields"])

parent = store.save(ButlerMatter(goal="parent task", mode="efficiency"))
child = store.save(ButlerMatter(goal="child task", mode="efficiency"))
placeholder = store.save(
    ButlerMatter(
        goal="parent then child",
        status=MatterStatus.WAITING_INPUT,
        interaction=MatterInteraction(message="choose", kind="choose_dependency"),
        constraints={
            "coordination_request": {
                "parent_goal": parent.goal,
                "child_goal": child.goal,
                "parents": [parent.id],
                "children": [child.id],
                "versions": {parent.id: parent.version, child.id: child.version},
            }
        },
    )
)
shown2 = public_matter(placeholder)
print("shown_parents=", shown2["constraints"]["coordination_request"]["parents"])
print("shown_children=", shown2["constraints"]["coordination_request"]["children"])
print("shown_versions_keys=", list(shown2["constraints"]["coordination_request"]["versions"]))
