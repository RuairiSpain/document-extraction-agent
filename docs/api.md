# API reference

`POST /invocations`, JSON in and JSON out. Try it with `python examples/call_agent.py examples/sample.pdf --evaluate`
or `curl -X POST <url>/invocations -H "Content-Type: application/json" -d @examples/request.example.json`.

## Request

```json
{
  "document": { "content": "<base64 PDF>", "contentType": "application/pdf", "fileName": "invoice.pdf" },
  "extraction": { "model": "gpt-5.4-mini" },
  "evaluation": { "enabled": true }
}
```

| Property | Rules |
|---|---|
| `document.content` | Required. Base64, with no `data:` prefix. Line breaks are ignored, so the output of the `base64` command works. |
| `document.contentType` | Required. Only `application/pdf`. |
| `document.fileName` | Optional. Never sent to the model or logged. |
| `extraction.model` | `gpt-5.4-mini` (default) or `gpt-5.4-pro`. |
| `evaluation.enabled` | Default `false`. |

Unknown properties are rejected.

## Response

[`examples/response.example.json`](../examples/response.example.json) is a full response. It was generated offline
from the test doubles, not from a live model call, so treat the values as an illustration of the shape.

- `modelId`: the model that did the extraction.
- `pages`: page number and size in inches.
- `paragraphs`: text outside tables, in reading order, with the page it is on.
- `tables`: `pageNumbers`, `rowCount`, `columnCount` and `cells`. Each cell has `rowIndex`, `columnIndex`, `rowSpan`,
  `columnSpan`, `kind` (`content`, `columnHeader`, `rowHeader`, `stubHead`), `content` and the `pageNumber` it is
  printed on. A table that continues across pages is one table, and a repeated header row on a continuation page is not
  repeated as data. Blank cells that matter to the structure are cells with empty `content`.
- `evaluation`: `null` unless evaluation is enabled. Removing it leaves exactly the GPT result.

The fields use the same names as Azure Document Intelligence where they overlap, but this is not an `AnalyzeResult`.
There is no `content` string, no spans and no bounding polygons.

Abbreviated example (the table shows 3 of its 6 cells):

```json
{
  "modelId": "gpt-5.4-mini",
  "pages": [
    {
      "pageNumber": 1,
      "width": 8.2677,
      "height": 11.6929
    }
  ],
  "paragraphs": [
    {
      "pageNumber": 1,
      "content": "Adjacent numeric cells"
    }
  ],
  "tables": [
    {
      "pageNumbers": [
        1
      ],
      "rowCount": 2,
      "columnCount": 3,
      "cells": [
        {
          "kind": "columnHeader",
          "rowIndex": 0,
          "columnIndex": 0,
          "rowSpan": 1,
          "columnSpan": 1,
          "content": "Product",
          "pageNumber": 1
        },
        {
          "kind": "content",
          "rowIndex": 1,
          "columnIndex": 1,
          "rowSpan": 1,
          "columnSpan": 1,
          "content": "5",
          "pageNumber": 1
        },
        {
          "kind": "content",
          "rowIndex": 1,
          "columnIndex": 2,
          "rowSpan": 1,
          "columnSpan": 1,
          "content": "8",
          "pageNumber": 1
        }
      ]
    }
  ]
}
```

## Agreement score

`evaluation` compares the GPT tables with an independent Mistral OCR 4 reading of the same PDF. `agreement` is the
fraction of the two that match. It is not a Document Intelligence confidence value and not a model's own certainty.

- Range 0.0 to 1.0. `null` means no comparison was possible, always with a `reason`. A table or cell that has no
  counterpart is `null`, not 0.0.
- A different number of tables is disagreement, not missing evidence. If Mistral finds no table but GPT does, the
  document scores 0.0 with `TABLE_COUNT_MISMATCH`.
- Values are compared as exact text after collapsing runs of whitespace (so `Net\nAmount` equals `Net Amount`). There
  is no fuzzy matching.
- Mistral reports tables page by page. A table that ends one page and starts the next, with the same column count, is
  merged into one before comparing, and a repeated header row is dropped. This needs Mistral's page markdown to show
  the table at the end of one page and the start of the next.
- The weights are constants in `src/comparison/scoring.py`.

| Level | Rule |
|---|---|
| Table matching | Tables are paired by first page and order on that page. If the extractors disagree on how many tables start on a page, those tables are `null` (`AMBIGUOUS_TABLE_ALIGNMENT`). |
| Cell | Same position, same text and spans: 1.0. Same text, different span: 0.5 (`SPAN_MISMATCH`). Different text: 0.0 (`VALUE_MISMATCH`). One side's cell text equals two or more consecutive cells on the other side joined by a space or nothing: 0.0 (`MERGED_CELLS_IN_SECONDARY` or `MERGED_CELLS_IN_PRIMARY`). |
| Table | 0.25 x structure (row and column counts equal) + 0.75 x mean of the cell scores. |
| Document | Mean of the table scores and table-count equality (1.0 if equal, 0.0 if not). |

In the example, Mistral read the table as two columns (`Product | Quantity Rate` and `A | 5 8`). The merged cells score
0.0, the table scores 0.375, and the document scores 0.6875. The GPT values are unchanged.

If Mistral fails, exceeds its limits (30 pages, 30 MB) or the comparison breaks, you still get the GPT result with
`evaluation.document.agreement = null`, a `reason` and a warning. The request does not fail.

## Errors

Every error has the same shape. Quote the `correlationId` when you report a problem.

```json
{ "error": { "code": "INVALID_REQUEST", "message": "Invalid request at 'extraction.model': ...", "correlationId": "..." } }
```

| Status | Code | Cause | Retry? | What to do |
|---|---|---|---|---|
| 400 | `INVALID_REQUEST` | Bad JSON, unknown property, bad Base64, wrong content type, unknown model, or an unreadable or empty PDF, or one that needs a password | No | Read `message`, fix the request. |
| 413 | `DOCUMENT_TOO_LARGE` | PDF larger than 50 MB | No | Split or compress the PDF. |
| 503 | `MODEL_UNAVAILABLE` | The GPT call failed: network, authentication, missing deployment, throttling or an outage | Yes, with back-off | If it persists, check the deployment names and the **Azure AI User** role. |
| 502 | `EXTRACTION_REJECTED` | The model refused this document (HTTP 400, 409, 413 or 422 from the model) | No | Try a smaller or simpler document, or `gpt-5.4-pro`. |
| 504 | `EXTRACTION_TIMEOUT` | The GPT call exceeded `EXTRACTION_TIMEOUT_SECONDS` | Once | Use a smaller document or `gpt-5.4-mini`. |
| 502 | `EXTRACTION_FAILED` | The model's answer was incomplete or structurally invalid (for example overlapping cells) twice in a row. The agent already retried once. | Yes, sometimes works | If it repeats, try `gpt-5.4-pro` or split the document. |
| 500 | `INTERNAL_ERROR` | Anything unexpected | Once | Report it with the `correlationId`. |

The retry advice is guidance, not a guarantee. Error messages never contain stack traces, credentials or document
content. Logs contain only metadata (sizes, timings, outcomes).

## Design notes

- **One call, one schema.** Both models answer through one forced, strict function call whose parameters are the
  Pydantic schema in `models/primary_output.py`. Microsoft's model table lists structured outputs for `gpt-5.4-mini`
  but not for `gpt-5.4-pro`, and function calling works for both. The output is validated, never repaired.
- **No Document Intelligence layer.** The response is the schema the model fills in, plus page sizes and `evaluation`.
  Producing a full `AnalyzeResult` (text content, spans, polygons) would need about 400 more lines of assembly code.
- **No adjacent-cell merging.** The prompt forbids joining cells such as `| 5 | 8 |` into `5 8`, because that merge is
  the error this agent exists to avoid. The Mistral comparison looks for the same error.
- **Evaluation warnings** are in `evaluation.warnings`, so removing `evaluation` leaves the GPT result unchanged.
- **Mistral endpoint.** The path `/providers/mistral/azure/ocr` comes from Mistral's Azure client, not from a Microsoft
  Learn page. It is a constant in `src/secondary/mistral_ocr.py` together with the token scope
  `https://ai.azure.com/.default`.
