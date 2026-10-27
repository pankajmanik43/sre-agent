# sre-agent
Python 3.12, pydantic, anthropic SDK, pytest, ruff.
- All kubectl calls must be read-only (get/describe/logs/top). Never add mutating verbs.
- Final agent output must validate against agent/schemas.py::IncidentAssessment.
- Every new tool in agent/tools.py gets a TOOL_SPECS entry and a test with a mocked backend.
- Run `ruff check . && pytest` before declaring anything done.
