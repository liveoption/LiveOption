from pathlib import Path

import yaml
import pytest
from pydantic import ValidationError

from liveoption_web.runs.protocol import RunSpecV1


def test_task_examples_are_valid_and_have_no_embedded_provider_secrets():
    root = Path(__file__).resolve().parents[2]
    paths = list((root / "examples/tasks").glob("*.yaml"))
    assert paths
    for path in paths:
        spec = RunSpecV1.model_validate(yaml.safe_load(path.read_text()))
        assert spec.protocol_version == "liveoption.run.v1"
        assert spec.experiment.start <= spec.experiment.end
        text = path.read_text()
        assert "api_key:" not in text and "sk-" not in text


@pytest.mark.parametrize("symbol", ["QZSP", "ACME", "BOLT", "MYDATA", "BRK.B"])
def test_custom_tasks_accept_imported_and_synthetic_underlyings(symbol):
    spec = RunSpecV1.model_validate({"mode": "custom", "cadence": "intraday", "model_id": "example",
                                     "experiment": {"universe": [symbol]}})
    assert spec.experiment.universe == [symbol]


@pytest.mark.parametrize("symbol", ["../SPY", "SPY?key=secret", "O:SPY250328C00500000", "", "lowercase"])
def test_custom_tasks_reject_invalid_underlying_identifiers(symbol):
    with pytest.raises(ValidationError):
        RunSpecV1.model_validate({"mode": "custom", "cadence": "intraday", "model_id": "example",
                                 "experiment": {"universe": [symbol]}})
