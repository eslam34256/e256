# LeakHunter AI v1.0 — Pilot-ready Commercial MVP

LeakHunter is a Procurement Value Recovery OS: **Find → Prove → Recover → Track**.

## Included
- Customer Demo Mode
- Synthetic enterprise dataset
- Executive Savings Report
- CFO Command Center
- Spend Analytics
- Invoice ↔ PO checks
- 3-way matching
- Contract intelligence
- Duplicate / maverick / split-PO signals
- Supplier 360
- Benchmarking
- Negotiation & Recovery workbench
- Savings Ledger
- Persistent SQLite storage
- Audit trail
- Docker packaging
- Pilot onboarding + demo script + security notes

## Run locally
```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

## Run with Docker
```bash
cp .env.example .env
# change LEAKHUNTER_DEMO_PASSWORD before sharing the demo
# optional: set LEAKHUNTER_DEMO_MODE=0 for pilot mode
docker compose up --build
```

Open `http://localhost:8501`.

### Demo credentials
When `LEAKHUNTER_DEMO_MODE=1`, the demo account is:
- Organization: Demo Manufacturing Co.
- Email: admin@demo.local
- Password: value of `LEAKHUNTER_DEMO_PASSWORD`

## Production gap
This is **pilot-ready, not production-enterprise ready**. Before real enterprise data, move to managed PostgreSQL, SSO/OIDC, MFA, database-enforced tenant isolation, managed secrets, TLS, centralized immutable audit logs, backups, file scanning, DLP, and observability. See `SECURITY.md`.

## Pilot mode bootstrap

For a fresh non-demo workspace, set `LEAKHUNTER_DEMO_MODE=0` and configure
`LEAKHUNTER_ORG_NAME`, `LEAKHUNTER_ADMIN_EMAIL`, and a strong
`LEAKHUNTER_BOOTSTRAP_PASSWORD` before the first startup. Remove the bootstrap
password from the environment after the administrator has been created.

## Quality checks

```bash
python -m pip install -r requirements-dev.txt
pytest -q
python -m py_compile app.py
```

GitHub Actions runs the smoke tests for every push and pull request.
