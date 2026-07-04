"""Source-level guard encoding the read-only design guarantee.

Mirrors the readonly-auditor: no mutating RPC verbs, no non-GET HTTP,
no key-file auth, and clients constructed only in getop/auth.py.
"""

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "getop"

MUTATING_CALL = re.compile(
    r"\.(create|update|patch|delete|purge|import|set_iam)_[a-z_]*\s*\("
)
NON_GET_HTTP = re.compile(r"\.(post|put|patch|delete)\s*\(|session\.request\s*\(")
KEY_FILE_AUTH = re.compile(r"from_service_account|service_account\.Credentials")
CLIENT_CONSTRUCTION = re.compile(
    r"(ServiceClient|AuthorizedSession|logging_v2\.Client|MetricServiceClient)\s*\("
)


def _sources() -> dict[Path, str]:
    return {path: path.read_text() for path in sorted(PACKAGE.rglob("*.py"))}


def test_no_mutating_rpc_calls():
    for path, text in _sources().items():
        assert not MUTATING_CALL.search(text), f"mutating RPC call in {path}"


def test_no_non_get_http():
    for path, text in _sources().items():
        assert not NON_GET_HTTP.search(text), f"non-GET HTTP call in {path}"


def test_no_key_file_auth():
    for path, text in _sources().items():
        assert not KEY_FILE_AUTH.search(text), f"key-file auth in {path}"


def test_clients_only_constructed_in_auth():
    for path, text in _sources().items():
        if path.name == "auth.py":
            continue
        assert not CLIENT_CONSTRUCTION.search(text), f"client constructed in {path}"
