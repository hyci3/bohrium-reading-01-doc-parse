#!/usr/bin/env python3
"""Unified command-line entry for the evidence-pdf-reader Skill."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_evidence.capabilities import capabilities_command
from pdf_evidence.case_io import inspect_case_command
from pdf_evidence.probe import probe_pdf_command
from pdf_evidence.extractors import extract_command
from pdf_evidence.context import select_context_command
from pdf_evidence.coverage import coverage_report_command
from pdf_evidence.pipeline import auto_command
from pdf_evidence.result_io import finalize_result_command
from pdf_evidence.validators import evaluate_order_command, validate_package_command, validate_result_command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    command = subparsers.add_parser("capabilities", help="Detect actual PDF and OCR capabilities")
    command.add_argument("--resources")
    command.add_argument("--output", required=True)

    command = subparsers.add_parser("inspect-case", help="Read input contract and write an early result")
    command.add_argument("--case", required=True)
    command.add_argument("--task", required=True)
    command.add_argument("--pdf", action="append")
    command.add_argument("--work", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--early-result", required=True)

    command = subparsers.add_parser("probe-pdf", help="Inspect PDF pages and available processing paths")
    command.add_argument("--plan", required=True)
    command.add_argument("--capabilities", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--backend", choices=["pymupdf", "poppler"])

    command = subparsers.add_parser("extract", help="Extract coordinates and restore page reading order")
    command.add_argument("--plan", required=True)
    command.add_argument("--probe", required=True)
    command.add_argument("--capabilities", required=True)
    command.add_argument("--work", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--order", choices=["naive", "layout"], default="layout")

    command = subparsers.add_parser("auto", help="Run capabilities, inspect-case, probe, extract and coverage in one call")
    command.add_argument("--case", required=True)
    command.add_argument("--task", required=True)
    command.add_argument("--pdf", action="append")
    command.add_argument("--work", required=True)
    command.add_argument("--output-dir", required=True)
    command.add_argument("--resources")
    command.add_argument("--backend", choices=["pymupdf", "poppler"])
    command.add_argument("--order", choices=["naive", "layout"], default="layout")

    command = subparsers.add_parser("evaluate-order", help="Compare document order with independent gold annotations")
    command.add_argument("--document", required=True)
    command.add_argument("--gold", required=True)
    command.add_argument("--output", required=True)

    command = subparsers.add_parser("select-context", help="Build a compact index and select budgeted evidence")
    command.add_argument("--plan", required=True)
    command.add_argument("--document", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--index-output", required=True)
    command.add_argument("--budget-state", required=True)
    command.add_argument("--max-tokens", type=int)
    command.add_argument("--query")
    command.add_argument("--page", action="append", type=int)
    command.add_argument("--section", action="append")
    command.add_argument("--block-id", action="append")
    command.add_argument("--cursor")
    command.add_argument("--usage-event")
    command.add_argument("--full-document", action="store_true")
    command.add_argument("--segments-dir")
    command.add_argument("--glossary")

    command = subparsers.add_parser("coverage-report", help="Report which document structures are actually present")
    command.add_argument("--document", required=True)
    command.add_argument("--output", required=True)

    command = subparsers.add_parser("finalize-result", help="Merge an actual answer with evidence and contract fields")

    command.add_argument("--plan", required=True)
    command.add_argument("--document", required=True)
    command.add_argument("--answer", required=True)
    command.add_argument("--segments-dir")
    command.add_argument("--merge-output")
    command.add_argument("--output", required=True)

    command = subparsers.add_parser("validate-result", help="Validate contract, evidence, coverage, and status")
    command.add_argument("--plan", required=True)
    command.add_argument("--document", required=True)
    command.add_argument("--result", required=True)
    command.add_argument("--output", required=True)

    command = subparsers.add_parser("validate-package", help="Validate a portable Skill package")
    command.add_argument("--skill-dir", required=True)
    command.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "capabilities":
            return capabilities_command(args.resources, args.output)
        if args.command == "inspect-case":
            return inspect_case_command(args.case, args.task, args.pdf, args.work, args.output, args.early_result)
        if args.command == "probe-pdf":
            return probe_pdf_command(args.plan, args.capabilities, args.output, args.backend)
        if args.command == "extract":
            return extract_command(args.plan, args.probe, args.capabilities, args.work, args.output, args.order)
        if args.command == "auto":
            return auto_command(
                args.case, args.task, args.pdf, args.work, args.output_dir,
                args.resources, args.backend, args.order,
            )
        if args.command == "evaluate-order":
            return evaluate_order_command(args.document, args.gold, args.output)
        if args.command == "select-context":
            return select_context_command(
                args.plan, args.document, args.output, args.index_output, args.budget_state,
                args.max_tokens, args.query, args.page, args.section, args.block_id, args.cursor,
                args.usage_event, args.full_document, args.segments_dir, args.glossary,
            )
        if args.command == "coverage-report":
            return coverage_report_command(args.document, args.output)
        if args.command == "finalize-result":
            return finalize_result_command(args.plan, args.document, args.answer, args.output, args.segments_dir, args.merge_output)
        if args.command == "validate-result":
            return validate_result_command(args.plan, args.document, args.result, args.output)
        if args.command == "validate-package":
            return validate_package_command(args.skill_dir, args.output)
        raise ValueError(f"Unsupported command: {args.command}")
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
