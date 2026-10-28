"""Command-line entry point: ``python -m mal_lifecycle {onboard,offboard,demo}``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from typing import TextIO

from dotenv import load_dotenv

from .config import ConfigError, Settings
from .factory import build_workflow
from .mockdata import SEED_EMPLOYEES
from .models import Employee, StepStatus, WorkflowResult

STATUS_MARK = {StepStatus.SUCCESS: "OK  ", StepStatus.FAILED: "FAIL", StepStatus.SKIPPED: "SKIP"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mal_lifecycle",
        description="Automate the security-relevant steps of employee onboarding and offboarding "
        "across Okta, Google Workspace, 1Password, Jamf and Slack/email.",
    )
    parser.add_argument("--mock", action="store_true", help="force every integration into mock mode")
    parser.add_argument("--env-file", default=".env", help="dotenv file to load (default: .env)")
    parser.add_argument(
        "--json", metavar="PATH", help="write the structured run result to this file ('-' for stdout)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    on = sub.add_parser("onboard", help="provision a new hire")
    on.add_argument("--first", required=True, help="first name")
    on.add_argument("--last", required=True, help="last name")
    on.add_argument("--email", required=True, help="corporate email / login")
    on.add_argument("--group", default="engineering", help="Okta group and 1Password group name")
    on.add_argument("--title")
    on.add_argument("--department")

    off = sub.add_parser("offboard", help="revoke access for a departing employee")
    off.add_argument("--email", required=True, help="corporate email / login")

    sub.add_parser("demo", help="run onboarding + offboarding end-to-end against the mocks")
    return parser


def print_summary(result: WorkflowResult, stream: TextIO | None = None) -> None:
    stream = stream or sys.stdout
    print(f"\n{result.workflow.upper()} {result.subject} -> {'OK' if result.ok else 'FAILURES'}", file=stream)
    for step in result.steps:
        line = f"  [{STATUS_MARK[step.status]}] {step.name:<42} {step.detail or step.error or ''}"
        print(line, file=stream)
    print(
        f"  {len(result.steps)} step(s), {len(result.failures)} failed, {result.duration_seconds:.2f}s\n",
        file=stream,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    load_dotenv(args.env_file, override=False)
    force_mock = args.mock or args.command == "demo"
    try:
        settings = Settings.from_env(force_mock=force_mock)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    workflow = build_workflow(settings)

    results: list[WorkflowResult] = []
    if args.command == "onboard":
        emp = Employee(args.first, args.last, args.email, args.group, args.title, args.department)
        results.append(workflow.onboard(emp))
    elif args.command == "offboard":
        results.append(workflow.offboard(args.email))
    else:
        new_hire = Employee(
            "Ada", "Lovelace", "ada.lovelace@example.com", "engineering", "Software Engineer", "Engineering"
        )
        results.append(workflow.onboard(new_hire))
        results.append(workflow.offboard(SEED_EMPLOYEES[0].login))

    for result in results:
        print_summary(result)
    if args.json:
        payload = json.dumps([r.to_dict() for r in results], indent=2)
        if args.json == "-":
            print(payload)
        else:
            with open(args.json, "w", encoding="utf-8") as fh:
                fh.write(payload)
    return 0 if all(r.ok for r in results) else 1
