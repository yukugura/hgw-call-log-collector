# HGW 通話ログ → MySQL

HGWのWeb設定にある「情報 > 通話ログ」を読み取り、未保存分だけをMySQLに記録するPythonプログラムです。HGWの設定を変更せず、1回取得して終了します。

GitHubリポジトリ名は `hgw-call-log-collector` を想定しています。

## 初回準備

Ubuntuでは、リポジトリをクローンしてインストーラーを実行するだけで設定できます。初回のみHGWとMySQLのパスワードを入力します。既に `.env` がある場合は上書きしません。

```bash
git clone https://github.com/yukugura/hgw-call-log-collector.git
cd hgw-call-log-collector
bash install.sh
```

インストーラーはPython仮想環境・依存関係・systemdサービス／10秒タイマーを作成し、初回取得まで実行します。DB `hgw_call_logs` は事前に作成しておいてください。

root専用のLXCコンテナでも、そのまま `bash install.sh` を実行できます。その場合、collectorのsystemdサービスもrootとして実行されます。

`python3-venv` が未導入のUbuntu/Debian環境では、インストーラーが自動的に導入して仮想環境を作り直します。

手動で設定する場合は、Ubuntuで任意の配置先へファイルを置き、次を実行します。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mysql -u root -p < schema.sql
cp .env.example .env
chmod 600 .env
```

`.env` にHGWのHTTP Basic認証情報とMySQLの接続情報を記入してください。HGWの接続先は `HGW_IP`（初期値は `192.168.0.1`）で変更できます。HGWのWeb設定はユーザー名 `user` と、初期設定時に設定した機器設定用パスワードで認証します。

```dotenv
HGW_BASIC_AUTH_USER=user
HGW_BASIC_AUTH_PASSWORD=機器設定用パスワード
```

MySQL利用者の作成例です。`十分に長いパスワード` は実際の値へ置き換えてください。

```sql
CREATE USER 'hgwlog'@'%' IDENTIFIED BY '十分に長いパスワード';
GRANT INSERT, SELECT, CREATE ON hgw_call_logs.* TO 'hgwlog'@'%';
FLUSH PRIVILEGES;
```

動作確認は以下です。

```bash
.venv/bin/python hgw_call_logger.py
```

`hgw_call_logs` データベースが存在すれば、初回実行時に `call_logs` テーブルを自動作成します。DBそのものの作成は管理者権限が必要なため、`schema.sql` または `CREATE DATABASE hgw_call_logs ...` を管理者で一度だけ実行してください。

このHGWの通話ログURLは標準で `HGW_CALL_LOG_URL=/cgi-bin/mainte.cgi?st_clog` に設定済みです。画面構成やファームウェア差異で取得できない場合は、ブラウザで通話ログを開いたURLをこの変数に指定してください。

## systemdを手動設定する場合

`/etc/systemd/system/hgw-call-logger.service`:

```ini
[Unit]
Description=HGW call-log collector

[Service]
Type=oneshot
WorkingDirectory=/opt/hgw-call-logger
ExecStart=/opt/hgw-call-logger/.venv/bin/python /opt/hgw-call-logger/hgw_call_logger.py
```

`/etc/systemd/system/hgw-call-logger.timer`:

```ini
[Unit]
Description=Run HGW call-log collector every 10 seconds

[Timer]
OnBootSec=15s
OnUnitInactiveSec=10s
AccuracySec=1s

[Install]
WantedBy=timers.target
```

配置先を変えた場合は、上記の `/opt/hgw-call-logger` を実際の絶対パスへ置換します。有効化は次の通りです。

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hgw-call-logger.timer
systemctl list-timers hgw-call-logger.timer
journalctl -u hgw-call-logger.service -f
```

---

Created with OpenAI Codex.

## 注意

- `.env` は認証情報を含むため、Gitに追加しないでください。
- HGWの通話ログは最大100件で、電源断や完全初期化で消える場合があります。10秒間隔なら通常は十分余裕があります。
- 本プログラムは通話終了後にHGWへ記録された履歴を保存します。着信中・通話中のイベントは取得しません。
