from __future__ import annotations

from masking import is_sensitive_key, mask_mapping, mask_secret


def test_mask_secret_preserves_only_tail():
    assert mask_secret("super-secret-value", visible=5).endswith("value")
    assert "super-secret" not in mask_secret("super-secret-value", visible=5)


def test_mask_secret_handles_empty_and_short_values():
    assert mask_secret(None) == ""
    assert mask_secret("") == ""
    assert mask_secret("abc") == "***"


def test_sensitive_key_detection_and_nested_mapping_masking():
    assert is_sensitive_key("GIGACHAT_CLIENT_SECRET")
    assert is_sensitive_key("api-key")
    assert not is_sensitive_key("GIGACHAT_MODEL")

    masked = mask_mapping({
        "GIGACHAT_CLIENT_SECRET": "abcdef123456",
        "GIGACHAT_MODEL": "GigaChat",
        "nested": {"token": "token-value"},
    })

    assert masked["GIGACHAT_CLIENT_SECRET"].endswith("3456")
    assert "abcdef" not in masked["GIGACHAT_CLIENT_SECRET"]
    assert masked["GIGACHAT_MODEL"] == "GigaChat"
    assert masked["nested"]["token"].endswith("alue")
