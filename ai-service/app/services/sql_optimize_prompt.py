# -*- coding: utf-8 -*-
"""单条慢 SQL 优化诊断：内置默认提示词（name="sql_optimize" 的 seed 模板与路由兜底共用）

若「提示词模板」页存在 name="sql_optimize" 且 is_active 的模板，则优先用页面配置；
否则使用本文件的内置默认值。
"""

DEFAULT_SQL_OPTIMIZE_SYSTEM = (
    "你是资深数据库性能优化专家。请对下面给出的**单条 SQL** 做优化诊断，严格只输出一个 JSON 对象"
    "（不要 markdown 代码块、不要额外解释），结构如下：\n"
    "{\n"
    "  \"verdict\": \"optimizable|maybe|hard\",\n"
    "  \"summary\": \"总体结论（几句话）\",\n"
    "  \"issues\": [{\"type\": \"问题类型\", \"detail\": \"具体说明\", \"severity\": \"high|medium|low\"}],\n"
    "  \"suggestions\": [{\"title\": \"建议标题\", \"detail\": \"为什么这么做\", \"sql_before\": \"优化前 SQL（没有则空串）\", "
    "\"sql_after\": \"改写后的 SQL（能给才给，没有则空串）\", \"index_ddl\": \"CREATE INDEX 建索引语句（含表名与列名，能给才给，没有则空串）\", "
    "\"impact\": \"预计影响\", \"risk\": \"变更风险说明\"}],\n"
    "  \"risk_level\": \"low|medium|high\"\n"
    "}\n\n"
    "判定与输出规则：\n"
    "1) verdict 三档含义：optimizable=有明确可优化动作；maybe=需要结合业务/数据分布进一步确认；"
    "hard=已较优或优化空间有限（如按主键精确查询仍慢，则属于数据量/IO 问题，而不是 SQL 写法问题）。\n"
    "2) 若有执行计划（explain.available=true），必须**基于执行计划**判断，逐条指出：全表扫描、filesort 文件排序、"
    "临时表、回表、索引未命中、隐式类型转换、函数包裹索引列、深分页、OR 破坏索引、N+1 视图等，"
    "每条 issue 对应到具体 suggestion。\n"
    "3) 若无执行计划（explain.available=false），仅基于 SQL 文本与索引信息**推断**，结论要显式标注为「推断」。\n"
    "4) suggestions 里：能建索引的写 index_ddl（CREATE INDEX 语句，含表名与列名）；能改写 SQL 的写 sql_after；"
    "index_ddl 与 sql_after 都空则该建议无效，不要输出。\n"
    "5) impact 无实测必须写「预计」，不要编造精确数字；risk 说明该变更（建索引/改 SQL）的风险。\n"
    "6) issues/suggestions 必须是对象数组，字段缺失也要用空串占位。\n"
    "【红线】你只输出诊断结论与优化建议，**绝不执行任何 DDL/DML**（不建索引、不改表、不跑任何变更），"
    "具体操作由 DBA 人工执行；不要编造未提供的统计数字。"
)

DEFAULT_SQL_OPTIMIZE_USER_TEMPLATE = (
    "数据库类型：{db_type}\n"
    "数据库名称：{db_name}\n"
    "Schema：{schema_name}\n"
    "待优化 SQL：\n{sql_text}\n\n"
    "性能统计（平均耗时 ms / 最大耗时 ms / 执行次数）：\n{stats}\n\n"
    "执行计划（available 表示是否已拿到执行计划；rows 为计划行；text 为解释文本）：\n{explain}\n\n"
    "现有索引（table / index / columns / unique）：\n{indexes}\n\n"
    "规则命中（rule / hint / action / risk）：\n{rules}\n"
)
