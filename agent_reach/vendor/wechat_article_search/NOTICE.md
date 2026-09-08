# wechat-article-search parser

Source: https://github.com/zjp1997720/wechat-article-search
Mirror commit: `7e1be9a0d5b5a9e6835c83cddb2d79bb9c9fe6b6`
Canonical repository: `zjp1997720/zhijian-skills`
Canonical commit (SOURCE.json): `30423a79ac7890bca3a9cc8592c241e3a9d91e69`
Original script SHA-256: `99d2a14baa382c01ee0ac44a10ac358e9fe2148567f50704ead3a35152c26896`
License: MIT, reproduced in LICENSE.

`parser.cjs` retains the original article-card selectors and parsing functions.
Agent Reach adds explicit blocked/unknown-page detection, fixes China-time date
consistency, and uses a bounded Python transport. Only search listings are read.
The original cookie bootstrap, retry loop and real-URL/article fetching are not
included. Dependency manifests are from the pinned source; npm ci uses its lock.

`run.cjs` reads HTML from stdin. It never makes a network request. The Cheerio
runtime is installed separately under ~/.agent-reach/tools/wechat-article-search.
