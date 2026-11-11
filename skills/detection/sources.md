# 出典

閲覧日はすべて 2026-11-11 です。要約は自分の言葉で書いています。資料が更新されたら、読み直して閲覧日を書き直します(08 章)。

## Anthropic

| 資料 | URL | 確かめた事 |
|---|---|---|
| Cyber Verification Program(英語) | https://support.claude.com/en/articles/14604842-cyber-verification-program | Defense Access が許す仕事に、セキュリティの運用、事故への対応、マルウェアの分析、detection engineering、弱いところの選別と確かめが入る事。Red Team Access は許しを得た侵入の試験、レッドチーム、攻撃の模擬などで、組織だけが申し込める事。制度に入った者は全員、悪用を見張り抑えるためにデータの保持が必須である事(データの保持の免除を持つ組織は、社内の使い道に限り保持なしで使える事)。既定は社内の使い道(自社のコード、製品、機械の仕事)で、お客のための仕事は別の承認が要る事。Defense Access には、多要素の認証と、API の鍵を 7 日以内に替える事を備えていると表明する事が要り、2026-12-15 からはフィッシングに強い多要素の認証が要り、長く使い続ける API の鍵が認められなくなる事。どの枠でも利用の方針はそのまま当たり、データや機器の大量の破壊、サービスの妨害などは禁じられている事 |

この頁は、運営の側が 2026-11-11 に取得した写しを読みました。

## 検知の書き方

| 資料 | URL | 確かめた事 |
|---|---|---|
| Sigma: Rules | https://sigmahq.io/docs/basics/rules.html | 決まりは YAML のファイルで、要る欄は `title`、`logsource`、`detection`(選ぶ条件と `condition`)である事。`id` は UUID の第 4 版を勧める事。`level` と `status` の取りうる値。並べた値は「どれか」、並べた欄は「全部」の意味である事 |
| Sigma: Log Sources | https://sigmahq.io/docs/basics/log-sources.html | Windows のセキュリティのイベントログは `product: windows` と `service: security`、Linux は `product: linux` と `service: auth` や `service: sshd`、プロセスの作成は `category: process_creation` である事 |

## Microsoft(learn.microsoft.com)

| 資料 | URL | 確かめた事 |
|---|---|---|
| Appendix L: Events to Monitor | https://learn.microsoft.com/en-us/windows-server/identity/ad-ds/plan/appendix-l--events-to-monitor | 見張るべきイベントの ID と重要度(4719 は高、1102 は中〜高、4624・4625・4672・4688・4697・4698・4720・4728・4732・4740・4756 は低で、普段の量を超えた時に調べる物)。Windows の既定の記録は版で違い、多くの監査の設定が「構成されていません」なので、要る記録が出るよう監査の方針を整える事 |
| 4625(F) An account failed to log on | https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/security/threat-protection/auditing/event-4625 | ログオンの種類の番号(2 は対話、3 は網、10 はリモートデスクトップ)。XML の欄の名(`TargetUserName`、`Status`、`IpAddress`)。状態の番号(0xC000006A はパスワードの誤り、0xC0000064 は居ないアカウント) |
| about_Logging_Windows | https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_logging_windows | スクリプトブロックの記録は ID 4104 で `PowerShellCore/Operational` に入る事。グループ ポリシーとレジストリでの入れ方。スクリプトの中の資格情報などが記録に入る事があり、保護されたイベントの記録を勧める事。秘密鍵は記録を出す機械に置かない事 |
| Sysmon | https://learn.microsoft.com/en-us/sysinternals/downloads/sysmon | 記録の置き場、`sysmon -accepteula -i 設定.xml` での入れ方、ID 1・3・11・13・22 の中身、網の接続(ID 3)は既定で切ってある事、時刻は UTC で書く事 |
| Get-WinEvent | https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.diagnostics/get-winevent | `-FilterHashtable` の鍵(`LogName`、`ID`、`StartTime` など)と `-MaxEvents` の書き方 |
| Search the audit log | https://learn.microsoft.com/en-us/purview/audit-search | 統合監査ログは企業向けの契約で既定で入っている事と確かめ方。読むのに要る役。残る期間(E5 の利用者の Entra ID・Exchange・SharePoint の記録は既定で 1 年、それ以外は 180 日)。`Search-UnifiedAuditLog` と Office 365 Management Activity API。記録が検索に出るまで主なサービスで 60〜90 分ほどかかる事 |

## Linux の記録を集める

| 資料 | URL | 確かめた事 |
|---|---|---|
| journal-upload.conf(Debian trixie の man) | https://manpages.debian.org/trixie/systemd-journal-remote/journal-upload.conf.5.en.html | 設定のファイルの場所、`[Upload]` の節、`URL=`、`ServerKeyFile=`、`ServerCertificateFile=`、`TrustedCertificateFile=` |
| systemd-journal-remote.service(Debian trixie の man) | https://manpages.debian.org/trixie/systemd-journal-remote/systemd-journal-remote.service.8.en.html | 既定の受ける口が 19532 である事、既定の置き場が `/var/log/journal/remote/` である事 |
| rsyslog omfwd | https://docs.rsyslog.com/doc/configuration/modules/omfwd.html | `action(type="omfwd" target=… port=… protocol="tcp" queue.type="linkedList")` の書き方と、`queue.type` が送り先の停止で送る側を止めないためである事 |
| rsyslog imtcp | https://docs.rsyslog.com/doc/configuration/modules/imtcp.html | `module(load="imtcp")` と `input(type="imtcp" port="514")` の書き方 |

## 被害の情勢

| 資料 | URL | 確かめた事 |
|---|---|---|
| 警察庁 令和 8 年版 警察白書 第 3 章 第 4 節 | https://www.npa.go.jp/hakusyo/r08/honbun/html/cc3314000.html | 令和 7 年のランサムウェアの被害の調べ(有効回答 92 件)で、VPN 機器からの侵入が 61 件(66.3%)、リモートデスクトップからが 19 件(20.7%)であり、大半は機器の弱いところや強度の弱い認証用のパスワードなどを使ったと考えられる事 |

## この机の PC で確かめた事

Debian 13(systemd 257)の机の PC で、sudo なしで次を打ち、形が通る事を確かめました(出力の中身は記録していません)。

- `journalctl`(`_COMM=sshd`、`_COMM=sudo`、`-t useradd -t groupadd -t usermod -t gpasswd`、`--grep`、`-o json`、`-o short-iso`、`--list-boots`、`--disk-usage`、`--verify`、`-k`、`-n 1`)
- `last -F -n 3`、`last -j --time-format iso -s "YYYY-MM-DD HH:MM:SS"`(wtmpdb の `last`)、`who -b`
- `timedatectl show -p NTPSynchronized --value`
- `ss -ltnH`、`ss -tnH state established`
- `getent group sudo`
- `systemctl --failed`、`systemctl list-timers --all`、`systemctl list-units --type=service --state=running`、`systemctl list-units --type=timer --all`
- `find ~/.ssh -name authorized_keys -newermt '1 day ago'`
- `scripts/mask.py`、`scripts/fail_then_ok.py`、`scripts/off_hours.py`(架空の記録で動きを、この机の PC の journald の出力で落ちない事を確かめた)

確かめていない物: rsyslog、systemd-journal-remote、auditd、nftables(この机の PC に入っていない)、Windows と Microsoft 365 での実行、ルーターと VPN 機器の syslog の設定、GeoIP の対応表。
