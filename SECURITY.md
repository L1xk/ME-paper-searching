# Security Policy

## Credentials

Never commit SMTP authorization codes, API keys, access tokens, `.env` files, or credential-bearing Git remote URLs. Configure runtime credentials only through repository **Settings → Secrets and variables → Actions**.

Forks do not inherit the upstream repository's Actions secrets. Fork owners must create their own secrets before enabling the workflow.

## Reporting a vulnerability

Report security issues privately through the repository's **Security → Advisories → New draft security advisory** page. Do not include credentials in a public issue. If a credential is exposed, revoke or rotate it immediately before cleaning logs or Git history.
