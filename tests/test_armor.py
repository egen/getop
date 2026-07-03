"""Model Armor filter builder and filter-result parsing."""

from types import SimpleNamespace

from geadm.commands import armor


def test_armor_filter_default_matched_only():
    f = armor.armor_filter("proj", "24h", matched_only=True)
    assert 'logName="projects/proj/logs/modelarmor.googleapis.com%2Fsanitize_operations"' in f
    assert 'filterMatchState="MATCH_FOUND"' in f
    assert "timestamp>=" in f


def test_armor_filter_all_omits_match_clause():
    f = armor.armor_filter("proj", "24h", matched_only=False)
    assert "filterMatchState" not in f


def test_matched_filters_jailbreak_and_rai_subtypes():
    filter_results = {
        "pi_and_jailbreak": {
            "piAndJailbreakFilterResult": {"matchState": "MATCH_FOUND", "confidenceLevel": "HIGH"}
        },
        "rai": {
            "raiFilterResult": {
                "matchState": "MATCH_FOUND",
                "raiFilterTypeResults": {
                    "dangerous": {"matchState": "MATCH_FOUND", "confidenceLevel": "HIGH"},
                    "harassment": {"matchState": "NO_MATCH_FOUND"},
                },
            }
        },
        "csam": {"csamFilterFilterResult": {"matchState": "NO_MATCH_FOUND"}},
        "malicious_uris": {"maliciousUriFilterResult": {"matchState": "NO_MATCH_FOUND"}},
    }
    matched = armor._matched_filters(filter_results)
    assert "pi_and_jailbreak(HIGH)" in matched
    assert "rai:dangerous(HIGH)" in matched
    assert not any("harassment" in m for m in matched)  # only matched sub-types
    assert not any(m.startswith("csam") for m in matched)


def test_matched_filters_empty_when_clean():
    assert armor._matched_filters({"csam": {"csamFilterFilterResult": {"matchState": "NO_MATCH_FOUND"}}}) == []
    assert armor._matched_filters(None) == []


def test_normalize_maps_direction_and_content():
    entry = SimpleNamespace(
        payload={
            "operationType": "SANITIZE_USER_PROMPT",
            "sanitizationInput": {"text": "bad prompt"},
            "sanitizationResult": {
                "filterMatchState": "MATCH_FOUND",
                "filterResults": {
                    "pi_and_jailbreak": {
                        "piAndJailbreakFilterResult": {
                            "matchState": "MATCH_FOUND",
                            "confidenceLevel": "HIGH",
                        }
                    }
                },
            },
        },
        timestamp=None,
        insert_id="x",
        resource=SimpleNamespace(labels={"template_id": "tmpl", "location": "us"}),
    )
    row = armor._normalize(entry)
    assert row["direction"] == "prompt"
    assert row["match_state"] == "MATCH_FOUND"
    assert row["matched_filters"] == ["pi_and_jailbreak(HIGH)"]
    assert row["content"] == "bad prompt"
    assert row["template"] == "tmpl"


def test_normalize_response_direction():
    entry = SimpleNamespace(
        payload={"operationType": "SANITIZE_MODEL_RESPONSE", "sanitizationResult": {}},
        timestamp=None,
        insert_id="y",
        resource=SimpleNamespace(labels={}),
    )
    assert armor._normalize(entry)["direction"] == "response"
