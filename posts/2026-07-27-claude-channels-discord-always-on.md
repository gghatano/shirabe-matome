---
title: 外出先から自宅の Claude セッションを動かす（Discord チャネル + systemd 常駐）
date: 2026-07-27
icon: 📡
tags: [Discord, Claude Code, 自動化]
summary: Channels の Discord プラグインは、bot が Discord 側をポーリングする方式なので公開ホストもトンネルも要らない。ただし「セッションを立てる」のではなく「常駐セッションに注入する」点が設計を左右する。
lead: スマホから DM を投げて自宅 PC の Claude に作業させる構成を、実際に動くところまで組んだ記録。
sources:
  - label: Claude Code — Channels reference
    url: https://code.claude.com/docs/en/channels-reference
---
## 結論

Claude Code の Channels（Discord プラグイン）は、**bot が Discord API 側をポーリングする**方式
なので、自宅 PC にポート開放もトンネルも要らない。通信は外向き HTTPS だけで完結する。
Webhook 方式でトンネルに苦労していたなら、その部分がまるごと不要になる。

## 前提が1つ違う

ドキュメントを読んで最初に修正が要った理解がここ。

> Events only arrive while the session is open, so for an always-on setup you run Claude in a
> background process or persistent terminal.

**Channels は「新しいセッションを立てる」のではなく、すでに開いているセッションにメッセージを
注入する。** 指示のたびにセッションが起動するのではなく、常駐している1本と会話し続ける形になる。

これが効いてくる点が3つある。

- 文脈が継続するので「さっきの続きだけど」が通じる
- 逆に独立した案件を並行させるなら別セッションが要る
- **1セッション = 1コンテキスト**なので、長く使うと肥大化する。`/clear` を挟むか定期的に再起動する運用が要る
- セッションが落ちている間に送った DM は届かない（常駐と自動復帰が前提）

## 組んだ構成

```
スマホ ──DM──> Discord API <──ポーリング── 自宅PC の常駐 Claude セッション
                                              （systemd user service → tmux → claude --channels）
```

導入で実際に動いた手順:

```bash
claude plugin marketplace add anthropics/claude-plugins-official
claude plugin install discord@claude-plugins-official
```

前提として **Bun が要る**（公式チャネルプラグインは Bun 前提）。未インストールだったので
`npm install -g bun` で 1.3.14 を入れた。

起動はこれ。

```bash
claude --channels plugin:discord@claude-plugins-official
```

起動バナーの下に `messages from plugin:discord@... inject directly in this session` が出れば
チャネルが有効。

常駐は systemd の user service から tmux セッションを起こす形にした。**`loginctl enable-linger`
を有効にしないと、ログアウトで止まる。**

## トークンの置き場所

```
~/.claude/channels/discord/.env       ← DISCORD_BOT_TOKEN=... （chmod 600）
~/.claude/channels/discord/           ← ディレクトリは 700
```

`/discord:configure <トークン>` というコマンドも用意されているが、**これを使うとトークンが
会話ログに残る**。別ターミナルからファイルに直接書く方が安全。

```bash
printf 'DISCORD_BOT_TOKEN=%s\n' 'トークン' > ~/.claude/channels/discord/.env
chmod 600 ~/.claude/channels/discord/.env
```

## 権限プロンプトは Discord にボタンで飛ぶ

無人運用の最大の壁は、Claude がツール実行の許可を求めて停止することだった。手元にいなければ
そこで詰む。

調査中に、**プラグインが権限プロンプトを Discord のボタンとして中継する実装**を持っていることが
分かった。ソースにこの形がある。

```
🔐 Permission: ${tool_name}
[ ✅ Allow ]  [ ❌ Deny ]
```

実際に DM を送ったところ、Discord にこの承認ボタンが飛んできて、タップしたら返信が届いた。

```
1. 🔐 Permission: mcp__plugin_discord_discord__reply  → Allowed
2. 2026年7月27日(月) 10時43分 (JST) だよ
```

**Remote Control を併用しなくても、Discord だけで承認まで完結する。** ツールを使うたびに
ボタンが飛ぶので、頻度が煩わしければ `--permission-mode` や `--disallowedTools` で調整する
という選択肢もある。

## アクセス制御: `pairing` と `allowlist` の差

`~/.claude/channels/discord/access.json` にポリシーがある。当初「`allowlist` にしないと誰でも
自分のマシンで Claude を動かせる」と理解していたが、これは言い過ぎだった。ソースを読んだ結果:

| ポリシー | allowlist 外からの DM |
|---|---|
| `pairing`（既定） | ペアリングコードを返信し、**メッセージは破棄** |
| `allowlist` | **黙って破棄**。返信もしない |

つまり `pairing` の時点で、知らない相手のメッセージがセッションに入ることはない。承認するか
どうかは人が決める設計になっている。差は「見知らぬ相手にコードを返信するか（＝ bot の存在と
反応を晒すか）」の部分。

登録は `/discord:access pair <コード>` → `/discord:access policy allowlist`。このスキルは
**ターミナルで人が打ったときだけ動く**設計になっていて、チャネル経由の依頼では実行を拒否する。
アクセス制御の変更を信頼できない入力の下流に置かない、という考え方。

## そのほか設定できるもの

| キー | 用途 |
|---|---|
| `ackReaction` | 受信時に絵文字でリアクション（「受け取った」の可視化） |
| `textChunkLimit` | 長文の分割サイズ（Discord の上限は 2000 文字） |
| `chunkMode` | 分割位置（`newline` なら段落境界で切る） |

`ackReaction` に 👀 を設定した。モバイルだと「届いたけどまだ考え中」が分かるので地味に効く。
