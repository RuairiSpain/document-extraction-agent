"""Send a PDF to the extraction agent and print what came back. Standard library only.

    python examples/call_agent.py examples/sample.pdf
    python examples/call_agent.py my.pdf --model gpt-5.4-pro --evaluate
    python examples/call_agent.py my.pdf --url https://<your-agent-endpoint>/invocations --raw

Local default: the agent started with `python main.py` listens on http://localhost:8088/invocations.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def build_request(pdf: Path, model: str, evaluate: bool) -> dict:
    return {
        "document": {
            "content": base64.b64encode(pdf.read_bytes()).decode("ascii"),
            "contentType": "application/pdf",
            "fileName": pdf.name,
        },
        "extraction": {"model": model},
        "evaluation": {"enabled": evaluate},
    }


def call(url: str, body: dict, timeout: float) -> tuple[int, dict]:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:  # the agent returns a JSON error body
        return error.code, json.load(error)


def print_table(index: int, table: dict) -> None:
    grid = {(c["rowIndex"], c["columnIndex"]): c["content"] for c in table["cells"]}
    pages = ", ".join(str(p) for p in table["pageNumbers"])
    print(f"\nTable {index + 1}: {table['rowCount']} rows x {table['columnCount']} columns (page {pages})")
    for row in range(table["rowCount"]):
        print("  | " + " | ".join(grid.get((row, col), "") for col in range(table["columnCount"])) + " |")


def print_evaluation(evaluation: dict) -> None:
    document = evaluation["document"]
    score = "n/a" if document["agreement"] is None else f"{document['agreement']:.2f}"
    print(f"\nAgreement with {evaluation['secondaryModel']}: {score} ({document['reason'] or 'full agreement'})")
    for table in evaluation["tables"]:
        flagged = [c for c in table["cells"] if c["reason"]]
        print(f"  table {table['tableIndex'] + 1}: score {table['agreement']}, {len(flagged)} cell(s) to review")
        for cell in flagged:
            print(f"    row {cell['rowIndex']}, column {cell['columnIndex']}: {cell['reason']}")
    for warning in evaluation["warnings"]:
        print(f"  warning {warning['code']}: {warning['message']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--url", default="http://localhost:8088/invocations")
    parser.add_argument("--model", default="gpt-5.4-mini", choices=["gpt-5.4-mini", "gpt-5.4-pro"])
    parser.add_argument("--evaluate", action="store_true", help="also score agreement with Mistral OCR")
    parser.add_argument("--raw", action="store_true", help="print the full JSON response")
    parser.add_argument("--timeout", type=float, default=1800, help="seconds to wait for the agent")
    args = parser.parse_args()

    status, body = call(args.url, build_request(args.pdf, args.model, args.evaluate), args.timeout)
    if status != 200:
        error = body["error"]
        print(f"Error {status} {error['code']}: {error['message']} (correlation id {error['correlationId']})")
        return 1
    if args.raw:
        print(json.dumps(body, indent=2))
        return 0
    print(f"Model {body['modelId']}: {len(body['pages'])} page(s), {len(body['tables'])} table(s)")
    for index, table in enumerate(body["tables"]):
        print_table(index, table)
    if body.get("evaluation"):
        print_evaluation(body["evaluation"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
