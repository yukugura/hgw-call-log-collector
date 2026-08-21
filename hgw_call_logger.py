#!/usr/bin/env python3
"""HGWのWeb設定「通話ログ」をMySQLに差分保存する。

本プログラムは HGW の設定を変更しない。1回だけ取得して終了するため、
systemd timer 等から定期実行する。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime
from typing import Iterable
from urllib.parse import urljoin, urlparse

import pymysql
import requests
from bs4 import BeautifulSoup, Tag
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth


HEADERS = {
    "表示番号": "displayed_number",
    "発着信種別": "call_direction",
    "発着信日時": "call_date_time",
    "通信日時": "call_date_time",
    "通話開始日時": "started_at_text",
    "通話切断日時": "ended_at_text",
    "装置内電話番号": "device_phone_number",
    "IP端末IPアドレス": "ip_terminal_address",
    "メディア種別": "media_type",
    "内線番号": "extension_number",
    "相手先電話番号": "peer_phone_number",
    "相手先IPアドレス": "peer_ip_address",
    "切断源": "disconnect_source",
    "切断理由": "disconnect_reason",
    "切断理由（SIP）": "sip_disconnect_reason",
    "切断理由(SIP)": "sip_disconnect_reason",
    "チャネル番号": "channel_number",
}

DB_COLUMNS = tuple(HEADERS.values())

# HGW画面上の表示順位（PRE内の「1.」「2.」など）は、新しい通話が追加されるたびに
# 全履歴で変化する。重複判定には順位やraw_recordを含めず、通話を識別する値だけを使う。
CALL_IDENTITY_FIELDS = (
    "call_date_time",
    "started_at_text",
    "ended_at_text",
    "device_phone_number",
    "call_direction",
    "displayed_number",
    "media_type",
    "extension_number",
    "peer_phone_number",
    "peer_ip_address",
    "disconnect_source",
    "disconnect_reason",
    "sip_disconnect_reason",
    "channel_number",
)

CREATE_CALL_LOGS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS call_logs (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      source_hash CHAR(64) NOT NULL,
      observed_at DATETIME NOT NULL,
      call_direction VARCHAR(32) NULL,
      displayed_number VARCHAR(128) NULL,
      call_date_time VARCHAR(64) NULL,
      started_at_text VARCHAR(64) NULL,
      ended_at_text VARCHAR(64) NULL,
      device_phone_number VARCHAR(128) NULL,
      ip_terminal_address VARCHAR(64) NULL,
      media_type VARCHAR(32) NULL,
      extension_number VARCHAR(32) NULL,
      peer_phone_number VARCHAR(128) NULL,
      peer_ip_address VARCHAR(64) NULL,
      disconnect_source VARCHAR(64) NULL,
      disconnect_reason VARCHAR(128) NULL,
      sip_disconnect_reason VARCHAR(128) NULL,
      channel_number VARCHAR(64) NULL,
      raw_record JSON NOT NULL,
      PRIMARY KEY (id),
      UNIQUE KEY uq_call_logs_source_hash (source_hash),
      KEY ix_call_logs_observed_at (observed_at)
    ) ENGINE=InnoDB
      DEFAULT CHARSET=utf8mb4
      COLLATE=utf8mb4_unicode_ci
"""


def setting(name: str, *, required: bool = True, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"環境変数 {name} を .env に設定してください")
    return value or ""


def clean(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def normalise_header(value: str) -> str:
    # 「装置内電話番号（※ 1）」のような脚注だけ取り除き、
    # 「切断理由（SIP）」の意味は残す。
    return re.sub(r"[（(]※[^）)]*[）)]", "", clean(value)).replace(" ", "")


class HgwClient:
    def __init__(self) -> None:
        hgw_ip = setting("HGW_IP")
        scheme = setting("HGW_SCHEME", required=False, default="http")
        if scheme not in {"http", "https"}:
            raise RuntimeError("HGW_SCHEME は http または https を指定してください")
        base_url = f"{scheme}://{hgw_ip}/"
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.timeout = float(setting("HGW_TIMEOUT_SECONDS", required=False, default="10"))
        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(
            setting("HGW_BASIC_AUTH_USER"),
            setting("HGW_BASIC_AUTH_PASSWORD"),
        )
        self.session.headers["User-Agent"] = "hgw-call-logger/1.0"

    def get(self, url: str) -> requests.Response:
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        # 本HGWのWeb設定はレスポンスヘッダーに文字コードを付けず、
        # Shift_JIS系でHTMLを返す。requestsの既定判定に任せると日本語の
        # 表ヘッダーを正しく照合できない場合があるため明示する。
        if "charset=" not in response.headers.get("Content-Type", "").lower():
            response.encoding = "cp932"
        return response

    def call_log_page(self) -> tuple[str, str]:
        configured = setting("HGW_CALL_LOG_URL", required=False)
        if configured:
            response = self.get(urljoin(self.base_url, configured))
            return response.url, response.text

        # メニューが frameset の場合もあるので、同一HGW内を浅く探索する。
        pending = [self.base_url]
        visited: set[str] = set()
        base_host = urlparse(self.base_url).netloc
        while pending and len(visited) < 20:
            url = pending.pop(0)
            if url in visited:
                continue
            visited.add(url)
            response = self.get(url)
            soup = BeautifulSoup(response.text, "html.parser")
            page_text = clean(soup.get_text(" "))
            if "通話ログ" in page_text and self._has_log_table(soup):
                return response.url, response.text
            for node in soup.select("a[href], frame[src], iframe[src]"):
                target = node.get("href") or node.get("src")
                if not target:
                    continue
                absolute = urljoin(response.url, target)
                parsed = urlparse(absolute)
                if parsed.scheme in {"http", "https"} and parsed.netloc == base_host:
                    label = clean(node.get_text(" "))
                    if "通話ログ" in label:
                        log_response = self.get(absolute)
                        return log_response.url, log_response.text
                    if absolute not in visited:
                        pending.append(absolute)
        raise RuntimeError(
            "通話ログ画面を自動検出できませんでした。ブラウザでHGWの通話ログを開き、そのURLを "
            "HGW_CALL_LOG_URL に設定してください。"
        )

    @staticmethod
    def _has_log_table(soup: BeautifulSoup) -> bool:
        return any("通話開始日時" in clean(table.get_text(" ")) for table in soup.find_all("table"))


def table_records(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    best: list[dict[str, str]] = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header_cells = rows[0].find_all(["th", "td"])
        headers = [normalise_header(cell.get_text(" ", strip=True)) for cell in header_cells]
        mapped = [HEADERS.get(header) for header in headers]
        if "started_at_text" not in mapped and "call_date_time" not in mapped:
            continue
        records: list[dict[str, str]] = []
        for row in rows[1:]:
            cells = row.find_all(["th", "td"])
            values = [clean(cell.get_text(" ", strip=True)) for cell in cells]
            if len(values) != len(headers) or not any(values):
                continue
            record = {mapped[index]: value for index, value in enumerate(values) if mapped[index] and value}
            if record:
                records.append(record)
        if len(records) > len(best):
            best = records
    return best


def pre_records(html: str) -> list[dict[str, str]]:
    """NTT系HGWの `<pre>` 形式の CALL.LOG を解析する。"""
    soup = BeautifulSoup(html, "html.parser")
    pre = soup.find("pre")
    if pre is None:
        return []
    lines = [line.rstrip() for line in pre.get_text().splitlines()]
    first = next((index for index, line in enumerate(lines) if re.match(r"^\s*\d+\.\s+", line)), None)
    if first is None:
        return []  # 履歴が0件の場合

    records: list[dict[str, str]] = []
    for index in range(first, len(lines), 6):
        block = lines[index : index + 6]
        if len(block) != 6 or not re.match(r"^\s*\d+\.\s+", block[0]):
            continue
        timestamp_line = re.sub(r"^\s*\d+\.\s+", "", block[0]).strip()
        # HGWは日時を固定幅（24文字）で並べる。未接続は ********** となる。
        call_date_time = timestamp_line[:24].strip()
        started_at = timestamp_line[26:50].strip()
        ended_at = timestamp_line[52:].strip()
        line1 = re.split(r"\s{2,}", block[1].strip(), maxsplit=1)
        line2 = re.split(r"\s{2,}", block[2].strip(), maxsplit=2)
        peer_ip_line = block[4].strip()
        peer_ip_match = re.search(r"(?:\d{1,3}\.){3}\d{1,3}", peer_ip_line)
        record = {
            "call_date_time": call_date_time,
            "started_at_text": started_at,
            "ended_at_text": ended_at,
            "device_phone_number": line1[0] if line1 else "",
            "call_direction": line1[1] if len(line1) > 1 else "",
            "displayed_number": line2[0] if line2 else "",
            "media_type": line2[1] if len(line2) > 1 else "",
            "peer_phone_number": block[3].strip(),
            # HGWの固定幅ログには他列の空白も含まれるため、IPv4だけを保存する。
            "peer_ip_address": peer_ip_match.group(0) if peer_ip_match else clean(peer_ip_line),
            "disconnect_source": block[5].strip(),
            # 元の固定幅表示も保持し、機種差があっても情報を失わない。
            "hgw_raw_lines": block,
        }
        records.append({key: value for key, value in record.items() if value not in ("", [])})
    return records


def parse_records(html: str) -> list[dict[str, str]]:
    records = table_records(html)
    return records or pre_records(html)


def source_hash(record: dict[str, str]) -> str:
    # 同一履歴は同じハッシュになるため、ポーリングによる重複登録を防げる。
    # hgw_raw_lines は画面上の順位を含むため、ここへ入れてはいけない。
    identity = {field: record.get(field, "") for field in CALL_IDENTITY_FIELDS}
    # 既存DBとの互換性のため、PRE形式では整形前のIP行を重複判定に使う。
    # 保存するpeer_ip_address自体は上でIPv4だけに整形している。
    raw_lines = record.get("hgw_raw_lines")
    if isinstance(raw_lines, list) and len(raw_lines) >= 5:
        identity["peer_ip_address"] = raw_lines[4].strip()
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def connect_db() -> pymysql.Connection:
    return pymysql.connect(
        host=setting("MYSQL_HOST"),
        port=int(setting("MYSQL_PORT", required=False, default="3306")),
        user=setting("MYSQL_USER"),
        password=setting("MYSQL_PASSWORD"),
        database=setting("MYSQL_DATABASE"),
        charset="utf8mb4",
        autocommit=False,
    )


def ensure_schema(connection: pymysql.Connection) -> None:
    """初回起動時だけテーブルを作成する（既存テーブルは変更しない）。"""
    with connection.cursor() as cursor:
        cursor.execute(CREATE_CALL_LOGS_TABLE_SQL)
    connection.commit()


def save(records: Iterable[dict[str, str]]) -> int:
    sql = """
        INSERT IGNORE INTO call_logs
        (source_hash, observed_at, call_direction, displayed_number, call_date_time,
         started_at_text, ended_at_text, device_phone_number, ip_terminal_address,
         media_type, extension_number, peer_phone_number, peer_ip_address,
         disconnect_source, disconnect_reason, sip_disconnect_reason, channel_number,
         raw_record)
        VALUES
        (%(source_hash)s, %(observed_at)s, %(call_direction)s, %(displayed_number)s,
         %(call_date_time)s, %(started_at_text)s, %(ended_at_text)s,
         %(device_phone_number)s, %(ip_terminal_address)s, %(media_type)s,
         %(extension_number)s, %(peer_phone_number)s, %(peer_ip_address)s,
         %(disconnect_source)s, %(disconnect_reason)s, %(sip_disconnect_reason)s,
         %(channel_number)s, %(raw_record)s)
    """
    rows = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for record in records:
        row = {column: record.get(column) for column in DB_COLUMNS}
        row.update(source_hash=source_hash(record), observed_at=now, raw_record=json.dumps(record, ensure_ascii=False))
        rows.append(row)
    if not rows:
        return 0
    with connect_db() as connection:
        ensure_schema(connection)
        with connection.cursor() as cursor:
            # INSERT IGNOREへ既存100件を毎回渡すと、MySQL/InnoDBが無視した行にも
            # AUTO_INCREMENT値を消費し、IDに大きな欠番ができることがある。
            placeholders = ", ".join(["%s"] * len(rows))
            cursor.execute(
                f"SELECT source_hash FROM call_logs WHERE source_hash IN ({placeholders})",
                tuple(row["source_hash"] for row in rows),
            )
            existing_hashes = {result[0] for result in cursor.fetchall()}
            new_rows = [row for row in rows if row["source_hash"] not in existing_hashes]
            affected = cursor.executemany(sql, new_rows) if new_rows else 0
        connection.commit()
    return affected


def main() -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = HgwClient()
    url, html = client.call_log_page()
    records = parse_records(html)
    inserted = save(records)
    logging.info("取得元=%s 検出=%d件 新規保存=%d件", url, len(records), inserted)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (requests.RequestException, pymysql.MySQLError, RuntimeError) as error:
        logging.basicConfig(level=logging.ERROR, format="%(asctime)s %(levelname)s %(message)s")
        logging.exception("通話ログ取得に失敗しました: %s", error)
        raise SystemExit(1)
