"""Private-data seam for the x402 enrichment tool inventory.

The mapping of tool IDs to enrichment categories is METHOD, not code
(standards/METHODOLOGY-PROTECTION.md): it describes what we enrich, so it is
supplied by the private deployment. Public code ships the loader only.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("x402.enrich")

CATEGORIES_ENV = "X402_ENRICHMENT_CATEGORIES"
DEFAULT_CATEGORIES_PATH = "/etc/x402/enrichment-categories.json"


def load_enrichable_categories(path: str | None = None) -> dict[str, list[str]]:
    """Load category -> tool-ID inventory from the private path; {} if absent."""
    source = Path(path or os.getenv(CATEGORIES_ENV, DEFAULT_CATEGORIES_PATH))
    if not source.is_file():
        logger.info("enrichment_categories_unavailable path=%s", source)
        return {}
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.error("enrichment_categories_unreadable path=%s error=%s", source, exc)
        return {}
    if not isinstance(raw, dict):
        logger.error("enrichment_categories_invalid_shape path=%s", source)
        return {}
    return {
        str(category): [str(tool) for tool in tools]
        for category, tools in raw.items()
        if isinstance(tools, list)
    }
