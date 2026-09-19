# AI Trade Lab

Local-first AI trading laboratory for **paper trading first**.

## Current v0.1
- 100 USDC virtual starting balance
- Apple-inspired responsive dashboard
- hard risk layer: 10% max position / 2% daily-loss limit
- decision journal
- Docker deployment for Debian / Proxmox
- live exchange orders intentionally locked

## Install
```bash
git clone https://github.com/BadFameZz/Ai-Trade-Lab.git /opt/ai-trade-lab && cd /opt/ai-trade-lab && docker compose up -d --build
```

Open `http://SERVER-IP:8787`.

Because this repository is private, cloning requires GitHub authentication on the server.

## Next milestones
Real market-data ingestion, persistent paper ledger, fee/slippage simulation, news ingestion, strategy evaluation, benchmark portfolios and versioned self-improvement.

No strategy can guarantee profit. Paper performance is not evidence of future live returns.
