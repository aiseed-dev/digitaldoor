# digitalkey-sesame

digitalkey の錠ドライバです。CANDY HOUSE 製 Sesame(SesameOS3。Sesame 5 / 6 / 6 Pro)を、公式アプリを使わずに操作します。

```bash
pip install digitalkey-sesame          # 錠ドライバとコマンド
pip install "digitalkey-sesame[app]"   # 画面(Flet)も使う場合(QR を読むには PC に libzbar が要る。無くても 32 桁を打ち込む道で動く)
```

インストールすると、事業所サーバーの設定(site.toml)で `lock = "sesame:<機器UUID>"`(BLE 直結)と `lock = "sesameweb:<機器UUID>"`(Hub 3 経由の Web API)が使えるようになり、`digitalkey sesame` コマンドが増えます。

```bash
digitalkey sesame scan                            # 近くの Sesame を探す(BLE アダプタが必要)
digitalkey sesame register <UUID> --name 玄関      # 一度だけ。鍵を ~/.config/digitalkey/sesame-keys.json に保存
digitalkey sesame import <UUID> --name 玄関        # 別の端末で登録した鍵を取り込む(後述)
digitalkey sesame unlock <UUID>
digitalkey sesame app --web --fake                # 画面。--fake は実機なしの疑似 Sesame
```

## 鍵の役と、鍵の渡し方

鍵ごとに役(管理者、利用者)を持ちます。管理者の画面(探す、登録、渡す、渡した相手の控え、リセットと渡し直しの案内)は、管理者の鍵がある端末だけで開きます。利用者の鍵しか無い端末では開きません。管理者として渡すときだけ「管理者の符号」を付け、符号の無い鍵は利用者として入ります。前の版で登録した鍵は管理者として引き継ぎます。

**これは画面の歯止めで、鍵の守りではありません。** Sesame の錠が知っている鍵は 32 桁の一つだけで、錠の側では人を分けられません。32 桁を持つ人が別の道具を使えば、歯止めは通ります。防げるのは、受け取った人がこのアプリで鍵をさらに写して広げることです。一人を本当に止めるには、Sesame をリセットして登録し直し、残る人に渡し直します。

渡し方は、対面で、アプリからアプリへ直接です。網(LINE、メール、クラウド)を通しません。渡す人が管理者の画面の「渡す」で QR を出し(60 秒で自動で閉じて消えます)、受け取る人が自分のアプリの「鍵を受け取る」で、アプリのカメラで読みます。受け取る側は、その Sesame にログインできたときだけ保存します。読み取りが使えないときは、32 桁を打ち込む道があります(利用者として入ります)。

## 鍵を PC に写す

スマートフォンのアプリで登録した Sesame を、PC や miniPC からも操作するときの手順です。鍵(32 桁の 16 進)を持つ人はその Sesame を開けられるので、人に見せず、チャットやメールに貼りません。

1. スマートフォンのアプリの管理者の画面で、「渡す」から Sesame を選び、相手の呼び名を入れて「32 桁を見せる」を押します。確かめのあとに 32 桁が出ます(閉じると消えます。ログ欄には出ません。管理者として渡すときは管理者の符号も出ます)
2. 写す先の機械で、BLE のアダプタを挿して `digitalkey sesame import <UUID> --name 玄関` を実行し、32 桁を入力します。入力は画面に出ず、シェルの履歴にも残りません。区切りの空白は読み飛ばします。続けて管理者の符号を尋ねられます。管理者として写すときだけ符号を入力し、利用者なら空のまま Enter します
3. `import` は、その鍵でログインできることを確かめてから保存します。ログインできなければ何も保存しません

説明は `digitalkey sesame import --help` と、`digitalkey_sesame/cli.py`、`app.py`、`handoff.py` の docstring にあります。疑似の Sesame(`--fake`)で手順を試せます。

プロトコル(広告の解析、AES-CCM、AES-CMAC、ECDH、登録とログイン、施錠・解錠の命令)の説明は `digitalkey_sesame/protocol.py` と `device.py` の先頭にあります。Android/iOS で BLE を使う Flet 拡張は `flet_ble/` です。鍵の保管庫と端末の認証は `securestore.py`、QR の読み取りは `qrread.py` と `qrcamera.py` の先頭に、選んだ理由が書いてあります。

## スマートフォンアプリの配布

画面は Android と iOS のアプリ(「Sesame の鍵」)として組めます。配布は Google Play と App Store で行います。APK ファイルをリポジトリに置いて配ることはしません。

```bash
cd drivers/digitalkey-sesame
pip install flet-cli
flet build apk    # 開発機で試す(Android、arm64)
flet build aab    # Google Play に出す形式
flet build ipa    # App Store に出す形式(macOS と Xcode が必要)
```

設定は pyproject.toml の `[tool.flet]` にあります。アプリ名、パッケージ名(dev.aiseed.digitalkey.sesame)、Bluetooth とカメラの権限、iOS の利用目的の文、組むときに入れる Python の部品(`[tool.flet.android]` と `[tool.flet.ios]` の `dependencies`)、バックアップの除外(`allowBackup = "false"`)がここに書いてあります。バージョンを上げるときは `build_version` と `build_number` を書き換えます。

### 前の版が入った端末に上書きするとき

前の版は鍵をアプリの保存領域(SharedPreferences)に置いていました。この版は、端末の安全な保管庫(Android Keystore、iOS のキーチェーン)に置きます。**初めて起動したときに、鍵を保管庫へ移します。** 移す前に、念のため鍵を PC に写しておくことを勧めます(前のアプリの「鍵を書き出す」で 32 桁を出し、PC の `digitalkey sesame import` に打ちます)。アンインストールはしません。上書きで入れます(前と同じ署名の鍵で組みます)。

移し替えは、写す → 読み戻して一致を確かめる → 元を消す、の順です。途中のどこで失敗しても元は消えず、今までの保存領域のまま動きます(ログ欄に理由が出ます)。前の版の鍵は管理者として引き継ぎます。鍵は端末のバックアップで別の端末へ移りません。機種変更のときは、管理者から鍵を受け取り直します。手順の全体と、実機で確かめることは、`skills/sesame-app/SKILL.md` にあります。

ストアへの登録には開発者アカウントが必要です。Google Play は登録料 25 ドル(一回)、App Store は年 99 ドルです。署名鍵と証明書はリポジトリに入れず、それぞれの開発機で管理します。
