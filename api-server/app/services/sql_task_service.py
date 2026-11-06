# -*- coding: utf-8 -*-
"""SQL 定时任务：按 cron 周期执行 SQL，把结果（CSV 附件 + 邮件正文摘要）发给收件邮箱。

使用平台「系统设置 → SMTP」的邮件配置；未配置时任务执行会明确报错（不会静默丢失）。
只读约束：只允许 select/show/describe/explain/with，避免定时任务误改数据。
"""
import asyncio
import csv
import io
import json
import time
from datetime import datetime

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.core.redis_client import acquire_lock, redis_pool, release_lock
from app.models import DBConnection, SqlTask
from app.services import audit_service
from app.services.db_agent_service import agent_query

logger = get_logger("api-server.sql_task")

_READ_HEADS = ("select", "show", "desc", "describe", "explain", "with")
MAX_ROWS = 5000
PREVIEW_ROWS = 20


def _job_id(task_id: int) -> str:
    return f"sql_task_{task_id}"


def register_task(task: SqlTask) -> None:
    """注册/刷新 cron 任务"""
    from app.services.scheduler_service import scheduler

    if not (task.cron_expression or "").strip():
        unregister_task(task.id)
        return
    try:
        trigger = CronTrigger.from_crontab(task.cron_expression.strip())
    except Exception as e:  # noqa: BLE001
        logger.warning("SQL 任务 %s cron 非法（%s）：%s", task.id, task.cron_expression, e)
        return
    if scheduler.get_job(_job_id(task.id)):
        scheduler.remove_job(_job_id(task.id))
    scheduler.add_job(
        execute_task, trigger, args=[task.id], id=_job_id(task.id),
        coalesce=True, max_instances=1, misfire_grace_time=300,
    )
    logger.info("SQL 任务已注册：#%s %s（%s）", task.id, task.name, task.cron_expression)


def unregister_task(task_id: int) -> None:
    from app.services.scheduler_service import scheduler
    if scheduler.get_job(_job_id(task_id)):
        scheduler.remove_job(_job_id(task_id))


async def load_all() -> None:
    async with async_session() as s:
        tasks = list((await s.execute(
            select(SqlTask).where(SqlTask.is_enabled.is_(True))
        )).scalars())
    for t in tasks:
        register_task(t)
    if tasks:
        logger.info("已加载 %d 个 SQL 定时任务", len(tasks))


# ---------- 执行 ----------

def _csv_bytes(columns: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    buf.write("\ufeff")  # Excel BOM
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(columns)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return buf.getvalue().encode("utf-8")


def _html_body(task_name: str, conn_name: str, sql: str, columns: list[str],
               rows: list[list], truncated: bool) -> str:
    def esc(x) -> str:
        return (str("" if x is None else x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    head = "".join(f"<th style='border:1px solid #eee;padding:4px 8px;background:#fafafa'>{esc(c)}</th>" for c in columns)
    body = ""
    for r in rows[:PREVIEW_ROWS]:
        body += "<tr>" + "".join(
            f"<td style='border:1px solid #eee;padding:4px 8px'>{esc(v)}</td>" for v in r
        ) + "</tr>"
    more = "" if len(rows) <= PREVIEW_ROWS else (
        f"<p style='color:#888'>仅展示前 {PREVIEW_ROWS} 行，完整 {len(rows)} 行见附件 CSV。</p>"
    )
    return f"""<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif">
  <h3>SQL 定时任务：{esc(task_name)}</h3>
  <p>数据库：<b>{esc(conn_name)}</b>　执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
  <p style="color:#666">SQL：<code>{esc(sql[:500])}</code></p>
  <p>返回 <b>{len(rows)}</b> 行{ '（已截断到 5000 行）' if truncated else '' }</p>
  <table style="border-collapse:collapse;font-size:12px">
    <tr>{head}</tr>{body}
  </table>
  {more}
  <p style="color:#999;font-size:12px">由 AutoHealOps 定时任务自动发送。</p>
</div>"""


def _send_mail_sync(host: str, port: int, username: str, password: str, sender: str,
                    recipients: list[str], subject: str, html: str,
                    filename: str, attachment: bytes, use_ssl: bool) -> None:
    import smtplib
    from email.header import Header
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart()
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html", "utf-8"))
    part = MIMEApplication(attachment, _subtype="csv")
    part.add_header("Content-Disposition", "attachment",
                    filename=("utf-8", "", filename))
    msg.attach(part)

    if use_ssl:
        server = smtplib.SMTP_SSL(host, port, timeout=15)
    else:
        server = smtplib.SMTP(host, port, timeout=15)
        server.starttls()
    try:
        if username:
            server.login(username, password)
        server.sendmail(sender, recipients, msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass


async def _smtp_conf() -> dict:
    from app.services.system_setting_service import get_setting
    host = (await get_setting("smtp_host", "") or "").strip()
    port = int((await get_setting("smtp_port", "465") or "465").strip() or "465")
    username = (await get_setting("smtp_username", "") or "").strip()
    password = await get_setting("smtp_password", "") or ""
    sender = (await get_setting("smtp_from", "") or "").strip() or username
    use_ssl = (await get_setting("smtp_ssl", "true") or "true").strip().lower() != "false"
    return {"host": host, "port": port, "username": username, "password": password,
            "sender": sender, "use_ssl": use_ssl}


async def _resolve_recipients(raw: str) -> list[str]:
    """收件人：支持直接写邮箱，也支持平台用户名（取其 email）。"""
    from app.models import User
    items = [x.strip() for x in (raw or "").replace(";", ",").split(",") if x.strip()]
    out: list[str] = []
    names = []
    for it in items:
        if "@" in it:
            out.append(it)
        else:
            names.append(it)
    if names:
        async with async_session() as s:
            users = list((await s.execute(select(User).where(User.username.in_(names)))).scalars())
        for u in users:
            if getattr(u, "email", ""):
                out.append(u.email)
    # 去重保序
    seen, uniq = set(), []
    for e in out:
        if e not in seen:
            seen.add(e)
            uniq.append(e)
    return uniq


async def execute_task(task_id: int, dry_run: bool = False) -> dict:
    """执行一次任务。dry_run=True 只跑 SQL + 生成附件，不发邮件（便于验证）。"""
    value = await acquire_lock(redis_pool, f"lock:sql_task:{task_id}", 120000)
    if value is None:
        return {"ok": False, "error": "任务正在其他实例执行"}

    started = time.time()
    result = {"ok": False, "error": "", "rows": 0}
    try:
        async with async_session() as s:
            task = await s.get(SqlTask, task_id)
            if not task:
                return {"ok": False, "error": "任务不存在"}
            conn = await s.get(DBConnection, task.db_connection_id)
        if not conn:
            result["error"] = "数据库连接不存在"
            return result
        if (getattr(conn, "source", "direct") or "direct") != "agent":
            result["error"] = "仅 agent 接入的库支持定时任务"
            return result

        sql = (task.sql or "").strip()
        head = (sql.split(None, 1)[0] or "").lower() if sql else ""
        if head not in _READ_HEADS:
            result["error"] = "定时任务只允许只读 SQL（select/show/describe/explain/with）"
            return result

        data = await agent_query(int(conn.cluster_id), conn.agent_db_key or "", sql, timeout=90.0)
        columns = data.get("columns") or []
        rows = data.get("rows") or []
        if len(rows) > MAX_ROWS:
            rows = rows[:MAX_ROWS]
        result["rows"] = len(rows)

        if dry_run:
            result.update({"ok": True, "dry_run": True, "columns": columns[:10],
                           "preview": rows[:5]})
            return result

        recipients = await _resolve_recipients(task.recipients or "")
        if not recipients:
            result["error"] = "没有有效的收件邮箱（recipients 为空或用户名无邮箱）"
            return result
        conf = await _smtp_conf()
        if not conf["host"]:
            result["error"] = "平台未配置 SMTP（系统设置 → 通知/邮件），无法发送"
            return result

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"{conn.name}-{task.name}-{ts}.csv".replace("/", "_")
        await asyncio.to_thread(
            _send_mail_sync, conf["host"], conf["port"], conf["username"], conf["password"],
            conf["sender"], recipients, f"[AutoHealOps] {task.name} 数据报表（{ts}）",
            _html_body(task.name, conn.name, sql, columns, rows, bool(data.get("truncated"))),
            filename, _csv_bytes(columns, rows), conf["use_ssl"],
        )
        result["ok"] = True
        result["recipients"] = recipients
        logger.info("SQL 任务 #%s 执行完成：%d 行 → %s", task_id, len(rows), ",".join(recipients))
        return result
    except Exception as e:  # noqa: BLE001
        result["error"] = str(e)
        logger.warning("SQL 任务 #%s 执行失败：%s", task_id, e)
        return result
    finally:
        elapsed = round(time.time() - started, 2)
        try:
            async with async_session() as s:
                t = await s.get(SqlTask, task_id)
                if t:
                    t.last_run_at = datetime.now()
                    t.last_status = "success" if result.get("ok") else "failed"
                    t.last_error = (result.get("error") or "")[:500]
                    await s.commit()
            await audit_service.create_audit_record(
                f"sqltask-{task_id}-{int(time.time())}",
                trigger_snapshot=json.dumps({"mode": "sql_task", "task_id": task_id}, ensure_ascii=False),
                execution_detail=json.dumps({
                    "rows": result.get("rows", 0), "elapsed_s": elapsed,
                    "ok": bool(result.get("ok")), "dry_run": dry_run,
                    "error": (result.get("error") or "")[:300],
                }, ensure_ascii=False),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("SQL 任务收尾写入失败：%s", e)
        await release_lock(redis_pool, f"lock:sql_task:{task_id}", value)
