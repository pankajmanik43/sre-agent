import json
from pathlib import Path
from unittest import mock

import pytest

from agent import cli
from agent.agent import AgentError
from agent.schemas import IncidentAssessment
from tests.conftest import VALID_ASSESSMENT

EXAMPLE = Path(__file__).parent.parent / "examples" / "incident_pod_crashloop.json"


def test_load_incident_free_text():
    assert cli.load_incident("pods crashlooping in payments") == (
        "Investigate this incident: pods crashlooping in payments"
    )


def test_load_incident_json_payload():
    prompt = cli.load_incident(str(EXAMPLE))
    assert prompt.startswith("Investigate this alert payload:")
    assert json.loads(prompt.split("\n", 1)[1])["alerts"][0]["labels"]["alertname"] == (
        "KubePodCrashLooping"
    )


def test_load_incident_missing_json_file(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        cli.load_incident(str(tmp_path / "nope.json"))


def test_load_incident_invalid_json(tmp_path):
    bad = tmp_path / "alert.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        cli.load_incident(str(bad))


def test_main_prints_assessment(capsys):
    assessment = IncidentAssessment.model_validate(VALID_ASSESSMENT)
    with (
        mock.patch.object(cli.anthropic, "Anthropic") as client_cls,
        mock.patch.object(cli, "run_agent", return_value=assessment) as run,
    ):
        code = cli.main([str(EXAMPLE), "--context", "staging", "--effort", "medium"])

    assert code == 0
    client_cls.assert_called_once_with()
    kwargs = run.call_args.kwargs
    assert kwargs["effort"] == "medium"
    assert kwargs["ctx"].kubectl.context == "staging"
    assert IncidentAssessment.model_validate_json(capsys.readouterr().out) == assessment


def test_main_reports_agent_error(capsys):
    with (
        mock.patch.object(cli.anthropic, "Anthropic"),
        mock.patch.object(cli, "run_agent", side_effect=AgentError("boom")),
    ):
        assert cli.main(["something broke"]) == 1
    assert "boom" in capsys.readouterr().err


def test_main_rejects_bad_input(tmp_path, capsys):
    assert cli.main([str(tmp_path / "missing.json")]) == 2
    assert "not found" in capsys.readouterr().err
