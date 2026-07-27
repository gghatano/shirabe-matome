---
title: ダークモードのちらつきを消す
date: 2026-07-19
icon: 🌙
tags: [フロントエンド, UI]
summary: テーマ適用を CSS 読み込み前のインラインスクリプトで行うと、初回描画の白フラッシュが消える。OS設定とユーザー選択の優先順位も整理する。
lead: リロードのたびに一瞬白くなる、あの現象の原因と直し方。
---

## 症状

`localStorage` に保存したテーマを `DOMContentLoaded` で適用していると、リロード時に一瞬ライトテーマが描画されてから暗くなる。ダークモードで使っている人には毎回まぶしい。

## 原因

`DOMContentLoaded` は HTML のパースが終わってから発火する。それより前にブラウザは最初の描画を始めているので、既定のスタイル（＝ライト）が見えてしまう。

## 直し方

`<head>` の中、**スタイルシートを読み込む前** に同期スクリプトを置く。

```html
<script>
  try {
    var t = localStorage.getItem("theme");
    if (t !== "dark" && t !== "light") {
      t = (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches)
        ? "dark" : "light";
    }
    document.documentElement.dataset.theme = t;
  } catch (e) {}
</script>
<link rel="stylesheet" href="base.css">
```

同期スクリプトはパースをブロックするので、この時点で `<html data-theme="dark">` が確定する。CSS が読まれた瞬間には既に正しいテーマが当たっていて、白い状態は一度も描かれない。

外部ファイルにせずインラインで書くのが必須。外部にすると取得待ちのぶんだけ描画が遅れる。数行なのでインラインのコストは無視できる。

## 優先順位

1. ユーザーが明示的に切り替えた値（`localStorage`）
2. OS の設定（`prefers-color-scheme`）
3. ライト

`localStorage` の値は `"dark"` / `"light"` のどちらかだけを信用する。壊れた値や `null` が入っていたら OS 設定に落とす。上のコードで `t !== "dark" && t !== "light"` と両方を明示チェックしているのはそのため。`if (!t)` だと不正な文字列がすり抜ける。

`try / catch` で囲むのは、プライバシー設定によっては `localStorage` へのアクセス自体が例外を投げるため。ここで落ちるとページ全体のスクリプトが止まる。

## CSS 側

`:root` に変数を並べ、`:root[data-theme="dark"]` で上書きする。

```css
:root { --bg: #f3f4f6; --text: #1f2330; }
:root[data-theme="dark"] { --bg: #0e131a; --text: #e6e8eb; }
```

`@media (prefers-color-scheme: dark)` だけで組むと、ユーザーによる手動切り替えができない。`data-theme` 属性を単一の情報源にして、OS 設定は「初期値をどちらにするか」の判断にだけ使う、と割り切ると分岐が増えない。
