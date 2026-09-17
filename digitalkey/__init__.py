"""digitalkey — 扉の鍵。

entrance(玄関、旧genkan): 様式・台帳・鍵の発行と失効・本人確認・報告・金庫。鍵の発行と、機器と業務のつなぎ。
door(扉、旧tobira core): 扉側のコントローラ。火災・停電・避難の判断、監査の連鎖、接点、HTTPの門。ローカルだけで完全に動く。
panel(旧tobira panel): 扉をMatterの錠として見せる橋と、その盤(Flet)。判断は door が持つ。
"""
__version__ = "0.1.0"
