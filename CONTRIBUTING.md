# Contributing

RetailOps Backend is a Django project. Keep changes scoped, tested, and
documented.

## Local Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py bootstrap_local --seed
python manage.py runserver
```

On Windows, activate the virtual environment with
`.\.venv\Scripts\Activate.ps1`.

## Checks

Run before opening a pull request:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

## Scope

This repository contains the RetailOps backend. Do not add RetailOps Kiosk or
RetailOps CLI source code here; they are independent projects that consume the
backend API.

Do not commit local databases, uploaded media, `.env` files, API keys, service
account files, or generated deployment secrets.
