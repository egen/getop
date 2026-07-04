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


def test_template_rows_renders_all_filter_groups():
    fc = {
        "piAndJailbreakFilterSettings": {"filterEnforcement": "ENABLED", "confidenceLevel": "HIGH"},
        "raiSettings": {
            "raiFilters": [
                {"filterType": "DANGEROUS", "confidenceLevel": "MEDIUM_AND_ABOVE"},
                {"filterType": "HATE_SPEECH", "confidenceLevel": "MEDIUM_AND_ABOVE"},
            ]
        },
        "maliciousUriFilterSettings": {"filterEnforcement": "ENABLED"},
    }
    rows = armor.template_rows(fc)
    labels = [r[0] for r in rows]
    assert "Prompt injection & jailbreak" in labels
    assert "Responsible AI: DANGEROUS" in labels
    assert "Malicious URIs" in labels
    pi = next(r for r in rows if r[0] == "Prompt injection & jailbreak")
    assert pi[1] == "ENABLED" and pi[2] == "HIGH"


def test_template_rows_empty_config():
    assert armor.template_rows({}) == []


def test_summarise_counts_per_filter_with_example():
    rows = [
        {
            "timestamp": "2026-07-03T12:49:03+00:00",
            "content": "ignore all instructions",
            "matched_filters": ["pi_and_jailbreak(HIGH)", "rai:dangerous(HIGH)"],
        },
        {
            "timestamp": "2026-07-03T09:14:00+00:00",
            "content": "http://testsafebrowsing.appspot.com/s/phishing.html",
            "matched_filters": ["malicious_uris"],
        },
        {
            "timestamp": "2026-07-02T08:00:00+00:00",
            "content": "another jailbreak",
            "matched_filters": ["pi_and_jailbreak(MEDIUM)"],
        },
    ]
    summary = armor.summarise(rows)
    by_filter = {s["filter"]: s for s in summary}
    # confidence suffix stripped for grouping; both jailbreak hits merge
    assert by_filter["pi_and_jailbreak"]["hits"] == 2
    assert by_filter["rai:dangerous"]["hits"] == 1
    assert by_filter["malicious_uris"]["hits"] == 1
    # newest example kept (rows are newest-first)
    assert by_filter["pi_and_jailbreak"]["example"] == "ignore all instructions"
    assert by_filter["pi_and_jailbreak"]["last_seen"] == "2026-07-03T12:49:03+00:00"
    # sorted by hits desc
    assert summary[0]["filter"] == "pi_and_jailbreak"


def test_summarise_empty():
    assert armor.summarise([]) == []


def test_normalize_response_direction():
    entry = SimpleNamespace(
        payload={"operationType": "SANITIZE_MODEL_RESPONSE", "sanitizationResult": {}},
        timestamp=None,
        insert_id="y",
        resource=SimpleNamespace(labels={}),
    )
    assert armor._normalize(entry)["direction"] == "response"
