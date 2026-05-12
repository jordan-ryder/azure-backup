# onedrive-sync

Not in a completely working state. I used this to pull all my files off of onedrive and upload to azure cold storage. 

Went from Onedrive cost of 15.00 to 0.01 per month, a savings of 99.93%!

## Setup

```bash
pip install msal psycopg sqlalchemy pandas requests dotmap pony python-dotenv
cp .env.example .env
# fill in the real values in .env
```

## Layout

- `Api_Helpers.py` — `OneDriveConnector` (MSAL auth + Graph API) and `DbConnector` (Postgres).
- `onedrive.ipynb`, `onedrive upload.ipynb` — sync notebooks.
- `.env.example` — required environment variables.

## Configuration

All credentials are read from environment variables (auto-loaded from `.env` if
`python-dotenv` is installed). See `.env.example` for the full list. `.env`
itself is gitignored.
