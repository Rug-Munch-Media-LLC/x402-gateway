# x402-gateway

Archived. This was the first pay-per-call x402 gateway; the code and the tool
catalogue now live in [`rmi-mcp-x402`](https://git.rugmunch.io/RugMunchMedia/rmi-mcp-x402),
which is the repository to use.

**Status:** archived — no further changes. **Licence:** MIT, see [LICENSE](LICENSE).

## Why it is archived

The gateway and the MCP server ended up sharing one payment core. Keeping two
copies meant two pricing tables and two places for the replay check to drift, so
the gateway was folded into `rmi-mcp-x402` and this tree was frozen. `ARCHIVED.md`
records the decision.

The Python package under `backend/app/` is the same code that now lives in
`rmi-mcp-x402/app/`. `backend/` is kept so the history is readable, not so it can
be deployed.

## What is still here

```
backend/app/     the gateway as it was at archive time (frozen)
scripts/         structure checks that ran in CI
ARCHIVED.md      the decision and what moved where
```

There is no supported way to run this. `docker compose` is not present, the CI
workflows are disabled, and no dependency pins have been refreshed since the
archive date.

## If you are looking for the working thing

```bash
git clone https://git.rugmunch.io/RugMunchMedia/rmi-mcp-x402.git
cd rmi-mcp-x402
docker compose up -d
```

That stack contains the facilitator (`coffer`), the MCP server, and Redis for
receipts.

## Licence

MIT. The archived code stays under the licence it shipped with.
