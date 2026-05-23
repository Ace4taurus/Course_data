# SJTU 选课数据整理

本项目只保留数据抓取、清洗、聚合和存储，不包含分析报告、统计脚本或可视化产物。

## 目录结构

- `data/raw/`
  原始数据。
- `data/processed/`
  清洗和聚合后的数据。
- `src/run_isolated_course_analysis.py`
  主入口，负责抓取并整理数据。
- `src/sjtu_course_analysis/course_plus.py`
  `Course+` 课程数据抓取与清洗。
- `src/sjtu_course_analysis/course_community.py`
  选课社区登录、点评抓取与聚合。

## 当前数据

- `data/raw/history_all_terms/course_community_reviews_all_terms.csv`
  选课社区全历史逐条点评原始数据。
- `data/processed/history_all_terms/course_community_courses_all_terms.csv`
  选课社区全历史课程层聚合数据。
- `data/raw/current_term_2025-2026_1/course_plus_raw_2025-2026_1.csv`
  `2025-2026_1` 学期 `Course+` 原始课程数据。
- `data/processed/current_term_2025-2026_1/course_plus_offerings_2025-2026_1.csv`
  `2025-2026_1` 学期教学班层数据。
- `data/processed/current_term_2025-2026_1/course_plus_courses_2025-2026_1.csv`
  `2025-2026_1` 学期课程层聚合数据。
- `data/processed/current_term_2025-2026_1/history_review_aggregate_reference.csv`
  历史点评课程聚合参考表。
- `data/processed/current_term_2025-2026_1/merged_current_courses_with_history_reviews_2025-2026_1.csv`
  当前学期课程与历史点评聚合结果的合并表。

## 运行方式

```bash
/opt/anaconda3/bin/python src/run_isolated_course_analysis.py \
  --semester 2025-2026_1 \
  --community-username YOUR_USERNAME \
  --community-password YOUR_PASSWORD \
  --community-login-mode email_password \
  --community-transport curl \
  --output-root .
```

## 说明

- 当前仓库不保留任何分析报告或图表。
- 评论正文只保存在 `data/raw/history_all_terms/`。
- `score` 字段是原始字段，未做分析解释。
