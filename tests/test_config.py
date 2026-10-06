"""Конфигурация: DRY RUN по умолчанию, сохранение, ссылки на секреты."""
from __future__ import annotations

from app.core.config import CONFIG_FILE_NAME, AppConfig
from app.core.secret_store import PREFIX, SecretStore


def test_dry_run_default_on():
    cfg = AppConfig()
    assert cfg.dry_run is True


def test_config_roundtrip(tmp_path):
    cfg = AppConfig()
    cfg.dry_run = False
    cfg.demand_weights.popularity = 0.42
    cfg.scheduler.lease_check_interval_min = 11
    cfg.save(tmp_path / CONFIG_FILE_NAME)
    loaded = AppConfig.load(tmp_path / CONFIG_FILE_NAME)
    assert loaded.dry_run is False
    assert loaded.demand_weights.popularity == 0.42
    assert loaded.scheduler.lease_check_interval_min == 11


def test_corrupted_config_falls_back(tmp_path):
    path = tmp_path / CONFIG_FILE_NAME
    path.write_text("{broken json", encoding="utf-8")
    cfg = AppConfig.load(path)
    assert cfg.dry_run is True


def test_secret_ref_format():
    assert SecretStore.ref("steam/api_key") == f"{PREFIX}steam/api_key"
    assert SecretStore.parse_ref(f"{PREFIX}steam/api_key") == "steam/api_key"
    assert SecretStore.parse_ref("plain") is None


def test_secrets_roundtrip(tmp_path):
    store = SecretStore(fallback_dir=tmp_path)
    store.set("test/key", "secret-value")
    assert store.get("test/key") == "secret-value"
    assert store.has("test/key")
    store.delete("test/key")
    assert store.get("test/key") is None
