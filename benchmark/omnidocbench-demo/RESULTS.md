# Per-case baseline results

Dataset revision: `193627ae9e97d89188468ed1ee3b7a856ff76044`

Project revision: `11321aa920917b8c946dec6c5a95fe0f6fa1127e`

Metric profile: `rendered_visual|backend=libreoffice|metric=2.1`

Failures are not excluded from the aggregate. An operational failure has a
rendered quality score of `0`.

| # | Official demo page | Class | Operational | Visual | Accepted | Failed gates / failure |
| ---: | --- | --- | --- | ---: | --- | --- |
| 1 | `yanbaopptmerge_SE05.pdf_7.jpg` | PPT2PDF | yes | 0.126528 | no | `source_row_gap_sanity` |
| 2 | `yanbaopptmerge_yanbaoPPT_145.jpg` | PPT2PDF | yes | 0.073059 | no | columns, column payload/flow, visual slots, vertical span, row gaps, foreground coverage |
| 3 | `docstructbench_llm-raw-scihub-o.O-j.chroma.2005.05.085.pdf_4.jpg` | academic literature | yes | 0.055031 | no | content/math projection, columns, geometry, page count/size, foreground coverage |
| 4 | `docstructbench_llm-raw-scihub-o.O-j.physletb.2004.06.101.pdf_3.jpg` | academic literature | yes | 0.163495 | no | unsupported math controls, page count, physical page size |
| 5 | `docstructbench_dianzishu_zhongwenzaixian-o.O-60599898.pdf_30.jpg` | book | yes | 0.395487 | no | vertical span, row gaps, body flow |
| 6 | `docstructbench_dianzishu_zhongwenzaixian-o.O-61522235.pdf_170.jpg` | book | yes | 0.404388 | **yes** | none |
| 7 | `docstructbench_dianzishu_zhongwenzaixian-o.O-61520814.pdf_185.jpg` | colorful textbook | **no** | 0 | no | strict evidence alignment |
| 8 | `docstructbench_dianzishu_zhongwenzaixian-o.O-61569294.pdf_128.jpg` | colorful textbook | **no** | 0 | no | strict evidence alignment |
| 9 | `jiaocaineedrop_Chapter9.pdf_46.jpg` | exam paper | **no** | 0 | no | strict evidence alignment |
| 10 | `jiaocaineedrop_Evans_PDE_Solution_Chapter_6_Second-Order_Elliptic_Equations.pdf_5.jpg` | exam paper | **no** | 0 | no | strict evidence alignment |
| 11 | `jiaocaineedrop_jiaocai_needrop_en_1898.jpg` | magazine | **no** | 0 | no | strict evidence alignment |
| 12 | `jiaocaineedrop_jiaocai_needrop_en_3361.jpg` | magazine | **no** | 0 | no | strict evidence alignment |
| 13 | `notes_1ba14cb325bc448f7201b20502ecf2b5_15.jpg` | note | **no** | 0 | no | strict evidence alignment |
| 14 | `notes_f7f010b78016aeebd76e56d9283eb67f_49.jpg` | note | **no** | 0 | no | strict evidence alignment |
| 15 | `newspaper_1cddf9d22ca549f3a86cf1512a3110cc_1.jpg` | newspaper | **no** | 0 | no | strict evidence alignment |
| 16 | `newspaper_5e266dfd9c498cab274e12a7b4a75755_4.jpg` | newspaper | **no** | 0 | no | strict evidence alignment |
| 17 | `eastmoney_62b4149b1612ce28d20f26cd5c5b2e18f80b26fca6e4452e090376a2fe72eae3.pdf_0.jpg` | research report | yes | 0.361649 | no | visual slots, geometry placements, vertical span |
| 18 | `yanbaopptmerge_0c79d327060dbf9f1582d03c235dadb039533a19091d2c0d24f2ad95d267f79b.pdf_2.jpg` | research report | yes | 0.304507 | no | visual slots, geometry placements, vertical span |

## Failure-inclusive slices

| Slice | Cases | Operational success | Mean rendered quality |
| --- | ---: | ---: | ---: |
| English | 7 | 42.86% | 0.049293 |
| Simplified Chinese | 10 | 50.00% | 0.153909 |
| Mixed English/Chinese | 1 | 0% | 0 |
| Academic literature | 2 | 100% | 0.109263 |
| Book | 2 | 100% | 0.399937 |
| PPT2PDF | 2 | 100% | 0.099793 |
| Research report | 2 | 100% | 0.333078 |
| Colorful textbook | 2 | 0% | 0 |
| Exam paper | 2 | 0% | 0 |
| Magazine | 2 | 0% | 0 |
| Notes | 2 | 0% | 0 |
| Newspaper | 2 | 0% | 0 |
| Fuzzy scan | 2 | 0% | 0 |

These values are an initial failure map, not evidence of generalization. With
only two pages per document class, no confidence interval would be meaningful.
