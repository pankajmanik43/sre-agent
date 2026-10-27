"""Command-line entry point: sre-agent "<description>" | sre-agent path/to/alert.json"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import anthropic

from agent.agent import DEFAULT_EFFORT, DEFAULT_MODEL, AgentError, run_agent
from agent.kubectl import KubectlRunner
from agent.tools import ToolContext


def load_incident(arg: str) -> str:
    """Return the prompt for an incident given free text or a path to a JSON alert payload."""
    path = Path(arg)
    if path.suffix.lower() == ".json" or path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"alert file not found: {arg}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"{arg} is not valid JSON: {exc}") from exc
        return "Investigate this alert payload:\n" + json.dumps(payload, indent=2)
    if not arg.strip():
        raise ValueError("incident description is empty")
    return f"Investigate this incident: {arg}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sre-agent",
        description="Investigate a Kubernetes incident with read-only kubectl access.",
    )
    parser.add_argument("incident", help="Free-text description, or a path to a JSON alert.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--effort", default=DEFAULT_EFFORT, choices=["low", "medium", "high", "xhigh", "max"]
    )
    parser.add_argument("--context", help="kubeconfig context to use.")
    parser.add_argument("--max-turns", type=int, default=20)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        incident = load_incident(args.incident)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    ctx = ToolContext(kubectl=KubectlRunner(context=args.context))
    try:
        assessment = run_agent(
            incident,
            client=anthropic.Anthropic(),
            ctx=ctx,
            model=args.model,
            effort=args.effort,
            max_turns=args.max_turns,
        )
    except AgentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except anthropic.APIStatusError as exc:
        print(f"error: Anthropic API returned {exc.status_code}: {exc.message}", file=sys.stderr)
        return 1
    except anthropic.APIConnectionError as exc:
        print(f"error: could not reach the Anthropic API: {exc}", file=sys.stderr)
        return 1

    print(assessment.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
