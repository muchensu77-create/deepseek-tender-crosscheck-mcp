from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

from .service import OfficialSearchService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Official Search MCP diagnostic CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="Search authorized official domains")
    search.add_argument("query")
    search.add_argument("--domain", action="append", dest="domains")
    search.add_argument("--max-results", type=int, default=5)

    fetch = subparsers.add_parser("fetch", help="Fetch a recent search candidate")
    fetch.add_argument("url")

    combined = subparsers.add_parser("search-and-fetch", help="Search then fetch candidates")
    combined.add_argument("query")
    combined.add_argument("--domain", action="append", dest="domains")
    combined.add_argument("--max-results", type=int, default=3)

    tender = subparsers.add_parser(
        "tender-crosscheck",
        help="Run server-enforced forward and reverse tender searches",
    )
    tender.add_argument("project_name")
    tender.add_argument("--project-code")
    tender.add_argument("--package-code")
    tender.add_argument("--purchaser")
    tender.add_argument("--supplier")
    tender.add_argument("--credit-code")
    tender.add_argument("--amount")
    tender.add_argument("--date")
    tender.add_argument("--region")
    tender.add_argument(
        "--regime",
        choices=("government_procurement", "mandatory_tender", "unknown"),
        default="unknown",
    )
    tender.add_argument(
        "--notice-type",
        choices=(
            "tender_notice",
            "candidate_notice",
            "award_result",
            "contract_notice",
            "acceptance_record",
            "unknown",
        ),
        default="unknown",
    )
    tender.add_argument(
        "--claimed-role",
        choices=(
            "bidder",
            "candidate",
            "winner",
            "contractor",
            "completed_performance",
            "unknown",
        ),
        default="unknown",
    )
    tender.add_argument("--domain", action="append", dest="domains")
    tender.add_argument("--max-results-per-query", type=int, default=3)
    tender.add_argument("--max-pages", type=int, default=10)

    subparsers.add_parser("config", help="Print non-secret effective configuration")
    return parser


async def _run(argv: Sequence[str] | None) -> int:
    args = _parser().parse_args(argv)
    service = OfficialSearchService()
    if args.command == "search":
        response = await service.search_official_sources(args.query, args.domains, args.max_results)
        payload = response.model_dump(mode="json")
    elif args.command == "fetch":
        response = await service.fetch_official_page(args.url)
        payload = response.model_dump(mode="json")
    elif args.command == "search-and-fetch":
        response = await service.search_and_fetch_official(
            args.query, args.domains, args.max_results
        )
        payload = response.model_dump(mode="json")
    elif args.command == "tender-crosscheck":
        response = await service.crosscheck_tender_information(
            args.project_name,
            procurement_regime=args.regime,
            project_code=args.project_code,
            package_code=args.package_code,
            purchaser=args.purchaser,
            supplier_legal_name=args.supplier,
            unified_social_credit_code=args.credit_code,
            claimed_amount=args.amount,
            claimed_date=args.date,
            region=args.region,
            notice_type=args.notice_type,
            claimed_role=args.claimed_role,
            domains=args.domains,
            max_results_per_query=args.max_results_per_query,
            max_pages_to_fetch=args.max_pages,
        )
        payload = response.model_dump(mode="json")
    else:
        payload = service.safe_config_summary()
        response = None
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if response is None or response.ok else 1


def main(argv: Sequence[str] | None = None) -> None:
    raise SystemExit(asyncio.run(_run(argv)))


if __name__ == "__main__":
    main()
