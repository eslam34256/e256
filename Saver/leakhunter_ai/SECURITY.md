# LeakHunter AI — Security Notes

## Pilot status
This package is pilot-ready, not an enterprise production deployment. Do not upload regulated, highly sensitive, or customer production data until the deployment is reviewed and approved.

## Required before production
- Replace SQLite with managed PostgreSQL.
- Add SSO/OIDC and MFA.
- Enforce RBAC and tenant isolation at the database layer.
- Store secrets in a managed secret store.
- Enable TLS at the ingress/load balancer.
- Centralize immutable audit logs.
- Add backup/restore and retention policies.
- Add malware/file scanning for uploaded documents.
- Add DLP/classification controls for procurement documents.
- Add rate limiting and session controls.

## Current safeguards
- Passwords are hashed with PBKDF2-HMAC-SHA256.
- Demo seed data is controlled through `LEAKHUNTER_DEMO_MODE`.
- SQLite data can be persisted on a dedicated Docker volume.
- Demo data is synthetic and should never be treated as customer data.
