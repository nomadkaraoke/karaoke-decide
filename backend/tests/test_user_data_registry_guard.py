"""Guard: every Firestore collection named in decide's code is classified for account deletion.

A collection must be in ``USER_DATA_STORES`` (``delete_all_user_data`` erases it) or in
``NOT_USER_DATA`` (global or keyed by something other than the user). A new per-user collection
that is in neither fails here, so it can't ship without being added to account deletion.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

from backend.services.user_deletion import NOT_USER_DATA, USER_DATA_STORES

ROOT = Path(__file__).resolve().parents[2]
SCANNED = (ROOT / "backend", ROOT / "karaoke_decide")

# FirestoreService (and TransactionView) methods whose first argument is a collection name.
FS_METHODS = frozenset(
    {
        "collection",
        "get_document",
        "set_document",
        "create_document",
        "update_document",
        "delete_document",
        "query_documents",
        "count_documents",
        "batch_set",
        "batch_delete",
        "get_many",
        "delete_document_atomically",
        "transform_document_atomically",
    }
)
# ``get`` / ``set`` / ``delete`` are also dict methods: only count them on a transaction receiver.
TX_METHODS = frozenset({"get", "set", "delete"})
TX_NAMES = frozenset({"tx", "txn", "transaction"})


def _source_files() -> Iterator[Path]:
    for base in SCANNED:
        for path in base.rglob("*.py"):
            if "tests" not in path.relative_to(base).parts:
                yield path


def _str(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _collection_literals(tree: ast.AST) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = _str(node.value)
            if value is not None and any(isinstance(t, ast.Name) and "COLLECTION" in t.id.upper() for t in targets):
                yield value
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            on_tx = isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in TX_NAMES
            if node.args and (name in FS_METHODS or (name in TX_METHODS and on_tx)):
                first = _str(node.args[0])
                if first is not None:
                    yield first
            for kw in node.keywords:
                if kw.arg == "collection" and _str(kw.value) is not None:
                    yield str(_str(kw.value))


def _all_collections() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in _source_files():
        for name in _collection_literals(ast.parse(path.read_text(), filename=str(path))):
            found.setdefault(name, set()).add(str(path.relative_to(ROOT)))
    return found


def test_scanner_finds_known_collections() -> None:
    """Not vacuous: constants, inline literals, keyword and transaction forms are all seen."""
    found = _all_collections()
    for name in ("decide_users", "user_songs", "voice_sessions", "playlists", "sync_jobs", "voice_pool_fetch"):
        assert name in found, name


def test_guard_every_collection_classified() -> None:
    registry = {s.collection for s in USER_DATA_STORES}
    unclassified = {n: sorted(p) for n, p in _all_collections().items() if n not in registry | NOT_USER_DATA}
    assert not unclassified, (
        "Firestore collections missing from backend/services/user_deletion.py: add each to USER_DATA_STORES "
        f"(per-user data, erased on account deletion) or NOT_USER_DATA (with the reason): {unclassified}"
    )


def test_registry_and_allowlist_are_disjoint_and_unique() -> None:
    names = [s.collection for s in USER_DATA_STORES]
    assert len(names) == len(set(names))
    assert not set(names) & NOT_USER_DATA
