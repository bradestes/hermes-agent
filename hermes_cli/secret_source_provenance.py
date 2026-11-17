"""Value-blind provenance lookup for environment names hydrated by external secret sources."""

import sys


def source_supplied_secret_env() -> frozenset[str]:
    """Return source-hydrated names without importing the side-effectful CLI loader."""
    loader = sys.modules.get("hermes_cli.env_loader")
    if loader is None:
        return frozenset()
    names = getattr(loader, "_SOURCE_SUPPLIED_NAMES", ())
    return frozenset(str(name).upper() for name in names)
