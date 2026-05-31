# RMI x402 Marketplace

> **Pay-per-call crypto security tools. For humans. For AI agents. No signup, no API key.**  
> Part of the [Rug Munch Intelligence](https://rugmunch.io) platform — The Bloomberg Terminal of Shitcoins.

---

## What Is This?

The x402 Gateway is the payment layer between users and our 210+ crypto security tools. It enables **instant, pay-per-call access** — humans pay with crypto wallets, AI agents pay via the HTTP 402 Payment Required protocol.

**Two audiences, one marketplace:**

| Audience | How They Pay | Experience |
|----------|-------------|------------|
| **Humans** | Connect wallet → pay USDC per scan | Web terminal at [rugmunch.io](https://rugmunch.io), Telegram bot |
| **AI Agents** | HTTP 402 handshake → auto-pay USDC per tool call | MCP protocol, direct API, Claude/Cursor/ChatGPT plugins |

---

## How It Works

```
User (human or AI) → Requests tool (e.g., "scan this token")
     │
     ▼
x402 Gateway ← Checks: free trial available? wallet connected? paid tier?
     │
     ├── Free tier → Execute tool (within rate limit)
     ├── Wallet tier → Execute tool (+ wallet bonus calls)
     └── Paid tier → HTTP 402 response with payment details
                         │
                         ▼
                    User pays USDC → Gateway verifies → Tool executes → Result returned
```

---

## Payment Rails (13 Chains, 10 Facilitators)

| Chain | Facilitators | Currency |
|-------|-------------|----------|
| Solana | PayAI | USDC |
| Base | Coinbase CDP, PayAI, EIP-7702 | USDC |
| Ethereum | Primev (free tier), PayAI, EIP-7702 | USDC |
| BSC | Pieverse, EIP-7702 | USDC, USDT |
| Arbitrum | EIP-7702 | USDC |
| Optimism | EIP-7702 | USDC |
| Polygon | EIP-7702 | USDC |
| Avalanche | EIP-7702 | USDC |
| Fantom | EIP-7702 | USDC |
| Gnosis | EIP-7702 | USDC |
| TRON | MERX, TRON Self-Verify | USDT, USDC, USDD |
| Bitcoin | Satoshi Self-Verify | BTC |
| SEPA/EUR | AsterPay | EUR |

**Auto-routing:** The gateway automatically picks the cheapest and fastest facilitator for each transaction.

---

## For AI Agents

```bash
# MCP Discovery
curl https://rugmunch.io/.well-known/mcp.json

# Direct API (OpenAI function calling format)
curl https://rugmunch.io/api/v1/x402-tools/openai-tools

# Anthropic Claude format
curl https://rugmunch.io/api/v1/x402-tools/anthropic-tools

# List all 210+ tools
curl https://rugmunch.io/api/v1/x402-tools/catalog
```

**Install the MCP server:**
```bash
pip install rug-munch-intelligence-mcp
```

---

## For Developers

Build tools that AI agents and humans can discover and pay for. Join the marketplace.

**Tool onboarding:** Add your tool to the catalog → set pricing → get listed.  
**Enterprise:** biz@rugmunch.io

---

## Deployment

The gateways run as Cloudflare Workers:

- **x402-gateway** — Main marketplace orchestrator
- **x402-gateway-base** — EVM chain payment processing
- **x402-gateway-solana** — Solana payment processing (PayAI)

---

## Anti-Abuse

- Browser/device fingerprinting
- Progressive wallet requirement (free → wallet → paid)
- IP-based rate limiting
- Global daily caps per device
- Burst protection (rapid-fire detection)
- **Full refund** if tool returns no data

---

## Part of Rug Munch Intelligence

- **Web Terminal** — [rugmunch.io](https://rugmunch.io)
- **MCP Server** — [github.com/Rug-Munch-Media-LLC/rug-munch-intelligence-mcp](https://github.com/Rug-Munch-Media-LLC/rug-munch-intelligence-mcp)
- **Token Scanner** — [github.com/Rug-Munch-Media-LLC/token-scanner](https://github.com/Rug-Munch-Media-LLC/token-scanner)
- **Wallet Scanner** — [github.com/Rug-Munch-Media-LLC/wallet-scanner](https://github.com/Rug-Munch-Media-LLC/wallet-scanner)
- **RugCharts** — Real-time charting + AI technical analysis
- **Telegram Bot** — [@rugmunchbot](https://t.me/rugmunchbot)

---

**Rug Munch Intelligence** — Don't get rugged. Get intelligence.

© 2026 Rug Munch Media LLC. All Rights Reserved.
