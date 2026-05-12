import json
import os
import msal
import psycopg
import requests
import sqlalchemy
import pandas as pd
from psycopg import sql

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


ONEDRIVE_CLIENT_ID = os.environ.get('ONEDRIVE_CLIENT_ID')
ONEDRIVE_TENANT_ID = os.environ.get('ONEDRIVE_TENANT_ID', 'consumers')

BLS_API_KEY = os.environ.get('BLS_API_KEY')

PG_HOST = os.environ.get('PGHOST', 'localhost')
PG_PORT = os.environ.get('PGPORT', '5432')
PG_USER = os.environ.get('PGUSER', 'postgres')
PG_PASSWORD = os.environ.get('PGPASSWORD', '')


def _pg_conninfo(db_name: str) -> str:
    return (
        f"host={PG_HOST} port={PG_PORT} dbname={db_name} "
        f"user={PG_USER} password={PG_PASSWORD}"
    )


def _pg_url(db_name: str) -> str:
    return f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{db_name}"


class BlsConnector:
    def __is_json(self, myjson: str):
        print('initialized')


class OneDriveConnector:

    authority = f'https://login.microsoftonline.com/{ONEDRIVE_TENANT_ID}'
    scopes = ['Files.Read', 'Files.ReadWrite']

    def __is_json(self, myjson: str):
        try:
            json.loads(myjson)
        except ValueError:
            return False
        return True

    def __init__(self):
        if not ONEDRIVE_CLIENT_ID:
            raise RuntimeError('ONEDRIVE_CLIENT_ID is not set (see .env.example)')
        self.client_id = ONEDRIVE_CLIENT_ID
        self.app = msal.PublicClientApplication(self.client_id, authority=self.authority)
        result = self.app.acquire_token_interactive(scopes=self.scopes)
        self.access_token = result['access_token']
        self.refresh_token = result['refresh_token']

    def __refresh_token(self):
        result = self.app.acquire_token_by_refresh_token(refresh_token=self.refresh_token, scopes=self.scopes)
        self.access_token = result['access_token']
        self.refresh_token = result['refresh_token']

    def request_by_id(self, item_id: str):
        url = f'https://graph.microsoft.com/v1.0/me/drive/items/{item_id}/children'
        return self.request_by_url(url)

    def request_by_url(self, url: str):
        headers = {'Authorization': f'Bearer {self.access_token}'}
        response = requests.get(url, headers=headers)

        if not response.ok and response.status_code == 403:
            print('refreshing token')
            self.__refresh_token()
            headers = {'Authorization': f'Bearer {self.access_token}'}
            response = requests.get(url, headers=headers)
            print(response.status_code)
            print(response.reason)

        if self.__is_json(response.content):
            try:
                self.files = response.json().get('value', [])
            except Exception as e:
                print(f'Error: {e}')
                print(f'Response: {response.text}')
                self.files = []
                raise Exception(e)
        else:
            self.files = None
        return response


class DbConnector:

    def __init__(self, db: str = 'cid'):
        self.db_name = db

    def engine(self):
        return sqlalchemy.create_engine(_pg_url(self.db_name), client_encoding='utf8')

    def script(self, df: pd.DataFrame, table: str):
        ddl = pd.io.sql.get_schema(df, table, con=self.engine())
        return f'DROP TABLE IF EXISTS {table}; {ddl}'

    def query(self, sql_text):
        with psycopg.connect(_pg_conninfo(self.db_name)) as conn:
            with conn.cursor() as cur:
                cur.execute(sql_text)
                return cur.fetchall()

    def execute(self, sql_text):
        with psycopg.connect(_pg_conninfo(self.db_name)) as conn:
            with conn.cursor() as cur:
                cur.execute(sql_text)
                return cur.rowcount

    def exists(self, query: str):
        with psycopg.connect(_pg_conninfo(self.db_name)) as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL(query))
                return cur.rowcount > 0

    def add_id(self, value: str):
        with psycopg.connect(_pg_conninfo(self.db_name)) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO onedrive_sync (id) VALUES (%s)",
                    (value,),
                )
                conn.commit()

    def exists_onedrive(self, value: str):
        with psycopg.connect(_pg_conninfo(self.db_name)) as conn:
            with conn.cursor() as cur:
                query = sql.SQL(
                    "Select 'Y' where exists(select * from onedrive_sync where id = {id})"
                ).format(id=sql.Literal(value))
                cur.execute(query)
                rows = cur.fetchall()
                conn.commit()
                return len(rows) > 0

    def add_path(self, value: str, modified: float):
        with psycopg.connect(_pg_conninfo(self.db_name)) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM blob_sync WHERE path = %s", (value,))
                cur.execute(
                    "INSERT INTO blob_sync (path, modified) VALUES (%s, %s)",
                    (value, modified),
                )
                conn.commit()

    def exists_path(self, value: str, modified: float):
        with psycopg.connect(_pg_conninfo(self.db_name)) as conn:
            with conn.cursor() as cur:
                query = sql.SQL(
                    "Select 'Y' where exists(select * from blob_sync where path = {id} and modified >= {modified})"
                ).format(id=sql.Literal(value), modified=sql.Literal(modified))
                cur.execute(query)
                rows = cur.fetchall()
                conn.commit()
                return len(rows) > 0
