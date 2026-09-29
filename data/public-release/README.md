# chinalaw 公开法规快照

这是一份可离线使用的全国层级法规数据包。数据来自官方公开文件，以 JSON 和 SQLite 两种形式提供。请先阅读 NOTICE.md 了解上游出处、许可边界与致谢。

- 快照日期、记录数、条文数、版本和 SHA-256 以 manifest.json / SHA256SUMS 为准。
- 数字包含历史版本及“正文”条目，不等于独立现行法律的数量。
- 缺少施行/废止日期、状态未知、本地维护的历史文本均保留原有标记。历史案件必须进一步核对时间效力。
- 这不是全中国全部规范库；没有承诺覆盖全部地方法规、部门规章或司法案例。
- 不含线上旧 ID 兼容副本，因此记录数小于托管实例。

## 安装工具

从 https://github.com/nh59yytyd5-dev/chinalaw-cli/releases/tag/v0.7.0 下载 wheel 后：

```sh
python -m pip install ./chinalaw-0.7.0-py3-none-any.whl
```

## JSON 包

解压后把 laws/ 下的记录导入一个新库，避免误覆盖自己的工作库：

```sh
chinalaw --db ./public-library.db sync --from-dir ./chinalaw-public-2026-09-26-json/laws
chinalaw --db ./public-library.db article 民法典 533
```

## SQLite 包

解压后即可查询：

```sh
chinalaw --db ./chinalaw-public-2026-09-26-sqlite/library.db status
chinalaw --db ./chinalaw-public-2026-09-26-sqlite/library.db article 民法典 533
chinalaw-mcp --db ./chinalaw-public-2026-09-26-sqlite/library.db
```

数据库使用兼容旧版 SQLite 的全文索引布局，不含 `contentless_delete` 选项；需要支持 FTS5/trigram 的 SQLite（3.34+），chinalaw 0.7.0+。若目标 SQLite 不兼容，使用 JSON 包在目标机器导入、重建索引。

## 核验与更新

先核对发布页 SHA256SUMS，再解压。新快照应先导入新库并核验，再决定是否替换现有库；认证状态和私域资料始终由使用者自己维护。
模型记忆可提供候选法规/条号，最终引用请核对 article 返回的正文、版本、来源。search 用于补缺，不要求先做学术术语搜索。
