---
title: GitHub Actions から Pages にデプロイする最小構成
date: 2026-07-27
icon: 🚀
tags: [GitHub Actions, インフラ]
summary: ブランチ公開ではなく Actions ビルドで Pages を配信する構成。enablement オプションと workflow_run による連鎖デプロイがポイント。
lead: 静的サイトを push のたびに自動デプロイする、いちばん短いワークフローを確認した。
sources:
  - label: actions/deploy-pages
    url: https://github.com/actions/deploy-pages
  - label: actions/configure-pages
    url: https://github.com/actions/configure-pages
---

## 最小のワークフロー

リポジトリのルートをそのまま配信する場合、これだけで足りる。

```yaml
name: Deploy to GitHub Pages

on:
  push:
    branches: [main]
  workflow_dispatch:

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      pages: write
      id-token: write
      contents: read
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/configure-pages@v5
        with:
          enablement: true
      - uses: actions/upload-pages-artifact@v3
        with:
          path: "."
      - uses: actions/deploy-pages@v4
        id: deployment
```

## 押さえておく点

**`permissions` の 3 つは必須**。`pages: write` と `id-token: write` が無いと `deploy-pages` が認証に失敗する。`id-token` は OIDC トークンの発行に使われる。

**`enablement: true`** を付けておくと、リポジトリ設定で Pages のソースが未設定だったりブランチ公開になっていても、Actions ビルドに切り替えてくれる。

ただし **新規リポジトリの初回だけは効かない**。Pages サイトがまだ存在しない状態では「切り替え」ではなく「新規作成」になり、ワークフローの `GITHUB_TOKEN` にはその権限が無いため `Resource not accessible by integration` で落ちる。`permissions: pages: write` を宣言していても足りない。

初回だけ手で有効化する。

```bash
gh api -X POST repos/<owner>/<repo>/pages -f build_type=workflow
```

2回目以降は既にサイトが存在するので、`enablement: true` のまま通る。

**`concurrency` で `cancel-in-progress: false`**。デプロイは途中でキャンセルすると中途半端な状態になりうるので、走っているものは完走させて後続を待たせる。ビルドと違ってここは並列化の旨味がない。

## 別のワークフローに続けてデプロイする

データ取得ジョブが先にあって、その完了後にデプロイしたい場合は `workflow_run` を使う。

```yaml
on:
  push:
    branches: [main]
  workflow_run:
    workflows: ["Fetch Events"]
    types: [completed]
```

ただし `workflow_run` は **失敗したときも発火する**。成功時だけ動かすには job 側でガードが要る。

```yaml
    if: ${{ github.event_name != 'workflow_run' || github.event.workflow_run.conclusion == 'success' }}
```

さらに `workflow_run` の checkout は既定でトリガー元のコミットを取る。先行ジョブがデータをコミットしている場合、その最新を拾うために `ref` を明示する。

```yaml
      - uses: actions/checkout@v4
        with:
          ref: main
```

ここを省くと「データは更新されたのにサイトが古いまま」という分かりにくい状態になる。
