# SJTU 课程数据 SQLite

这个项目把 SJTU 课程评论和当前学期教学班数据整理进一个简单的 SQLite 数据库。

## 主要文件

- `data/raw/history_all_terms/course_community_reviews_all_terms.csv`：历史课程评论
- `data/processed/current_term_2025-2026_1/course_plus_offerings_2025-2026_1_cleaned.csv`：当前学期教学班
- `data/processed/course_reviews_simple.sqlite`：生成后的 SQLite 数据库
- `src/sqlite_basic_usage.ipynb`：SQLite 基础使用示例

## 生成 SQLite

先生成评论表：

```bash
python src/ingest_course_community.py
```

再直接抓取 Course+ 并写入当前学期教学班表：

```bash
python src/ingest_course_plus.py --semester 2025-2026_1
```

如果只想导入已经清洗好的 CSV：

```bash
python src/ingest_course_plus.py --input-csv data/processed/current_term_2025-2026_1/course_plus_offerings_2025-2026_1_cleaned.csv
```

如果抓取时也想保留 raw/processed CSV：

```bash
python src/ingest_course_plus.py --semester 2025-2026_1 --save-csv
```

## SQLite 表

`course_teacher_reviews`

- 评论明细表
- 可通过 `course_code` + `course_teacher` 查询某门课某位老师的所有评论

`course_teacher_rating_summary`

- 评论评分汇总表
- 可通过 `course_code` + `course_teacher` 查询评论数和平均评分

`course_plus_offerings`

- 当前学期教学班表
- 可通过 `course_code` 查询所有教学班信息
- 包含 `schedule_code`，把中文排课文本转成更方便处理的编码

## schedule_code 例子

原始文本：

```text
星期一第1-2节{1-8周};星期五第1-2节{1-8周}
```

编码后：

```text
D1:P1-2:W1-8;D5:P1-2:W1-8
```

含义：

- `D1` 到 `D7`：星期一到星期日
- `P1-2`：第 1-2 节
- `W1-8`：第 1-8 周

## 学习 SQL

打开：

```text
src/sqlite_basic_usage.ipynb
```

里面有连接数据库、查询表、筛选、排序、聚合和教学班查询示例。
