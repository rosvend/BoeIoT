"""Tests for the cold-start config layer (thresholds loading + factory).

Uses a fake S3 client (plain Python object with a ``get_object`` method) so
no boto3 calls actually hit the network.
"""

from __future__ import annotations

import io
import json

import pytest
from botocore.exceptions import ClientError

import config
from detector import DEFAULT_THRESHOLDS, ThresholdDetector


class _FakeS3:
    """Minimal stand-in for boto3 S3 client used in load_thresholds tests."""

    def __init__(self, payload: bytes | None = None, raise_on_get: Exception | None = None):
        self._payload = payload
        self._raise = raise_on_get

    def get_object(self, Bucket, Key):  # noqa: N803 — matches boto3 signature
        if self._raise is not None:
            raise self._raise
        return {"Body": io.BytesIO(self._payload or b"")}


@pytest.fixture(autouse=True)
def _set_thresholds_env(monkeypatch):
    """All load_thresholds tests need BUCKET/KEY env vars set."""
    monkeypatch.setenv("THRESHOLDS_BUCKET", "test-bucket")
    monkeypatch.setenv("THRESHOLDS_KEY", "artifacts/anomaly_thresholds.json")


def test_load_thresholds_returns_defaults_when_env_unset(monkeypatch):
    monkeypatch.delenv("THRESHOLDS_BUCKET", raising=False)
    monkeypatch.delenv("THRESHOLDS_KEY", raising=False)
    out = config.load_thresholds(s3_client=_FakeS3())
    assert out == DEFAULT_THRESHOLDS


def test_load_thresholds_returns_defaults_on_missing_object():
    err = ClientError({"Error": {"Code": "NoSuchKey", "Message": "x"}}, "GetObject")
    out = config.load_thresholds(s3_client=_FakeS3(raise_on_get=err))
    assert out == DEFAULT_THRESHOLDS


def test_load_thresholds_merges_published_artifact():
    payload = json.dumps({
        "version": 1,
        "thresholds": {
            "oil_temp_max": 0.91,
            "cht_spread_max": 0.27,
        },
    }).encode()
    out = config.load_thresholds(s3_client=_FakeS3(payload=payload))
    assert out["oil_temp_max"] == 0.91
    assert out["cht_spread_max"] == 0.27
    # Unspecified keys keep their defaults.
    assert out["oil_press_min"] == DEFAULT_THRESHOLDS["oil_press_min"]
    assert out["egt_spread_max"] == DEFAULT_THRESHOLDS["egt_spread_max"]


def test_load_thresholds_accepts_flat_dict_shape():
    payload = json.dumps({"oil_press_min": 0.07}).encode()
    out = config.load_thresholds(s3_client=_FakeS3(payload=payload))
    assert out["oil_press_min"] == 0.07


def test_load_thresholds_falls_back_on_malformed_json():
    out = config.load_thresholds(s3_client=_FakeS3(payload=b"not json"))
    assert out == DEFAULT_THRESHOLDS


def test_build_detector_default_is_threshold():
    det = config.build_detector()
    assert isinstance(det, ThresholdDetector)


def test_build_detector_unknown_type_raises(monkeypatch):
    monkeypatch.setenv("DETECTOR_TYPE", "transformer-9000")
    with pytest.raises(NotImplementedError):
        config.build_detector()
