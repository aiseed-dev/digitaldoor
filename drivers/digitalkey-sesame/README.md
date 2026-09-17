# digitalkey-sesame

digitalkey の錠ドライバです。CANDY HOUSE 製 Sesame(SesameOS3。Sesame 5 / 6 / 6 Pro)を、公式アプリを使わずに操作します。

```bash
pip install digitalkey-sesame          # 錠ドライバとコマンド
pip install "digitalkey-sesame[app]"   # 登録用の画面(Flet)も使う場合
```

インストールすると、事業所サーバーの設定(site.toml)で `lock = "sesame:<機器UUID>"`(BLE 直結)と `lock = "sesameweb:<機器UUID>"`(Hub 3 経由の Web API)が使えるようになり、`digitalkey sesame` コマンドが増えます。

```bash
digitalkey sesame scan                            # 近くの Sesame を探す(BLE アダプタが必要)
digitalkey sesame register <UUID> --name 玄関      # 一度だけ。鍵を ~/.config/digitalkey/sesame-keys.json に保存
digitalkey sesame unlock <UUID>
digitalkey sesame app --web --fake                # 画面。--fake は実機なしの疑似 Sesame
```

プロトコル(広告の解析、AES-CCM、AES-CMAC、ECDH、登録とログイン、施錠・解錠の命令)の説明は `digitalkey_sesame/protocol.py` と `device.py` の先頭にあります。Android/iOS で BLE を使う Flet 拡張は `flet_ble/` です。
