# -*- coding: utf-8 -*-
"""表结构对比（R16 §16.8）——纯函数，不连库。

输入是两张表的归一化结构 dict（与 `table_schema` 返回同形，见 dialects/mysql.py 的
`_mysql_schema_to_dict`）：columns / indexes / 表选项（engine/charset/collation/comment）。

输出三态 diff：added（目标有源无）/ removed（源有目标无）/ modified（两边有但不同）。
字段按 name 匹配、索引按 name 匹配、表选项逐 key 比较。
"""

# 参与对比的表选项 key（引擎/字符集/排序规则/表注释）
OPTION_KEYS = ("engine", "charset", "collation", "comment")

# 参与「字段是否被修改」比较的字段属性
_COL_CMP_KEYS = ("type", "nullable", "default", "extra", "comment")
_IDX_CMP_KEYS = ("unique", "type", "columns")


def _cmp_col(c: dict) -> dict:
    """字段参与对比的属性快照（default 归一化为字符串，避免 None/0 差异误报）。"""
    out = {}
    for k in _COL_CMP_KEYS:
        v = c.get(k)
        out[k] = "" if v is None else v
    return out


def _cmp_idx(i: dict) -> dict:
    out = {}
    for k in _IDX_CMP_KEYS:
        v = i.get(k)
        if k == "columns":
            out[k] = tuple(v or [])
        else:
            out[k] = v
    return out


def _diff_list(source_items: list[dict], target_items: list[dict],
               key_fn, cmp_fn) -> dict:
    """按 key 匹配的两集合 diff，返回 {added, removed, modified}。

    added/removed 里放原始 dict；modified 里放 {key, before, after}。
    """
    s = {key_fn(x): x for x in (source_items or [])}
    t = {key_fn(x): x for x in (target_items or [])}
    added, removed, modified = [], [], []
    for k, item in t.items():
        if k not in s:
            added.append(item)
    for k, item in s.items():
        if k not in t:
            removed.append(item)
        elif cmp_fn(s[k]) != cmp_fn(t[k]):
            modified.append({"key": k, "before": s[k], "after": t[k]})
    return {"added": added, "removed": removed, "modified": modified}


def _diff_options(source: dict, target: dict) -> dict:
    modified = []
    for k in OPTION_KEYS:
        sv = source.get(k) if source else None
        tv = target.get(k) if target else None
        if (sv or "") != (tv or ""):
            modified.append({"key": k, "before": sv or "", "after": tv or ""})
    # 表选项只有「修改」，无增删三态
    return {"added": [], "removed": [], "modified": modified}


def _col_rename_key(c: dict) -> tuple:
    """改名启发式的配对键：同类型 + 同默认值（位置在调用处比对）。"""
    return (
        str(c.get("type") or "").strip().lower(),
        "" if c.get("default") is None else str(c.get("default")).strip(),
    )


def _diff_columns(source: list[dict], target: list[dict]) -> dict:
    """字段三态 diff + 改名启发式配对（R51）。

    先按 name 匹配得 added/removed/modified；再把「删掉的」与「新增的」按
    「同位置（列序）+ 同类型 + 同默认值」贪心一对一配对，配上的标为 renamed
    （疑似改名），从 added/removed 中剔除，避免误判为「删 + 增」。
    """
    source = source or []
    target = target or []
    s = {c.get("name"): c for c in source}
    t = {c.get("name"): c for c in target}

    modified = []
    for name, item in s.items():
        if name in t and _cmp_col(item) != _cmp_col(t[name]):
            modified.append({"key": name, "before": item, "after": t[name]})

    removed = [(c, i) for i, c in enumerate(source) if c.get("name") not in t]
    added = [(c, i) for i, c in enumerate(target) if c.get("name") not in s]

    renamed = []
    used: set[int] = set()
    for rc, ridx in removed:
        for ac, aidx in added:
            if aidx in used:
                continue
            if ridx == aidx and _col_rename_key(rc) == _col_rename_key(ac):
                renamed.append({"from": rc.get("name"), "to": ac.get("name"),
                                "before": rc, "after": ac})
                used.add(aidx)
                break

    renamed_src = {r["from"] for r in renamed}
    renamed_tgt = {r["to"] for r in renamed}
    removed_out = [c for c, _ in removed if c.get("name") not in renamed_src]
    added_out = [c for c, _ in added if c.get("name") not in renamed_tgt]
    return {"added": added_out, "removed": removed_out,
            "modified": modified, "renamed": renamed}


def compare_table_schema(source: dict, target: dict) -> dict:
    """对比两张表结构，返回 {columns, indexes, options} 三态 diff（columns 含 renamed）。"""
    source = source or {}
    target = target or {}
    return {
        "columns": _diff_columns(source.get("columns"), target.get("columns")),
        "indexes": _diff_list(
            source.get("indexes"), target.get("indexes"),
            key_fn=lambda i: i.get("name"), cmp_fn=_cmp_idx),
        "options": _diff_options(source, target),
    }


def diff_to_changes(diff: dict) -> dict:
    """把 diff 转成 `build_table_alter` 可消费的 changes dict（DbTableChanges 同形）。

    - added 列 → add_columns；removed 列 → drop_columns；modified 列 → modify_columns（取 after）；
      renamed 列（改名启发式配对）→ change_columns（from=旧名 + 目标列定义）。
    - added 索引 → add_indexes；removed 索引 → drop_indexes。
    - 表选项 modified → engine/charset/collation/comment。
    """
    diff = diff or {}
    cols = diff.get("columns") or {}
    idxs = diff.get("indexes") or {}
    opts = diff.get("options") or {}

    def _col_out(c: dict) -> dict:
        return {
            "name": c.get("name"),
            "type": c.get("type") or "",
            "nullable": bool(c.get("nullable", True)),
            "default": c.get("default"),
            "comment": c.get("comment") or "",
            "auto_increment": "auto_increment" in ((c.get("extra") or "").lower()),
        }

    changes: dict = {}
    if cols.get("added"):
        changes["add_columns"] = [_col_out(c) for c in cols["added"]]
    if cols.get("removed"):
        changes["drop_columns"] = [c.get("name") for c in cols["removed"]]
    if cols.get("renamed"):
        # 改名 → change_columns（from=旧名，after=目标列完整定义），不落成删+增
        changes["change_columns"] = [
            {**_col_out(r["after"]), "from": r["from"]} for r in cols["renamed"]
        ]
    if cols.get("modified"):
        changes["modify_columns"] = [_col_out(m["after"]) for m in cols["modified"]]
    if idxs.get("added"):
        changes["add_indexes"] = [
            {"name": i.get("name"), "columns": i.get("columns") or [], "unique": bool(i.get("unique"))}
            for i in idxs["added"]
        ]
    if idxs.get("removed"):
        changes["drop_indexes"] = [i.get("name") for i in idxs["removed"]]
    for m in opts.get("modified") or []:
        key = m.get("key")
        if key in OPTION_KEYS:
            changes[key] = m.get("after")
    return changes
