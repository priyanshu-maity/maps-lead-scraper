from datetime import datetime
from typing import Any

import gspread
from gspread import Spreadsheet, Worksheet
from google.oauth2.service_account import Credentials


class GSheetBatchWriter:
    def __init__(
            self,
            creds_path: str,
            sheet_id: str,
            headers: list[str],
            insert_at: int = 0,
            dedupe_on: list[str] | None = None,
            batch_size: int = 50,
    ):
        self.creds_path: str = creds_path
        self.sheet_id: str = sheet_id
        self.headers: list[str] = headers
        self.insert_at: int = insert_at
        self.dedupe_on: list[str] | None = dedupe_on
        self.batch_size: int = batch_size

        self.workbook: Spreadsheet = self._connect()
        self.worksheets: list[Worksheet] = self.workbook.worksheets()
        self.worksheet: Worksheet = self.worksheets[insert_at]
        self.data: list[list[Any]] = []

        self._keys_cache: set[tuple[Any, ...]] = set()
        self._dedupe_indexes: list[int] | None = None

        self._check_dedupe_cols()
        self._set_dedupe_indexes()
        self._check_fill_headers()
        self._build_existing_keys_cache()

    def _connect(self) -> Spreadsheet:
        scopes = ['https://www.googleapis.com/auth/spreadsheets']
        creds = Credentials.from_service_account_file(self.creds_path, scopes=scopes)
        client = gspread.authorize(creds)

        workbook: Spreadsheet = client.open_by_key(self.sheet_id)
        return workbook

    def _check_fill_headers(self):
        if not self.worksheet.get_all_values():
            self.worksheet.append_row(self.headers)

    def _check_dedupe_cols(self):
        if self.dedupe_on:
            missing = set(self.dedupe_on) - set(self.headers)
            if missing:
                raise ValueError(f'Dedupe columns not in sheet: {missing}')

    def _set_dedupe_indexes(self):
        if self.dedupe_on:
            col_index_map = {name: i for i, name in enumerate(self.headers)}
            self._dedupe_indexes = [col_index_map[col] for col in self.dedupe_on]

    def _build_existing_keys_cache(self):
        if not self.dedupe_on:
            return

        existing_data = self.worksheet.get_all_values()
        if len(existing_data) > 1:
            self._keys_cache = {
                self._get_key(row)
                for row in existing_data[1:]
                if row and all(i < len(row) for i in self._dedupe_indexes)
            }

    def _get_key(self, row: list[Any]) -> tuple[Any, ...]:
        if not self._dedupe_indexes:
            return tuple()
        return tuple(row[i] for i in self._dedupe_indexes)

    def _dedupe_data(self):
        unique_data = []

        for row in self.data:
            key = self._get_key(row)
            if key not in self._keys_cache:
                unique_data.append(row)
                self._keys_cache.add(key)

        self.data = unique_data

    def dump(self, data: list[str] | dict):
        if isinstance(data, dict):
            data = [data.get(header, '') for header in self.headers]

        for idx, datum in enumerate(data):
            if len(str(datum)) > 50_000:
                data[idx] = str(datum)[:49_997] + '...'

        if len(data) != len(self.headers):
            raise ValueError(f'Data length {len(data)} does not match headers length {len(self.headers)}')
        self.data.append(data)
        if len(self.data) >= self.batch_size:
            self.flush()

    def flush(self):
        if not self.data:
            return

        if self.dedupe_on:
            self._dedupe_data()

        if self.data:
            self.worksheet.append_rows(self.data)

        self.data.clear()

    @staticmethod
    def get_date(fmt: str = '%Y-%m-%d') -> str:
        return datetime.now().date().strftime(fmt)
