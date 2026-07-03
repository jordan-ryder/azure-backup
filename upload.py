#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "azure-storage-blob",
#     "psycopg[binary]",
#     "python-dotenv",
# ]
# ///
"""Push local files to Azure Blob Storage (cold storage backup), ad hoc.

Self-bootstrapping: run ./upload.py directly and uv creates a cached
environment with the dependencies above; no venv or pip install needed.

Converted from 'onedrive upload.ipynb'. Uses the same .env variables and the
same Postgres blob_sync table to skip already-uploaded, unchanged files, so
it is safe to re-run any time and picks up where previous runs left off.

A container SAS token must be current (see README for the Storage browser
link); set AZURE_STORAGE_SAS_TOKEN in .env, then:

    ./upload.py                    # defaults: ~/Pictures ~/Documents
    ./upload.py ~/Videos           # push specific folders or files
    ./upload.py --dry-run          # show what would upload, no changes

Legend while running: '.' uploaded, '-' unchanged/skipped, '/' too large.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv

# Load the project's .env regardless of where the script is run from.
load_dotenv(Path(__file__).parent / '.env')

HOME = Path.home()
CHUNK_SIZE = 4 * 1024 * 1024
# Same exclusions as the notebook, plus node_modules (substring match).
SKIP_DIR_SUBSTRINGS = ('NetBeans', 'actions-runner', 'node_modules')
# Dev junk skipped by exact directory name (dot-prefixed dirs are always skipped).
SKIP_DIR_NAMES = ('__pycache__', 'venv', 'virtualenv')
SKIP_PATH_PARTS = ('Documents/Games/',)

PG_CONNINFO = (
    f"host={os.environ.get('PGHOST', 'localhost')} "
    f"port={os.environ.get('PGPORT', '5432')} "
    f"dbname={os.environ.get('PGDATABASE', 'cid')} "
    f"user={os.environ.get('PGUSER', 'postgres')} "
    f"password={os.environ.get('PGPASSWORD', '')}"
)


def db_exists_path(value: str, modified: float) -> bool:
    """True if blob_sync already has this path at this mtime or newer."""
    with psycopg.connect(PG_CONNINFO) as conn:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT 1 FROM blob_sync WHERE path = %s AND modified >= %s',
                (value, modified),
            )
            return cur.fetchone() is not None


def db_add_path(value: str, modified: float) -> None:
    with psycopg.connect(PG_CONNINFO) as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM blob_sync WHERE path = %s', (value,))
            cur.execute(
                'INSERT INTO blob_sync (path, modified) VALUES (%s, %s)',
                (value, modified),
            )
            conn.commit()


def blob_name_for(filepath: Path) -> str:
    """Blob name convention from the notebook: path with $HOME stripped,
    keeping the leading slash, so names match existing blobs and blob_sync rows."""
    full = str(filepath)
    home = str(HOME)
    return full[len(home):] if full.startswith(home + '/') else full


def iter_files(root: Path, extra_skips: tuple[str, ...] = ()):
    if root.is_file():
        yield root
        return
    for entry in sorted(os.scandir(root), key=lambda e: e.name):
        p = Path(entry.path)
        if entry.is_dir(follow_symlinks=False):
            if (entry.name.startswith('.')
                    or entry.name in SKIP_DIR_NAMES
                    or entry.name in extra_skips
                    or any(n in entry.name for n in SKIP_DIR_SUBSTRINGS)):
                continue
            if any(part in f'{p}/' for part in SKIP_PATH_PARTS):
                continue
            yield from iter_files(p, extra_skips)
        elif entry.is_file(follow_symlinks=False):
            yield p


def upload_file(blob_service_client, container_client, container_name: str, filepath: Path, blob_name: str) -> None:
    if filepath.stat().st_size > CHUNK_SIZE:
        block_ids = []
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
        with open(filepath, 'rb') as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                block_id = str(len(block_ids)).zfill(6)
                block_ids.append(block_id)
                blob_client.stage_block(block_id=block_id, data=chunk)
        blob_client.commit_block_list(block_ids)
    else:
        with open(filepath, 'rb') as data:
            container_client.upload_blob(name=blob_name, data=data, overwrite=True)


def main() -> int:
    parser = argparse.ArgumentParser(description='Push local files to Azure Blob Storage.')
    parser.add_argument('paths', nargs='*', type=Path,
                        default=[HOME / 'Pictures', HOME / 'Documents'],
                        help='files or folders to push (default: ~/Pictures ~/Documents)')
    parser.add_argument('--max-size-mb', type=int, default=800,
                        help='skip files larger than this (default: 800)')
    parser.add_argument('--dry-run', action='store_true',
                        help='list what would upload without touching Azure or the DB')
    parser.add_argument('--skip', action='append', default=[], metavar='NAME',
                        help='additional directory name to skip (exact match, repeatable)')
    args = parser.parse_args()

    container_name = os.environ['AZURE_STORAGE_CONTAINER']
    account_url = os.environ['AZURE_STORAGE_ACCOUNT_URL']
    sas_token = os.environ['AZURE_STORAGE_SAS_TOKEN']

    blob_service_client = BlobServiceClient(f'{account_url}/?{sas_token}')
    container_client = blob_service_client.get_container_client(container_name)

    try:
        db_exists_path('__connection_probe__', 0)
    except Exception as e:
        print(f'Cannot reach Postgres (blob_sync dedup table): {e}', file=sys.stderr)
        return 1

    limit = args.max_size_mb * 1024 * 1024
    uploaded = unchanged = too_large = errors = 0

    for root in args.paths:
        if not root.exists():
            print(f'skipping missing path: {root}', file=sys.stderr)
            continue
        print(f'\n== {root}')
        for filepath in iter_files(root.expanduser().resolve(), tuple(args.skip)):
            blob_name = blob_name_for(filepath)
            modified = filepath.stat().st_mtime

            if db_exists_path(blob_name, modified):
                unchanged += 1
                print('-', end='', flush=True)
                continue
            if filepath.stat().st_size > limit:
                too_large += 1
                print('/', end='', flush=True)
                continue

            print(f'\n{blob_name}', end='')
            if args.dry_run:
                uploaded += 1
                continue
            try:
                upload_file(blob_service_client, container_client, container_name, filepath, blob_name)
            except (ClientAuthenticationError, HttpResponseError) as e:
                status = getattr(e, 'status_code', None)
                if status in (401, 403):
                    print(f'\nAzure auth failed ({status}). The SAS token has likely expired; '
                          'generate a new one and update AZURE_STORAGE_SAS_TOKEN in .env.',
                          file=sys.stderr)
                    return 1
                errors += 1
                print(f'\nerror uploading {blob_name}: {e}', file=sys.stderr)
                continue
            except OSError as e:
                errors += 1
                print(f'\nerror reading {filepath}: {e}', file=sys.stderr)
                continue
            db_add_path(blob_name, modified)
            uploaded += 1

    verb = 'would upload' if args.dry_run else 'uploaded'
    print(f'\n\n{verb}: {uploaded} | unchanged: {unchanged} | '
          f'over size limit: {too_large} | errors: {errors}')
    return 0 if errors == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
