# onedrive-sync

Scripts and notebooks for pulling data from OneDrive, the BLS, USDA Quickstats,
and NewsAPI into a local Postgres database.

## Setup

```bash
pip install msal psycopg sqlalchemy pandas requests dotmap pony python-dotenv
cp .env.example .env
# fill in the real values in .env
```

## Layout

- `Api_Helpers.py` — `OneDriveConnector` (MSAL auth + Graph API) and `DbConnector` (Postgres).
- `onedrive.ipynb`, `onedrive upload.ipynb` — sync notebooks.
- `athena.ipynb` — USDA / NewsAPI / BLS ingestion into the `athena` database.
- `.env.example` — required environment variables.

## Configuration

All credentials are read from environment variables (auto-loaded from `.env` if
`python-dotenv` is installed). See `.env.example` for the full list. `.env`
itself is gitignored.
