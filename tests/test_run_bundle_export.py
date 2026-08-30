import hashlib

from scripts.export_run_bundle import build_archive_manifest, sanitize


def test_run_bundle_redacts_credentials_but_keeps_sandbox_safety_evidence() -> None:
    result = sanitize(
        {
            "api_key": "do-not-export",
            "authorization": "Bearer do-not-export",
            "secret_environment_present": False,
            "capability_token_id": "cap_evidence_id",
        }
    )

    assert result == {
        "api_key": "[REDACTED]",
        "authorization": "[REDACTED]",
        "secret_environment_present": False,
        "capability_token_id": "cap_evidence_id",
    }


def test_run_bundle_v2_manifest_hashes_every_evidence_entry() -> None:
    entries = {
        "run_bundle.json": b'{"bundle_version":"2.0"}',
        "conversation.json": b'{"messages":[]}',
    }

    manifest = build_archive_manifest(entries)

    assert manifest["bundle_version"] == "2.5"
    assert {item["path"] for item in manifest["entries"]} == set(entries)
    for item in manifest["entries"]:
        assert item["sha256"] == hashlib.sha256(entries[item["path"]]).hexdigest()
