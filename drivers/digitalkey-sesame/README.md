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
digitalkey sesame import <UUID> --name 玄関        # 別の端末で登録した鍵を取り込む(後述)
digitalkey sesame unlock <UUID>
digitalkey sesame app --web --fake                # 画面。--fake は実機なしの疑似 Sesame
```

## 鍵を別の機械へ写す

スマートフォンのアプリで登録した Sesame を、別の機械(PC や miniPC)からも操作するときの手順です。鍵(32 桁の 16 進)を持つ人はその Sesame を開けられるので、人に見せず、チャットやメールに貼りません。

1. スマートフォンのアプリで、その Sesame を選び、「鍵を書き出す」を押します。確かめのあとに 32 桁が出ます(閉じると消えます。ログ欄には出ません)
2. 写す先の機械で、BLE のアダプタを挿して `digitalkey sesame import <UUID> --name 玄関` を実行し、32 桁を入力します。入力は画面に出ず、シェルの履歴にも残りません。区切りの空白は読み飛ばします
3. `import` は、その鍵でログインできることを確かめてから保存します。ログインできなければ何も保存しません

説明は `digitalkey sesame import --help` と、`digitalkey_sesame/cli.py`、`app.py` の docstring にあります。疑似の Sesame(`--fake`)で手順を試せます。

プロトコル(広告の解析、AES-CCM、AES-CMAC、ECDH、登録とログイン、施錠・解錠の命令)の説明は `digitalkey_sesame/protocol.py` と `device.py` の先頭にあります。Android/iOS で BLE を使う Flet 拡張は `flet_ble/` です。

## スマートフォンアプリの配布

登録用の画面は Android と iOS のアプリとして組めます。配布は Google Play と App Store で行います。APK ファイルをリポジトリに置いて配ることはしません。

```bash
cd drivers/digitalkey-sesame
pip install flet-cli
flet build apk    # 開発機で試す(Android、arm64)
flet build aab    # Google Play に出す形式
flet build ipa    # App Store に出す形式(macOS と Xcode が必要)
```

設定は pyproject.toml の `[tool.flet]` にあります。アプリ名、パッケージ名(dev.aiseed.digitalkey.sesame)、Bluetooth の権限、iOS の利用目的の文がここに書いてあります。バージョンを上げるときは `build_version` と `build_number` を書き換えます。

ストアへの登録には開発者アカウントが必要です。Google Play は登録料 25 ドル(一回)、App Store は年 99 ドルです。署名鍵と証明書はリポジトリに入れず、それぞれの開発機で管理します。
