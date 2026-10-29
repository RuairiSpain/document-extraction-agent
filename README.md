# Document Extraction Agent

A Python **Microsoft Foundry hosted agent**. You send it a PDF, and it returns the paragraphs and tables as JSON
(rows, columns, spans and cell text). A GPT-5.4 model reads the whole PDF in one call.

Use it when Document Intelligence struggles with complex tables, especially when values from separate cells are
merged into one value. This is a proof of concept. It is not hardened for production load.

Optionally, it also runs an independent **Mistral OCR 4** extraction and returns an `agreement` score (0 to 1) for how
closely the two match. Low agreement points to cells a person should check. The comparison never changes the GPT
result.

## Try it

Start the agent locally (see [Run it](#run-it)), then send the sample PDF. The client needs Python 3 and no packages:

```bash
python examples/call_agent.py examples/sample.pdf --evaluate
```

It prints each table as a grid, then the agreement summary. Options: `--url` for a deployed agent, `--model gpt-5.4-pro`,
`--raw` for the full JSON. With curl:

```bash
curl -X POST http://localhost:8088/invocations -H "Content-Type: application/json" -d @examples/request.example.json
```

Request and response fields, error codes and the scoring rules are in [docs/api.md](docs/api.md).

| Choice | Meaning |
|---|---|
| `gpt-5.4-mini` | Faster and cheaper. Start here. |
| `gpt-5.4-pro` | Slower, for harder documents. |
| `evaluation.enabled` | `true` adds the Mistral comparison. `false` (default) skips it. |

## How it works

`src/pipeline.py` is the entry point to read first.

1. `main.py` validates the request (`models/request.py`) and maps errors to HTTP status codes.
2. `pipeline.py` checks the PDF (`common/pdf.py`). With evaluation on, it starts the Mistral call at the same time.
3. `extraction/extractor.py` sends the whole PDF to the chosen GPT deployment in one call. The answer is forced into
   the schema in `models/primary_output.py`. The prompt is `extraction/resources/primary_instructions.md`.
4. The extractor checks the page numbers, adds the page sizes and returns the response (`models/response.py`).
5. `secondary/` calls Mistral OCR and parses its HTML or Markdown tables. `comparison/` scores the agreement.

In the code and settings, "primary" means the GPT extraction and "secondary" means the Mistral cross-check.

## Run it

Needs Python 3.13, [uv](https://docs.astral.sh/uv/), and a Foundry project with deployments of `gpt-5.4-mini`,
`gpt-5.4-pro` and (for evaluation) `mistral-ocr-4-0`. Sign in with `az login`.

```bash
cd src
uv venv .venv --python 3.13
source .venv/bin/activate            # Windows: .\.venv\Scripts\activate
uv pip install -r requirements.txt pytest pytest-asyncio reportlab ruff
cp ../.env.example .env              # Windows: copy ..\.env.example .env   (then edit it)
python main.py                       # listens on http://localhost:8088
```

Test and lint from the project folder:

```bash
python -m pytest                     # unit tests, no Azure needed
python -m pytest -m integration      # live tests against your Foundry project
ruff check . && ruff format .
```

## Settings

Environment variables, or `src/.env` locally. All authentication uses Microsoft Entra ID. There are no API keys.

| Variable | Default | Meaning |
|---|---|---|
| `FOUNDRY_PROJECT_ENDPOINT` | required | Foundry project endpoint. The platform injects it when hosted. |
| `EXTRACTION_MODEL_MINI` | required | Deployment name used for `gpt-5.4-mini` |
| `EXTRACTION_MODEL_PRO` | required | Deployment name used for `gpt-5.4-pro` |
| `SECONDARY_EXTRACTION_MODEL` | `mistral-ocr-4-0` | Mistral OCR deployment name |
| `EXTRACTION_TIMEOUT_SECONDS` | `1800` | Deadline for the GPT call |
| `LOG_LEVEL` | `INFO` | Python log level |

Grant the agent's identity (and developers, for local runs) the **Azure AI User** role on the Foundry project. See
[Foundry RBAC](https://learn.microsoft.com/azure/foundry/concepts/rbac-foundry).

## Deploy

`azure.yaml` declares the three model deployments and the hosted agent. You need the
[Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd) 1.27.1 or later
with the `azure.ai.agents` extension (`azd extension install azure.ai.agents`).

```bash
export AZURE_DEV_USER_AGENT="microsoft_foundry_skill"   # PowerShell: $env:AZURE_DEV_USER_AGENT="microsoft_foundry_skill"
azd ai agent init -m ./azure.yaml        # add --project-id "<foundry-project-arm-id>" to use an existing project
azd env set AZURE_SUBSCRIPTION_ID "<subscription-id>"
azd env set AZURE_LOCATION "<region that offers all three models>"
azd provision
azd deploy
azd ai agent invoke --input-file examples/request.example.json
```

The `sku` capacities in `azure.yaml` are placeholders. Adjust them to your quota, and check model availability in your
region first.

## Limits

- PDF only, up to 50 MB. The whole PDF goes in one call, so very long documents are limited by the model's context
  window.
- The Mistral comparison covers tables only, and Mistral OCR accepts up to 30 pages and 30 MB. For larger documents
  you still get the GPT result, with the evaluation marked unavailable.
- Mistral reports tables per page. The code merges a table that continues onto the next page, but only when Mistral's
  page markdown shows it at the end of one page and the start of the next. Otherwise it scores lower.
- Only paragraphs and tables are extracted. There are no key-value pairs, figures, captions, bounding polygons, or
  Document Intelligence-style `content` text and spans.
- Not verified from this repository: strict function calling on `gpt-5.4-mini` and the Mistral OCR call
  (`/providers/mistral/azure/ocr`, a Preview model). Run `python -m pytest -m integration` before relying on them.
