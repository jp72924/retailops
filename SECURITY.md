# Security Policy

Report security issues privately to the project maintainer. Do not open a public
issue containing secrets, exploit details, live tokens, or customer data.

## Sensitive Data

Never commit:

- `.env` files
- Django `SECRET_KEY` values
- database passwords
- Kiosk API keys
- cloud service account files
- uploaded receipt images or customer documents
- local SQLite databases

## Supported Branch

Security fixes are expected on the default branch unless a release branch is
created later.
