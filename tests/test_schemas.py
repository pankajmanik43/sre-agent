import copy

import pytest
from pydantic import ValidationError

from agent.schemas import IncidentAssessment
from tests.conftest import VALID_ASSESSMENT


def _assessment(**overrides):
    data = copy.deepcopy(VALID_ASSESSMENT)
    data.update(overrides)
    return data


def test_valid_assessment_round_trips():
    assessment = IncidentAssessment.model_validate(VALID_ASSESSMENT)
    assert IncidentAssessment.model_validate_json(assessment.model_dump_json()) == assessment


def test_rollback_plan_is_optional():
    data = _assessment()
    del data["rollback_plan"]
    assert IncidentAssessment.model_validate(data).rollback_plan is None


def test_extra_fields_rejected():
    with pytest.raises(ValidationError, match="extra"):
        IncidentAssessment.model_validate(_assessment(probable_root_cause="x"))


def test_nested_extra_fields_rejected():
    data = _assessment()
    data["hypotheses"][0]["notes"] = "x"
    with pytest.raises(ValidationError, match="extra"):
        IncidentAssessment.model_validate(data)


@pytest.mark.parametrize("severity", ["SEV0", "sev1", "critical"])
def test_severity_must_be_known(severity):
    with pytest.raises(ValidationError):
        IncidentAssessment.model_validate(_assessment(severity=severity))


@pytest.mark.parametrize("confidence", [-0.1, 1.5])
def test_confidence_bounded(confidence):
    data = _assessment()
    data["hypotheses"][0]["confidence"] = confidence
    with pytest.raises(ValidationError):
        IncidentAssessment.model_validate(data)


def test_hypotheses_must_be_ranked():
    data = _assessment()
    data["hypotheses"].reverse()
    with pytest.raises(ValidationError, match="descending confidence"):
        IncidentAssessment.model_validate(data)


@pytest.mark.parametrize(
    "command",
    [
        "kubectl delete pod checkout-api-7d9f8c6b5-x2kq9 -n payments",
        "kubectl -n payments get pods",
        "kubectl",
    ],
)
def test_diagnostic_commands_must_be_read_only_kubectl(command):
    data = _assessment()
    data["hypotheses"][0]["diagnostic_commands"] = [command]
    with pytest.raises(ValidationError, match="diagnostic command"):
        IncidentAssessment.model_validate(data)


def test_non_kubectl_diagnostic_commands_allowed():
    data = _assessment()
    data["hypotheses"][0]["diagnostic_commands"] = ["dig db.payments.svc.cluster.local"]
    IncidentAssessment.model_validate(data)


def test_json_schema_forbids_additional_properties():
    schema = IncidentAssessment.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Hypothesis"]["additionalProperties"] is False
