# Per-page results

`Visual` is the LibreOffice rendered visual 2.2 score. `Gate score` is the
fraction of measured project gates that passed; it is not a semantic-accuracy
score. Empty failed-gate cells are the two accepted pages.

| Case | Accepted | Visual | Gate score | Failed gates |
| --- | --- | ---: | ---: | --- |
| odb-demo-00-yanbaopptmerge_SE05.pdf_7 | no | 0.126528 | 0.975000 | source_row_gap_sanity |
| odb-demo-01-yanbaopptmerge_yanbaoPPT_145 | no | 0.073059 | 0.829268 | native_body_columns, native_body_column_payload, native_body_column_flow_safety, source_visual_slot_coverage, mapped_vertical_span, source_row_gap_sanity, rendered_body_foreground_coverage |
| odb-demo-02-docstructbench_llm-raw-scihub-o.O-j.chroma.2005.05.085.pdf_4 | no | 0.055031 | 0.731707 | native_content_projection, native_office_math_count, office_math_structure, supported_math_controls, native_body_columns, native_body_column_payload, native_body_column_flow_safety, source_geometry_placements, rendered_page_count, rendered_physical_page_size, rendered_body_foreground_coverage |
| odb-demo-03-docstructbench_llm-raw-scihub-o.O-j.physletb.2004.06.101.pdf_3 | no | 0.163495 | 0.925000 | supported_math_controls, rendered_page_count, rendered_physical_page_size |
| odb-demo-04-docstructbench_dianzishu_zhongwenzaixian-o.O-60599898.pdf_30 | no | 0.395487 | 0.926829 | mapped_vertical_span, source_row_gap_sanity, native_body_flow_sanity |
| odb-demo-05-docstructbench_dianzishu_zhongwenzaixian-o.O-61522235.pdf_170 | yes | 0.404388 | 1.000000 | |
| odb-demo-06-docstructbench_dianzishu_zhongwenzaixian-o.O-61520814.pdf_185 | no | 0.184782 | 0.825000 | supported_math_controls, source_visual_slot_coverage, source_geometry_placements, mapped_vertical_span, source_anchor_order, rendered_page_count, rendered_physical_page_size |
| odb-demo-07-docstructbench_dianzishu_zhongwenzaixian-o.O-61569294.pdf_128 | yes | 0.101864 | 1.000000 | |
| odb-demo-08-jiaocaineedrop_Chapter9.pdf_46 | no | 0.207021 | 0.707317 | native_content_projection, native_office_math_count, office_math_structure, native_body_columns, native_body_column_payload, native_body_column_flow_safety, source_visual_slot_coverage, source_geometry_placements, mapped_vertical_span, source_anchor_order, source_row_gap_sanity, native_body_flow_sanity |
| odb-demo-09-jiaocaineedrop_Evans_PDE_Solution_Chapter_6_Second-Order_Elliptic_Equations.pdf_5 | no | 0.205562 | 0.900000 | native_office_math_count, office_math_structure, supported_math_controls, native_source_footers |
| odb-demo-10-jiaocaineedrop_jiaocai_needrop_en_1898 | no | 0.117133 | 0.875000 | source_geometry_placements, mapped_vertical_span, source_anchor_order, rendered_page_count, rendered_physical_page_size |
| odb-demo-11-jiaocaineedrop_jiaocai_needrop_en_3361 | no | 0.029909 | 0.707317 | native_content_projection, native_office_math_count, office_math_structure, supported_math_controls, native_body_columns, native_body_column_payload, native_body_column_flow_safety, mapped_vertical_span, rendered_page_count, rendered_physical_page_size, rendered_body_foreground_coverage, rendered_visual_similarity |
| odb-demo-12-notes_1ba14cb325bc448f7201b20502ecf2b5_15 | no | 0.381989 | 0.925000 | source_visual_slot_coverage, source_geometry_placements, mapped_vertical_span |
| odb-demo-13-notes_f7f010b78016aeebd76e56d9283eb67f_49 | no | 0.384215 | 0.925000 | source_visual_slot_coverage, source_geometry_placements, mapped_vertical_span |
| odb-demo-14-newspaper_1cddf9d22ca549f3a86cf1512a3110cc_1 | no | 0.234155 | 0.926829 | source_row_gap_sanity, rendered_page_count, rendered_physical_page_size |
| odb-demo-15-newspaper_5e266dfd9c498cab274e12a7b4a75755_4 | no | 0.135595 | 0.926829 | source_row_gap_sanity, rendered_page_count, rendered_physical_page_size |
| odb-demo-16-eastmoney_62b4149b1612ce28d20f26cd5c5b2e18f80b26fca6e4452e090376a2fe72eae3.pdf_0 | no | 0.361649 | 0.926829 | source_visual_slot_coverage, source_geometry_placements, mapped_vertical_span |
| odb-demo-17-yanbaopptmerge_0c79d327060dbf9f1582d03c235dadb039533a19091d2c0d24f2ad95d267f79b.pdf_2 | no | 0.304507 | 0.925000 | source_visual_slot_coverage, source_geometry_placements, mapped_vertical_span |

The failed gates are deliberately published rather than collapsed into a single
score. They define the next renderer/planner work: page geometry and pagination,
multi-column flow, source-geometry coverage, math preservation, and vertical-flow
constraints.

