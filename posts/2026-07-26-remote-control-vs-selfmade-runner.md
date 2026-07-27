---
title: "自作しようとしていた仕組みが Claude Code の標準機能とほぼ同じだった"
date: 2026-07-26
icon: 📱
tags: [Claude Code, 設計]
summary: モバイルから指示して自分のサーバで Claude Code を動かすだけなら、Webhook 受信もトンネルも署名検証もキューも worktree 管理も要らない。Remote Control のサーバモードと Channels の役割の違いを整理した。
lead: 半日かけて作ったものの大半が、コマンド1つで置き換わることに気づいた日の記録。
sources:
  - label: Claude Code — Remote Control
    url: https://code.claude.com/docs/en/remote-control
  - label: Claude Code — Channels
    url: https://code.claude.com/docs/en/channels
---
## 結論

「モバイルから指示して、自分のサーバで Claude Code を動かす」だけが目的なら、
公開エンドポイント・トンネル・署名検証・キュー・worktree 管理を自作する必要はない。

```bash
cd <プロジェクト>
claude remote-control --spawn worktree
```

これをサーバで常駐させると、モバイルアプリの Code タブからそのサーバのセッションを新規に
立てられる。`--spawn worktree` を付けると、**アプリから作るセッションごとに git worktree が
自動で切られる**。

## 何が置き換わるか

| 自作していた部品 | 標準機能での相当 |
|---|---|
| 公開エンドポイント + トンネル + Webhook 署名検証 | 不要（アプリ ↔ サーバが直接つながる） |
| 同時実行数の管理 | `--capacity`（既定 32 セッション） |
| worktree の作成と分離 | `--spawn worktree` |
| 非対話実行で権限をどう通すか | アプリ側で許可プロンプトを承認できる |

最後の行が効く。`claude -p`（非対話・一発実行）では許可を求められるツールが通らないので、
権限モードを緩める設定が必要だった。対話セッションなら、その場でスマホから承認すればいい。
**問題が消える。**

`--no-create-session-in-dir` を付ければ、起動時はセッション0個で待機する。

## Remote Control と Channels は別物

混同しやすいので整理しておく。

**Remote Control** はアプリからサーバ上のセッションを覗く／新規に立てる仕組み。
ただし CLI のヘルプにこう書かれているとおり、**対話セッション専用**。

```
--remote-control [name]    Start an interactive session with Remote Control enabled
```

`claude -p` で起動している限り、アプリには出てこない。

**Channels** はチャットアプリからのイベントを受ける仕組み。ここが直感と食い違う。

> Events only arrive while the session is open, so for an always-on setup you run Claude in a
> background process or persistent terminal.

**新しいセッションを立てない。すでに開いているセッションにメッセージを注入する。**

したがって使い分けはこうなる。

| やりたいこと | 使うもの |
|---|---|
| 指示するたびに独立した作業を並列で走らせたい | Remote Control のサーバモード |
| 常駐している1つのセッションと会話し続けたい | Channels |

## Slack は要件と食い違う

| 方式 | Claude が動く場所 | Slack |
|---|---|---|
| 公式 Slack 連携（メンション） | Anthropic のクラウド | 対応 |
| Channels（チャットアプリ → ローカル） | 自分のサーバ | Slack プラグインは無い |

Channels の公式プラグインは Telegram / Discord / iMessage。
**「Slack から指示 → 自分のサーバで実行」は、公式連携ではできない**（クラウド実行になる）。
自分のマシンで動かすことが要件なら、チャットアプリの選択がそのまま制約になる。

## トレードオフ

導入前に確認しておくべき点。

- **接続中は、会話とツール活動のトランスクリプトが Anthropic 側に保存される**
  （デバイス間の同期と再接続のため）。実行とファイルアクセスはローカルのままだが、
  「コードを外部サービスに預けなくてよい」が動機だったなら、方針の再確認が要る
- **認証は claude.ai の OAuth（フルスコープ）が必須。** `claude setup-token` や
  長寿命トークンの環境変数では Remote Control が張れない。トークン運用で完全無人常駐に
  寄せる計画とは両立しない
- **ネットワーク断が約10分続くとセッションが終了する。** 自宅回線では起こりうるので、
  再起動の仕掛けが要る
- 対話 TUI なので、常駐させるなら tmux 越しが無難（TTY 無しで動くかは未検証）
- どちらも research preview

## 自作側に残る価値

全部が無駄になるわけではない。Issue を起点にした仕組みには、
**Issue という永続的な記録が残る**という性質がある。アプリのセッション一覧は、
あとから追える作業ログとしては別物。

## 設計メモ（未検証）

非対話実行を対話セッションに変えると、**完了検知が壊れる**。
「プロセスが終了したら通知する」という設計は、自分で終わらない対話セッションでは成立しない。
Claude Code の `Stop` フック（応答を終えたときに発火し `session_id` と最終応答を受け取れる）や
`SessionEnd` に置き換えるのが素直、というところまでで、実装しての確認はしていない。
