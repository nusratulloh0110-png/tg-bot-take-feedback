import secrets
from collections.abc import AsyncIterator
from datetime import date
from html import escape
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.background import BackgroundTask

from app.config import Settings, get_settings
from app.db.models import Admin, Employee, EmployeeInstitution, Feedback, FeedbackStatus, FeedbackType, Institution
from app.db.session import SessionFactory
from app.domain.criteria import criterion_label, tag_label
from app.services.admins import add_admin
from app.services.analytics import feedback_report_data
from app.services.employees import set_employee_institutions
from app.services.export import export_feedback_pdf, export_feedback_xlsx
from app.services.feedback import average_rating, update_feedback_status
from app.services.institutions import create_institution, reissue_token
from app.services.tokens import build_deep_link

app = FastAPI(title="DMED Admin Panel")


STATUS_LABELS = {
    FeedbackStatus.new: "Новые",
    FeedbackStatus.reviewed: "Изучено",
    FeedbackStatus.in_progress: "В работе",
    FeedbackStatus.closed: "Закрыто",
}

TYPE_LABELS = {
    FeedbackType.employee: "Сотрудник",
    FeedbackType.implementation: "Внедрение",
}

TAB_LABELS = {
    "institutions": "Учреждение",
    "employees": "Сотрудники",
    "reviews": "Отзывы",
    "analytics": "Аналитика",
    "admins": "Администраторы Telegram",
}


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _token(settings: Settings) -> str | None:
    token = settings.web_admin_token.strip() if settings.web_admin_token else ""
    return token or None


def _authenticated(request: Request, settings: Settings) -> bool:
    token = _token(settings)
    cookie = request.cookies.get("dmed_admin_token", "")
    return bool(token and cookie and secrets.compare_digest(cookie, token))


def _admin_required(request: Request, settings: Settings) -> None:
    if not _authenticated(request, settings):
        raise HTTPException(status_code=403, detail="Нет доступа")


def _redirect_admin(tab: str = "analytics", **params: object) -> RedirectResponse:
    query = {"tab": tab}
    query.update({key: value for key, value in params.items() if value not in (None, "")})
    return RedirectResponse(f"/admin?{urlencode(query)}", status_code=303)


def _esc(value: object) -> str:
    return escape(str(value or ""))


def _selected(value: object, current: object) -> str:
    return " selected" if str(value) == str(current) else ""


def _checked(value: object, selected_values: set[int]) -> str:
    return " checked" if int(value) in selected_values else ""


def _form_int(value: object, field_name: str) -> int:
    try:
        parsed = int(str(value or "").strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Некорректное значение поля {field_name}") from exc
    if parsed <= 0:
        raise HTTPException(status_code=400, detail=f"Некорректное значение поля {field_name}")
    return parsed


def _form_text(value: object, field_name: str, required: bool = False) -> str | None:
    text = str(value or "").strip()
    if required and not text:
        raise HTTPException(status_code=400, detail=f"Поле {field_name} обязательно")
    return text or None


def _date_value(value: date | None) -> str:
    return value.isoformat() if value else ""


def _period_query(institution_id: int | None, date_from: date | None, date_to: date | None) -> str:
    query = {}
    if institution_id:
        query["institution_id"] = institution_id
    if date_from:
        query["date_from"] = date_from.isoformat()
    if date_to:
        query["date_to"] = date_to.isoformat()
    return urlencode(query)


def _institution_options(institutions: list[Institution], current: int | None = None, include_all: bool = True) -> str:
    options = ['<option value="">Все учреждения</option>'] if include_all else []
    for institution in institutions:
        options.append(
            f'<option value="{institution.id}"{_selected(institution.id, current)}>'
            f"#{institution.id} {_esc(institution.name)}</option>"
        )
    return "\n".join(options)


def _status_badge(status: FeedbackStatus) -> str:
    css = {
        FeedbackStatus.new: "",
        FeedbackStatus.reviewed: "ok",
        FeedbackStatus.in_progress: "warn",
        FeedbackStatus.closed: "done",
    }.get(status, "")
    return f'<span class="badge {css}">{_esc(STATUS_LABELS.get(status, status.value))}</span>'


def _rating_bar(value: float) -> str:
    percent = min(max(value / 5 * 100, 0), 100)
    return (
        f'<span class="rating"><span class="rating__fill" style="width:{percent:.0f}%"></span></span>'
        f'<strong>{value}</strong>'
    )


def _page(title: str, body: str, active_tab: str | None = None) -> HTMLResponse:
    nav = ""
    if active_tab:
        links = []
        for tab, label in TAB_LABELS.items():
            active = " active" if tab == active_tab else ""
            links.append(f'<a class="{active}" href="/admin?tab={tab}">{label}</a>')
        nav = f'<nav class="nav">{"".join(links)}<a href="/admin/logout">Выйти</a></nav>'

    css = """
    :root {
      --bg: #f4f6fa;
      --panel: #fff;
      --line: #d8e0ec;
      --text: #172033;
      --muted: #647084;
      --accent: #0f8f8c;
      --accent-2: #3454d1;
      --danger: #b42318;
      --warn: #9a6700;
      --ok: #247a3e;
      --done: #475569;
    }
    * { box-sizing: border-box; }
    body { margin: 0; color: var(--text); background: var(--bg); font-family: Arial, Helvetica, sans-serif; font-size: 14px; line-height: 1.45; }
    a { color: var(--accent-2); text-decoration: none; }
    .topbar { position: sticky; top: 0; z-index: 2; display: flex; align-items: center; justify-content: space-between; gap: 20px; min-height: 62px; padding: 0 28px; background: #fff; border-bottom: 1px solid var(--line); }
    .brand { font-size: 18px; font-weight: 800; }
    .nav { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
    .nav a { min-height: 34px; display: inline-flex; align-items: center; padding: 6px 10px; border-radius: 6px; color: var(--muted); font-weight: 700; }
    .nav a.active, .nav a:hover { background: #eaf3f4; color: var(--accent); }
    .shell { max-width: 1480px; margin: 0 auto; padding: 22px 28px 42px; }
    .page-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 14px; }
    h1, h2 { margin: 0 0 12px; line-height: 1.15; }
    h1 { font-size: 24px; }
    h2 { font-size: 18px; }
    .panel, .toolbar { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; margin-bottom: 16px; }
    .toolbar { display: grid; grid-template-columns: minmax(220px, 1fr) 155px 155px 150px minmax(180px, 1fr) auto; gap: 10px; align-items: end; }
    .toolbar.analytics { grid-template-columns: minmax(260px, 1fr) 170px 170px auto auto; }
    .form-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)) auto; gap: 10px; align-items: end; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(170px, 1fr)); gap: 12px; margin-bottom: 16px; }
    .split { display: grid; grid-template-columns: minmax(0, 1fr) minmax(340px, 430px); gap: 16px; }
    label { display: block; margin: 0 0 5px; color: var(--muted); font-size: 12px; font-weight: 800; }
    input, select, textarea { width: 100%; min-height: 36px; padding: 8px 10px; color: var(--text); background: #fff; border: 1px solid #cbd5e1; border-radius: 6px; font: inherit; }
    textarea { min-height: 74px; resize: vertical; }
    select[multiple] { min-height: 110px; }
    button, .button { display: inline-flex; align-items: center; justify-content: center; min-height: 34px; padding: 7px 11px; border: 1px solid #b9c6d8; border-radius: 6px; background: #fff; color: var(--text); font: inherit; font-weight: 800; cursor: pointer; white-space: nowrap; }
    button:hover, .button:hover { border-color: var(--accent); color: var(--accent); }
    .primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    .primary:hover { color: #fff; filter: brightness(0.96); }
    .danger { color: var(--danger); border-color: #e7b8b3; }
    .inline-actions { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
    .table-wrap { overflow: auto; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
    table { width: 100%; border-collapse: collapse; min-width: 940px; }
    th, td { padding: 9px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { background: #eef3f9; color: #394255; font-size: 12px; text-transform: uppercase; }
    tr:last-child td { border-bottom: 0; }
    .muted { color: var(--muted); }
    .metric { min-height: 96px; background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 14px; }
    .metric span { display: block; color: var(--muted); font-size: 12px; font-weight: 800; }
    .metric strong { display: block; margin-top: 8px; font-size: 26px; line-height: 1; }
    .metric small { display: block; margin-top: 8px; color: var(--muted); }
    .badge { display: inline-flex; align-items: center; min-height: 22px; padding: 2px 8px; border-radius: 999px; background: #eef3f9; color: #334155; font-size: 12px; font-weight: 800; }
    .badge.ok { background: #e8f6ee; color: var(--ok); }
    .badge.warn { background: #fff4dc; color: var(--warn); }
    .badge.done { background: #e9edf2; color: var(--done); }
    .badge.danger { background: #ffe8e5; color: var(--danger); }
    .rating { position: relative; display: inline-block; width: 54px; height: 7px; margin-right: 8px; background: #d9e1ec; border-radius: 999px; overflow: hidden; }
    .rating__fill { display: block; height: 100%; background: var(--accent); }
    .review-comment { max-width: 360px; white-space: pre-wrap; }
    .status-tabs { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
    .status-tabs a { display: inline-flex; gap: 6px; align-items: center; min-height: 34px; padding: 6px 10px; border: 1px solid var(--line); border-radius: 6px; background: #fff; color: var(--muted); font-weight: 800; }
    .status-tabs a.active { color: #fff; background: var(--accent); border-color: var(--accent); }
    .login { max-width: 420px; margin: 80px auto; background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 22px; }
    .checkbox-list { display: grid; grid-template-columns: repeat(2, minmax(180px, 1fr)); gap: 6px 12px; max-height: 170px; overflow: auto; padding: 8px; border: 1px solid var(--line); border-radius: 6px; background: #fff; }
    .checkbox-list label { display: flex; align-items: center; gap: 7px; margin: 0; font-size: 13px; color: var(--text); font-weight: 600; }
    .checkbox-list input { width: auto; min-height: auto; }
    @media (max-width: 1050px) {
      .toolbar, .toolbar.analytics, .split, .form-grid, .grid { grid-template-columns: 1fr; }
      .topbar { align-items: flex-start; flex-direction: column; padding: 14px 18px; }
      .shell { padding: 18px; }
      .checkbox-list { grid-template-columns: 1fr; }
    }
    """

    html = f"""
    <!doctype html>
    <html lang="ru">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{_esc(title)}</title>
      <style>{css}</style>
    </head>
    <body>
      <header class="topbar"><div class="brand">DMED Admin</div>{nav}</header>
      {body}
    </body>
    </html>
    """
    return HTMLResponse(html, media_type="text/html; charset=utf-8")


def _login_page(error: str | None = None, setup_required: bool = False) -> HTMLResponse:
    if setup_required:
        body = """
        <main class="login">
          <h1>Веб-панель не настроена</h1>
          <p class="muted">Укажите переменную <strong>WEB_ADMIN_TOKEN</strong> в .env и перезапустите сервис.</p>
        </main>
        """
        return _page("Настройка веб-панели", body)
    error_html = f'<p class="badge danger">{_esc(error)}</p>' if error else ""
    body = f"""
    <main class="login">
      <h1>Вход в админ-панель</h1>
      {error_html}
      <form method="post" action="/admin/login">
        <label for="token">WEB_ADMIN_TOKEN</label>
        <input id="token" name="token" type="password" autocomplete="current-password" required>
        <div style="height:12px"></div>
        <button class="primary" type="submit">Войти</button>
      </form>
    </main>
    """
    return _page("Вход", body)


def _metric(label: str, value: object, note: str = "") -> str:
    note_html = f"<small>{_esc(note)}</small>" if note else ""
    return f'<div class="metric"><span>{_esc(label)}</span><strong>{_esc(value)}</strong>{note_html}</div>'


def _institution_link(settings: Settings, institution: Institution) -> str:
    if settings.bot_username:
        return build_deep_link(settings.bot_username, institution.token)
    return institution.token


def _institution_checkboxes(institutions: list[Institution], selected_ids: set[int], form_id: str | None = None) -> str:
    if not institutions:
        return '<span class="muted">Сначала добавьте учреждение.</span>'
    form_attr = f' form="{_esc(form_id)}"' if form_id else ""
    return "".join(
        f"""
        <label>
          <input type="checkbox" name="institution_ids" value="{institution.id}"{form_attr}{_checked(institution.id, selected_ids)}>
          #{institution.id} {_esc(institution.name)}
        </label>
        """
        for institution in institutions
    )


def _reviews_query(
    institution_id: int | None,
    date_from: date | None,
    date_to: date | None,
    q: str | None,
    feedback_type: str | None,
) -> str:
    params = {"tab": "reviews"}
    if institution_id:
        params["institution_id"] = institution_id
    if date_from:
        params["date_from"] = date_from.isoformat()
    if date_to:
        params["date_to"] = date_to.isoformat()
    if q:
        params["q"] = q
    if feedback_type:
        params["feedback_type"] = feedback_type
    return urlencode(params)


def _date_filter_conditions(date_from: date | None, date_to: date | None) -> list:
    from datetime import datetime, time, timedelta, timezone

    conditions = []
    if date_from:
        start_at = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
        conditions.append(Feedback.created_at >= start_at)
    if date_to:
        end_at = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=timezone.utc)
        conditions.append(Feedback.created_at < end_at)
    return conditions


async def _all_institutions(session: AsyncSession) -> list[Institution]:
    return list(await session.scalars(select(Institution).order_by(Institution.archived, Institution.name)))


async def _status_counts(
    session: AsyncSession,
    institution_id: int | None,
    date_from: date | None,
    date_to: date | None,
    q: str | None,
    feedback_type: str | None,
) -> dict[str, int]:
    counts = {"all": 0}
    base_conditions = []
    if institution_id:
        base_conditions.append(Feedback.institution_id == institution_id)
    base_conditions.extend(_date_filter_conditions(date_from, date_to))
    if q:
        pattern = f"%{q.strip()}%"
        base_conditions.append(
            or_(
                Feedback.reviewer_full_name.ilike(pattern),
                Feedback.reviewer_phone.ilike(pattern),
                Feedback.comment.ilike(pattern),
            )
        )
    if feedback_type in {item.value for item in FeedbackType}:
        base_conditions.append(Feedback.feedback_type == FeedbackType(feedback_type))
    counts["all"] = await session.scalar(select(func.count(Feedback.id)).where(*base_conditions)) or 0
    for status in FeedbackStatus:
        counts[status.value] = (
            await session.scalar(select(func.count(Feedback.id)).where(*base_conditions, Feedback.status == status)) or 0
        )
    return counts


def _analytics_toolbar(
    institutions: list[Institution],
    institution_id: int | None,
    date_from: date | None,
    date_to: date | None,
) -> str:
    query = _period_query(institution_id, date_from, date_to)
    suffix = f"?{query}" if query else ""
    return f"""
    <form class="toolbar analytics" method="get" action="/admin">
      <input type="hidden" name="tab" value="analytics">
      <div>
        <label>Учреждение</label>
        <select name="institution_id">{_institution_options(institutions, institution_id)}</select>
      </div>
      <div><label>С даты</label><input type="date" name="date_from" value="{_date_value(date_from)}"></div>
      <div><label>По дату</label><input type="date" name="date_to" value="{_date_value(date_to)}"></div>
      <button class="primary" type="submit">Показать</button>
      <div class="inline-actions">
        <a class="button" href="/admin/export.xlsx{suffix}">Excel</a>
        <a class="button" href="/admin/export.pdf{suffix}">PDF</a>
      </div>
    </form>
    """


def _analytics_content(data: dict) -> str:
    summary = data["summary"]
    metrics = [
        _metric("Всего отзывов", summary["feedback_total"], "за выбранный период"),
        _metric("Сотрудников ответило", summary["unique_reviewers_total"], "уникальные телефон/Telegram"),
        _metric("Средняя оценка", summary["average_rating"], "по всем критериям"),
        _metric("Низкие оценки", summary["low_score_total"], "2.5 и ниже"),
        _metric("По сотрудникам", summary["employee_feedback_total"], f"средняя {summary['employee_average_rating']}"),
        _metric("По внедрению", summary["implementation_feedback_total"], f"средняя {summary['implementation_average_rating']}"),
        _metric("С комментариями", summary["comments_total"]),
        _metric("Переходы по ссылкам", summary["link_visits_total"]),
    ]
    institution_rows = "\n".join(
        f"""
        <tr>
          <td>#{row['id']} {_esc(row['name'])}</td>
          <td>{_esc(row['feedback_total'])}</td>
          <td>{_esc(row['reviewers_total'])}</td>
          <td>{_rating_bar(row['average_rating'])}</td>
        </tr>
        """
        for row in data["institutions"][:20]
    ) or '<tr><td colspan="4" class="muted">Нет данных.</td></tr>'
    employee_rows = "\n".join(
        f"""
        <tr>
          <td>{_esc(row['name'])}<br><span class="muted">{_esc(row['institution'])}</span></td>
          <td>{_esc(row['feedback_total'])}</td>
          <td>{_rating_bar(row['average_rating'])}</td>
        </tr>
        """
        for row in data["employees"][:20]
    ) or '<tr><td colspan="3" class="muted">Нет данных.</td></tr>'
    return f"""
    <div class="grid">{''.join(metrics)}</div>
    <div class="split">
      <section class="panel">
        <h2>Учреждения</h2>
        <div class="table-wrap">
          <table><thead><tr><th>Учреждение</th><th>Отзывы</th><th>Сотрудников</th><th>Средняя</th></tr></thead><tbody>{institution_rows}</tbody></table>
        </div>
      </section>
      <section class="panel">
        <h2>Сотрудники</h2>
        <div class="table-wrap">
          <table><thead><tr><th>Сотрудник</th><th>Отзывы</th><th>Средняя</th></tr></thead><tbody>{employee_rows}</tbody></table>
        </div>
      </section>
    </div>
    """


async def _render_analytics(
    session: AsyncSession,
    institutions: list[Institution],
    institution_id: int | None,
    date_from: date | None,
    date_to: date | None,
) -> str:
    data = await feedback_report_data(
        session,
        days=None if date_from or date_to else 30,
        institution_id=institution_id,
        date_from=date_from,
        date_to=date_to,
    )
    return f"""
    <div class="page-head"><h1>Аналитика</h1></div>
    {_analytics_toolbar(institutions, institution_id, date_from, date_to)}
    {_analytics_content(data)}
    """


async def _render_institutions(institutions: list[Institution], settings: Settings) -> str:
    rows = []
    for institution in institutions:
        link = _institution_link(settings, institution)
        status = "Активно" if institution.token_active and not institution.archived else "Закрыто"
        badge = "ok" if institution.token_active and not institution.archived else "warn"
        rows.append(
            f"""
            <tr>
              <form id="inst-edit-{institution.id}" method="post" action="/admin/institutions/{institution.id}/update"></form>
              <td>#{institution.id}</td>
              <td><input form="inst-edit-{institution.id}" name="name" value="{_esc(institution.name)}"></td>
              <td><input form="inst-edit-{institution.id}" name="region" value="{_esc(institution.region)}"></td>
              <td><input form="inst-edit-{institution.id}" name="address" value="{_esc(institution.address)}"></td>
              <td><span class="badge {badge}">{status}</span></td>
              <td><a href="{_esc(link)}" target="_blank" rel="noreferrer">{_esc(link)}</a></td>
              <td>
                <div class="inline-actions">
                  <button form="inst-edit-{institution.id}" type="submit">Сохранить</button>
                  <form method="post" action="/admin/institutions/{institution.id}/reissue"><button type="submit">Новая ссылка</button></form>
                  <form method="post" action="/admin/institutions/{institution.id}/deactivate"><button type="submit">Отключить</button></form>
                  <form method="post" action="/admin/institutions/{institution.id}/archive"><button class="danger" type="submit">{'Вернуть' if institution.archived else 'Архив'}</button></form>
                  <a class="button" href="/admin?tab=analytics&institution_id={institution.id}">Отчет</a>
                </div>
              </td>
            </tr>
            """
        )
    rows_html = "\n".join(rows) or '<tr><td colspan="7" class="muted">Учреждений пока нет.</td></tr>'
    return f"""
    <div class="page-head"><h1>Учреждение</h1></div>
    <section class="panel">
      <h2>Добавить учреждение</h2>
      <form class="form-grid" method="post" action="/admin/institutions">
        <div><label>Название</label><input name="name" required></div>
        <div><label>Регион</label><input name="region"></div>
        <div><label>Адрес</label><input name="address"></div>
        <button class="primary" type="submit">Добавить</button>
      </form>
    </section>
    <section class="panel">
      <h2>Список учреждений</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>ID</th><th>Название</th><th>Регион</th><th>Адрес</th><th>Статус</th><th>Ссылка</th><th>Действия</th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
    </section>
    """


async def _render_employees(
    session: AsyncSession,
    institutions: list[Institution],
    institution_id: int | None,
) -> str:
    query = (
        select(Employee)
        .options(selectinload(Employee.institution_links).selectinload(EmployeeInstitution.institution))
        .order_by(Employee.archived, Employee.full_name)
    )
    if institution_id:
        query = query.join(EmployeeInstitution, EmployeeInstitution.employee_id == Employee.id).where(
            EmployeeInstitution.institution_id == institution_id
        )
    employees = list((await session.scalars(query)).unique())
    rows = []
    for employee in employees:
        selected_ids = {link.institution_id for link in employee.institution_links}
        institution_names = ", ".join(link.institution.name for link in employee.institution_links if link.institution) or "-"
        rows.append(
            f"""
            <tr>
              <td>#{employee.id}</td>
              <td>
                <form id="emp-edit-{employee.id}" method="post" action="/admin/employees/{employee.id}/update"></form>
                <input form="emp-edit-{employee.id}" name="full_name" value="{_esc(employee.full_name)}">
              </td>
              <td><input form="emp-edit-{employee.id}" name="position" value="{_esc(employee.position)}"></td>
              <td>
                <div class="muted">{_esc(institution_names)}</div>
                <div class="checkbox-list">{_institution_checkboxes(institutions, selected_ids, form_id=f"emp-edit-{employee.id}")}</div>
              </td>
              <td><span class="badge {'warn' if employee.archived else 'ok'}">{'Архив' if employee.archived else 'Активен'}</span></td>
              <td>
                <div class="inline-actions">
                  <button form="emp-edit-{employee.id}" type="submit">Сохранить</button>
                  <form method="post" action="/admin/employees/{employee.id}/archive"><button class="danger" type="submit">{'Вернуть' if employee.archived else 'Архив'}</button></form>
                </div>
              </td>
            </tr>
            """
        )
    rows_html = "\n".join(rows) or '<tr><td colspan="6" class="muted">Сотрудников пока нет.</td></tr>'
    return f"""
    <div class="page-head"><h1>Сотрудники</h1></div>
    <form class="toolbar analytics" method="get" action="/admin">
      <input type="hidden" name="tab" value="employees">
      <div><label>Фильтр по учреждению</label><select name="institution_id">{_institution_options(institutions, institution_id)}</select></div>
      <button class="primary" type="submit">Показать</button>
    </form>
    <section class="panel">
      <h2>Добавить сотрудника</h2>
      <form class="form-grid" method="post" action="/admin/employees">
        <div><label>ФИО</label><input name="full_name" required></div>
        <div><label>Должность</label><input name="position"></div>
        <div><label>Учреждения</label><div class="checkbox-list">{_institution_checkboxes(institutions, set())}</div></div>
        <button class="primary" type="submit">Добавить</button>
      </form>
    </section>
    <section class="panel">
      <h2>Список сотрудников</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>ID</th><th>ФИО</th><th>Должность</th><th>Учреждения</th><th>Статус</th><th>Действия</th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
    </section>
    """


async def _render_reviews(
    session: AsyncSession,
    institutions: list[Institution],
    institution_id: int | None,
    date_from: date | None,
    date_to: date | None,
    status_value: str | None,
    q: str | None,
    feedback_type: str | None,
) -> str:
    status = FeedbackStatus(status_value) if status_value in {item.value for item in FeedbackStatus} else None
    active_type = feedback_type if feedback_type in {item.value for item in FeedbackType} else None
    base_query = _reviews_query(institution_id, date_from, date_to, q, active_type)
    counts = await _status_counts(session, institution_id, date_from, date_to, q, active_type)
    tabs = [
        ("all", "Все", counts["all"]),
        *[(item.value, STATUS_LABELS[item], counts[item.value]) for item in FeedbackStatus],
    ]
    status_tabs = "".join(
        f'<a class="{"active" if (key == "all" and status is None) or key == status_value else ""}" '
        f'href="/admin?{base_query}&status={"" if key == "all" else key}">{label}<span>{count}</span></a>'
        for key, label, count in tabs
    )

    conditions = []
    if institution_id:
        conditions.append(Feedback.institution_id == institution_id)
    conditions.extend(_date_filter_conditions(date_from, date_to))
    if status:
        conditions.append(Feedback.status == status)
    if q:
        pattern = f"%{q.strip()}%"
        conditions.append(
            or_(
                Feedback.reviewer_full_name.ilike(pattern),
                Feedback.reviewer_phone.ilike(pattern),
                Feedback.comment.ilike(pattern),
            )
        )
    if active_type:
        conditions.append(Feedback.feedback_type == FeedbackType(active_type))
    query = (
        select(Feedback)
        .options(selectinload(Feedback.institution), selectinload(Feedback.employee), selectinload(Feedback.user))
        .where(*conditions)
        .order_by(Feedback.created_at.desc())
        .limit(300)
    )
    reviews = list(await session.scalars(query))
    rows = []
    for item in reviews:
        tags = ", ".join(tag_label(code) for code in item.tags or []) or "-"
        subject = item.employee.full_name if item.employee else item.institution.name
        ratings = "<br>".join(
            f"{_esc(criterion_label(code, item.feedback_type.value))}: {_esc(value)}/5"
            for code, value in (item.ratings or {}).items()
        ) or "-"
        rows.append(
            f"""
            <tr>
              <td>{item.created_at:%Y-%m-%d %H:%M}<br><span class="muted">#{_esc(item.id)}</span></td>
              <td>{_esc(item.institution.name)}<br><span class="muted">{_esc(TYPE_LABELS.get(item.feedback_type, item.feedback_type.value))}: {_esc(subject)}</span></td>
              <td>{_esc(item.reviewer_full_name or '-')}<br><span class="muted">{_esc(item.reviewer_phone or '-')}</span></td>
              <td>{_rating_bar(average_rating(item.ratings))}<br><span class="muted">{ratings}</span></td>
              <td><span class="muted">{_esc(tags)}</span><div class="review-comment">{_esc(item.comment or '-')}</div></td>
              <td>
                <form method="post" action="/admin/reviews/{item.id}/status">
                  <select name="status">
                    {''.join(f'<option value="{status_item.value}"{_selected(status_item.value, item.status.value)}>{label}</option>' for status_item, label in STATUS_LABELS.items())}
                  </select>
                  <input type="hidden" name="institution_id" value="{_esc(institution_id or '')}">
                  <input type="hidden" name="date_from" value="{_date_value(date_from)}">
                  <input type="hidden" name="date_to" value="{_date_value(date_to)}">
                  <input type="hidden" name="q" value="{_esc(q or '')}">
                  <input type="hidden" name="feedback_type" value="{_esc(active_type or '')}">
                  <div style="height:6px"></div>
                  <button type="submit">Обновить</button>
                </form>
                <div style="height:6px"></div>
                {_status_badge(item.status)}
              </td>
            </tr>
            """
        )
    rows_html = "\n".join(rows) or '<tr><td colspan="6" class="muted">Отзывов по выбранным фильтрам нет.</td></tr>'
    return f"""
    <div class="page-head"><h1>Отзывы</h1></div>
    <form class="toolbar" method="get" action="/admin">
      <input type="hidden" name="tab" value="reviews">
      <input type="hidden" name="status" value="{_esc(status_value or '')}">
      <div><label>Учреждение</label><select name="institution_id">{_institution_options(institutions, institution_id)}</select></div>
      <div><label>С даты</label><input type="date" name="date_from" value="{_date_value(date_from)}"></div>
      <div><label>По дату</label><input type="date" name="date_to" value="{_date_value(date_to)}"></div>
      <div>
        <label>Тип</label>
        <select name="feedback_type">
          <option value="">Все</option>
          <option value="employee"{_selected("employee", active_type)}>Сотрудник</option>
          <option value="implementation"{_selected("implementation", active_type)}>Внедрение</option>
        </select>
      </div>
      <div><label>Поиск</label><input name="q" value="{_esc(q)}" placeholder="ФИО, телефон, комментарий"></div>
      <button class="primary" type="submit">Фильтр</button>
    </form>
    <div class="status-tabs">{status_tabs}</div>
    <section class="panel">
      <div class="table-wrap">
        <table>
          <thead><tr><th>Дата</th><th>Объект</th><th>Отправитель</th><th>Оценки</th><th>Комментарий</th><th>Статус</th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
    </section>
    """


async def _render_admins(session: AsyncSession, settings: Settings) -> str:
    admins = list(await session.scalars(select(Admin.user_id).order_by(Admin.user_id)))
    all_admins = sorted(set(settings.admin_ids) | set(admins))
    rows = "\n".join(f"<tr><td>{admin_id}</td></tr>" for admin_id in all_admins) or '<tr><td class="muted">Админов пока нет.</td></tr>'
    return f"""
    <div class="page-head"><h1>Администраторы Telegram</h1></div>
    <section class="panel">
      <h2>Добавить администратора</h2>
      <form class="form-grid" method="post" action="/admin/admins">
        <div><label>Telegram ID</label><input name="user_id" inputmode="numeric" required></div>
        <button class="primary" type="submit">Добавить</button>
      </form>
    </section>
    <section class="panel">
      <h2>Список администраторов</h2>
      <div class="table-wrap"><table><thead><tr><th>Telegram ID</th></tr></thead><tbody>{rows}</tbody></table></div>
    </section>
    """


@app.get("/")
async def index() -> Response:
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin/login", response_class=HTMLResponse)
async def login_page() -> Response:
    settings = get_settings()
    return _login_page(setup_required=_token(settings) is None)


@app.post("/admin/login")
async def login(request: Request) -> Response:
    settings = get_settings()
    configured = _token(settings)
    if configured is None:
        return _login_page(setup_required=True)
    form = await request.form()
    token = str(form.get("token") or "")
    if not secrets.compare_digest(token, configured):
        return _login_page("Неверный токен")
    response = _redirect_admin("analytics")
    response.set_cookie("dmed_admin_token", configured, max_age=60 * 60 * 12, httponly=True, samesite="lax")
    return response


@app.get("/admin/logout")
async def logout() -> Response:
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie("dmed_admin_token")
    return response


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    tab: str = "analytics",
    institution_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    status: str | None = None,
    q: str | None = None,
    feedback_type: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> Response:
    settings = get_settings()
    if _token(settings) is None:
        return _login_page(setup_required=True)
    if not _authenticated(request, settings):
        return RedirectResponse("/admin/login", status_code=303)

    active_tab = tab if tab in TAB_LABELS else "analytics"
    institutions = await _all_institutions(session)
    if active_tab == "institutions":
        content = await _render_institutions(institutions, settings)
    elif active_tab == "employees":
        content = await _render_employees(session, institutions, institution_id)
    elif active_tab == "reviews":
        content = await _render_reviews(session, institutions, institution_id, date_from, date_to, status, q, feedback_type)
    elif active_tab == "admins":
        content = await _render_admins(session, settings)
    else:
        content = await _render_analytics(session, institutions, institution_id, date_from, date_to)

    body = f'<main class="shell">{content}</main>'
    return _page("DMED Admin", body, active_tab)


@app.post("/admin/institutions")
async def web_create_institution(request: Request, session: AsyncSession = Depends(get_session)) -> RedirectResponse:
    settings = get_settings()
    _admin_required(request, settings)
    form = await request.form()
    await create_institution(
        session,
        _form_text(form.get("name"), "Название", required=True) or "",
        _form_text(form.get("region"), "Регион"),
        _form_text(form.get("address"), "Адрес"),
    )
    return _redirect_admin("institutions")


@app.post("/admin/institutions/{institution_id}/update")
async def web_update_institution(
    institution_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    settings = get_settings()
    _admin_required(request, settings)
    institution = await session.get(Institution, institution_id)
    if institution is None:
        raise HTTPException(status_code=404, detail="Учреждение не найдено")
    form = await request.form()
    institution.name = _form_text(form.get("name"), "Название", required=True) or institution.name
    institution.region = _form_text(form.get("region"), "Регион")
    institution.address = _form_text(form.get("address"), "Адрес")
    return _redirect_admin("institutions")


@app.post("/admin/institutions/{institution_id}/reissue")
async def web_reissue_institution(
    institution_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    settings = get_settings()
    _admin_required(request, settings)
    institution = await session.get(Institution, institution_id)
    if institution is None:
        raise HTTPException(status_code=404, detail="Учреждение не найдено")
    await reissue_token(session, institution)
    institution.archived = False
    return _redirect_admin("institutions")


@app.post("/admin/institutions/{institution_id}/deactivate")
async def web_deactivate_institution(
    institution_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    settings = get_settings()
    _admin_required(request, settings)
    institution = await session.get(Institution, institution_id)
    if institution is None:
        raise HTTPException(status_code=404, detail="Учреждение не найдено")
    institution.token_active = False
    return _redirect_admin("institutions")


@app.post("/admin/institutions/{institution_id}/archive")
async def web_archive_institution(
    institution_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    settings = get_settings()
    _admin_required(request, settings)
    institution = await session.get(Institution, institution_id)
    if institution is None:
        raise HTTPException(status_code=404, detail="Учреждение не найдено")
    institution.archived = not institution.archived
    institution.token_active = not institution.archived
    return _redirect_admin("institutions")


def _institution_ids_from_form(form) -> list[int]:
    raw_values = form.getlist("institution_ids")
    ids = []
    for value in raw_values:
        try:
            ids.append(int(str(value)))
        except ValueError:
            continue
    return ids


@app.post("/admin/employees")
async def web_create_employee(request: Request, session: AsyncSession = Depends(get_session)) -> RedirectResponse:
    settings = get_settings()
    _admin_required(request, settings)
    form = await request.form()
    institution_ids = _institution_ids_from_form(form)
    if not institution_ids:
        raise HTTPException(status_code=400, detail="Выберите хотя бы одно учреждение")
    employee = Employee(
        institution_id=institution_ids[0],
        full_name=_form_text(form.get("full_name"), "ФИО", required=True) or "",
        position=_form_text(form.get("position"), "Должность"),
    )
    session.add(employee)
    await session.flush()
    await set_employee_institutions(session, employee, institution_ids)
    return _redirect_admin("employees")


@app.post("/admin/employees/{employee_id}/update")
async def web_update_employee(
    employee_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    settings = get_settings()
    _admin_required(request, settings)
    employee = await session.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    form = await request.form()
    institution_ids = _institution_ids_from_form(form)
    if not institution_ids:
        raise HTTPException(status_code=400, detail="Выберите хотя бы одно учреждение")
    employee.full_name = _form_text(form.get("full_name"), "ФИО", required=True) or employee.full_name
    employee.position = _form_text(form.get("position"), "Должность")
    await set_employee_institutions(session, employee, institution_ids)
    return _redirect_admin("employees")


@app.post("/admin/employees/{employee_id}/archive")
async def web_archive_employee(
    employee_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    settings = get_settings()
    _admin_required(request, settings)
    employee = await session.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    employee.archived = not employee.archived
    return _redirect_admin("employees")


@app.post("/admin/reviews/{feedback_id}/status")
async def web_update_review_status(
    feedback_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    settings = get_settings()
    _admin_required(request, settings)
    form = await request.form()
    raw_status = str(form.get("status") or "")
    if raw_status not in {item.value for item in FeedbackStatus}:
        raise HTTPException(status_code=400, detail="Некорректный статус")
    status = FeedbackStatus(raw_status)
    await update_feedback_status(session, feedback_id, status)
    params = {
        "status": status.value,
        "institution_id": form.get("institution_id"),
        "date_from": form.get("date_from"),
        "date_to": form.get("date_to"),
        "q": form.get("q"),
        "feedback_type": form.get("feedback_type"),
    }
    return _redirect_admin("reviews", **params)


@app.post("/admin/admins")
async def web_add_admin(request: Request, session: AsyncSession = Depends(get_session)) -> RedirectResponse:
    settings = get_settings()
    _admin_required(request, settings)
    form = await request.form()
    await add_admin(session, _form_int(form.get("user_id"), "Telegram ID"))
    return _redirect_admin("admins")


@app.get("/admin/export.xlsx")
async def web_export_xlsx(
    request: Request,
    institution_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    session: AsyncSession = Depends(get_session),
) -> Response:
    settings = get_settings()
    _admin_required(request, settings)
    path = await export_feedback_xlsx(session, institution_id=institution_id, date_from=date_from, date_to=date_to)
    filename = f"dmed_feedback_{institution_id or 'all'}.xlsx"
    return FileResponse(path, filename=filename, background=BackgroundTask(lambda: Path(path).unlink(missing_ok=True)))


@app.get("/admin/export.pdf")
async def web_export_pdf(
    request: Request,
    institution_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    session: AsyncSession = Depends(get_session),
) -> Response:
    settings = get_settings()
    _admin_required(request, settings)
    path = await export_feedback_pdf(session, institution_id=institution_id, date_from=date_from, date_to=date_to)
    filename = f"dmed_feedback_{institution_id or 'all'}.pdf"
    return FileResponse(path, filename=filename, background=BackgroundTask(lambda: Path(path).unlink(missing_ok=True)))
