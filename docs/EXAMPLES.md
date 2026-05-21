# Examples

## 1. First Run

```bash
chinalaw sync --fixtures
chinalaw status --format md
```

Expected result: the local database contains the bundled fixtures and can serve article-level queries without network access.

## 2. Resolve A Common Name

```bash
chinalaw resolve 民法典 --format json
```

Use `resolve` before citation-sensitive work when the user gives a short name, nickname, or ambiguous title.

## 3. Search Before Answering

```bash
chinalaw search 合同效力 --kind article --limit 10 --format json
```

Agent rule: if a legal answer cites an article, the article should come from `article`, `articles`, or a search result that includes source metadata.

## 4. Retrieve Exact Articles

```bash
chinalaw article 民法典 第一百四十三条 --format md
chinalaw articles 民法典 --numbers "143,464,509,577" --format json
```

Prefer `articles` for contract review or memo drafting where multiple provisions are known in advance.

## 5. Check A Missing Article

```bash
chinalaw article 民法典 第五百八十五条 --format json
```

If the result reports `article_null`, `law_stub`, `law_seed`, `needs_fetch`, or another diagnostic error, do not fabricate the text. Use:

```bash
chinalaw ensure 民法典 --format json
chinalaw fetch 民法典 --article 第五百八十五条 --format json
```

Then re-run `article`.

## 6. Inspect Outline

```bash
chinalaw outline 民法典 --limit 30 --format md
```

Use this when the agent needs to understand the nearby article structure before selecting provisions.

## 7. Historical Fact Pattern

```bash
chinalaw applicable --date 2019-05-01 --topic 合同效力 --format json
chinalaw relation 民法典 --format md
```

These commands provide time-effect clues. They do not replace legal analysis. If the local data cannot identify the applicable historical version, say so explicitly and fetch or verify before concluding.

## 8. Minimal Contract Review Flow

```bash
chinalaw search 违约责任 --kind article --limit 10 --format json
chinalaw articles 民法典 --numbers "509,577,584,585" --format json
```

Suggested agent behavior:

1. Extract issues from the contract.
2. Search local law for candidate provisions.
3. Retrieve exact articles.
4. Use only retrieved text in citations.
5. If any provision is missing, fetch or mark the legal basis as unverified.

## 9. Citation Checking

For a draft that says "依据民法典第一百四十三条":

```bash
chinalaw article 民法典 第一百四十三条 --format json
```

Check:

- the law resolved to the intended official title;
- the article exists;
- the article text matches the draft's quotation or paraphrase;
- the law status and source metadata are acceptable for the task.
