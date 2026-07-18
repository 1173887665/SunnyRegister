# Security Policy

## Reporting a vulnerability

Do not open a public issue for vulnerabilities or include real credentials, mailbox records, session tokens, screenshots, or production logs in an issue.

Use the repository's **Security** tab and submit a private vulnerability report through GitHub Security Advisories. Include affected versions, reproduction steps with synthetic data, impact, and a proposed mitigation when available.

## Supported versions

Security fixes are provided for the latest tagged release. Production deployments should use tagged versions and keep an encrypted database backup before updating.

## Sensitive data

SunnyRegister stores mailbox credentials and OAuth/session tokens in its SQLite database to perform configured workflows. Never commit `.env`, `secrets/`, `data/`, database files, exported account files, task logs, or backups. Restrict server access, use encrypted disks and encrypted off-site backups, and rotate any credential that may have entered Git history or logs.
