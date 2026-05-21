# Third-Party Notices

This project records third-party dependencies and public data sources here.

## Python Dependencies

Runtime dependencies are intentionally minimal. The core package currently uses the Python standard library and SQLite.

Development dependencies:

| Package | Purpose | License |
| --- | --- | --- |
| `ruff` | linting | MIT |
| `build` | package build | MIT |
| `hatchling` | PEP 517 build backend | MIT |

## Public Data Sources

| Source | URL | Use |
| --- | --- | --- |
| 国家法律法规数据库 | https://flk.npc.gov.cn | laws, administrative regulations, judicial interpretations, selected public normative texts |
| 国家行政法规库 | https://www.gov.cn/zhengce/xzfgk/ | administrative regulation discovery / fetch adapter |
| 最高人民法院公报 | http://gongbao.court.gov.cn | judicial interpretations and court gazette materials |
| 最高人民法院 | https://www.court.gov.cn | selected judicial documents from the main site |
| 最高人民检察院 | https://www.spp.gov.cn | selected prosecutorial judicial interpretations / normative documents |
| 中国证监会 | https://www.csrc.gov.cn | selected securities regulatory rules |
| 证券交易所 / 自律组织公开规则 | official exchange / association sites | selected securities self-regulatory rules |

Legal, administrative, judicial, and other official normative documents are not protected by copyright under Article 5 of the Copyright Law of the People's Republic of China. Website terms, access controls, non-official commentary, commercial database content, and private user materials must still be respected separately.

The project does not include commercial legal database content, paid annotations, editorial summaries, or private user materials.

## Compliance

See [docs/COMPLIANCE.md](docs/COMPLIANCE.md). The project requires conservative request rates, source attribution, no anti-bot bypass, no personal-data scraping, and no bulk mirroring.
