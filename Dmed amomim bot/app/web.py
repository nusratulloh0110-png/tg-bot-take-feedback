import secrets
from collections.abc import AsyncIterator
from html import escape
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.background import BackgroundTask

from app.config import Settings, get_settings
from app.db.models import Admin, Employee, Feedback, FeedbackStatus, Institution
from app.db.session import SessionFactory
from app.domain.criteria import criterion_label, tag_label
from app.services.admins import add_admin
from app.services.analytics import feedback_report_data
from app.services.export import export_feedback_pdf, export_feedback_xlsx
from app.services.feedback import average_rating, update_feedback_status
from app.services.institutions import create_institution, reissue_token
from app.services.tokens import build_deep_link

app = FastAPI(title="DMED Admin Panel")


STATUS_LABELS = {
    FeedbackStatus.new: "Новый",
    FeedbackStatus.reviewed: "Изучено",
    FeedbackStatus.in_progress: "В работе",
    FeedbackStatus.closed: "Закрыто",
}

TYPE_LABELS = {
    "employee": "Сотрудник",
    "implementation": "Внедрение",
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


def _redirect_admin() -> RedirectResponse:
    return RedirectResponse("/admin", status_code=303)


def _admin_required(request: Request, settings: Settings) -> None:
    if not _authenticated(request, settings):
        raise HTTPException(status_code=403, detail="Нет доступа")


def _esc(value: object) -> str:
    return escape(str(value or ""))


def _selected(value: object, current: object) -> str:
    return " selected" if str(value) == str(current) else ""


def _status_options(current: str | None = None, include_all: bool = True) -> str:
    options = ['<option value="">Все статусы</option>'] if include_all else []
    for status, label in STATUS_LABELS.items():
        options.append(f'<option value="{status.value}"{_selected(status.value, current)}>{label}</option>')
    return "\n".join(options)


def _institution_options(institutions: list[Institution], current: int | None = None, include_all: bool = True) -> str:
    options = ['<option value="">Все учреждения</option>'] if include_all else []
    for institution in institutions:
        options.append(
            f'<option value="{institution.id}"{_selected(institution.id, current)}>'
            f"#{institution.id} {_esc(institution.name)}</option>"
        )
    return "\n".join(options)


def _rating_bar(value: float) -> str:
    percent = min(max(value / 5 * 100, 0), 100)
    return (
        f'<span class="rating"><span class="rating__fill" style="width:{percent:.0f}%"></span></span>'
        f'<strong>{value}</strong>'
    )


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


def _page(title: str, body: str, request: Request | None = None) -> HTMLResponse:
    css = """
    :root {
      --bg: #f5f7fb;
      --panel: #ffffff;
      --line: #dbe3ef;
      --text: #182033;
      --muted: #657083;
      --accent: #0f8f8c;
      --accent-2: #3454d1;
      --danger: #b42318;
      --warn: #b7791f;
      --ok: #267a3e;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--text);
      background: var(--bg);
      font-family: Arial, Helvetica, sans-serif;
      font-size: 14px;
      line-height: 1.45;
    }
    a { color: var(--accent-2); text-decoration: none; }
    .topbar {
      position: sticky;
      top: 0;
      z-index: 2;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      min-height: 58px;
      padding: 0 28px;
      background: #ffffff;
      border-bottom: 1px solid var(--line);
    }
    .brand { font-size: 18px; font-weight: 700; letter-spacing: 0; }
    .nav { display: flex; gap: 12px; flex-wrap: wrap; }
    .nav a { color: var(--muted); font-weight: 600; }
    .nav a:hover { color: var(--text); }
    .shell { max-width: 1440px; margin: 0 auto; padding: 22px 28px 40px; }
    .toolbar, .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      margin-bottom: 16px;
    }
    .toolbar {
      display: grid;
      grid-template-columns: minmax(220px, 1fr) 150px 160px auto auto;
      gap: 10px;
      align-items: end;
    }
    h1, h2 { margin: 0 0 12px; line-height: 1.15; letter-spacing: 0; }
    h1 { font-size: 24px; }
    h2 { font-size: 18px; }
    label { display: block; margin: 0 0 5px; color: var(--muted); font-size: 12px; font-weight: 700; }
    input, select, textarea {
      width: 100%;
      min-height: 36px;
      padding: 8px 10px;
      color: var(--text);
      background: #fff;
      border: 1px solid #cbd5e1;
      border-radius: 6px;
      font: inherit;
    }
    textarea { min-height: 64px; resize: vertical; }
    button, .button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 34px;
      padding: 7px 11px;
      border: 1px solid #b9c6d8;
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      white-space: nowrap;
    }
    button:hover, .button:hover { border-color: var(--accent); color: var(--accent); }
    .primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    .primary:hover { color: #fff; filter: brightness(0.96); }
    .danger { color: var(--danger); border-color: #e7b8b3; }
    .inline-actions { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(160px, 1fr)); gap: 12px; margin-bottom: 16px; }
    .metric {
      min-height: 94px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }
    .metric span { display: block; color: var(--muted); font-size: 12px; font-weight: 700; }
    .metric strong { display: block; margin-top: 8px; font-size: 26px; line-height: 1; }
    .metric small { display: block; margin-top: 8px; color: var(--muted); }
    .split { display: grid; grid-template-columns: minmax(0, 1fr) minmax(320px, 420px); gap: 16px; }
    .form-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)) auto; gap: 10px; align-items: end; }
    .table-wrap { overflow: auto; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
    table { width: 100%; border-collapse: collapse; min-width: 900px; }
    th, td { padding: 9px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { background: #eef3f9; color: #394255; font-size: 12px; text-transform: uppercase; letter-spacing: 0; }
    tr:last-child td { border-bottom: 0; }
    .muted { color: var(--muted); }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 2px 8px;
      border-radius: 999px;
      background: #eef3f9;
      color: #334155;
      font-size: 12px;
      font-weight: 700;
    }
    .badge.ok { background: #e8f6ee; color: var(--ok); }
    .badge.warn { background: #fff4dc; color: var(--warn); }
    .badge.danger { background: #ffe8e5; color: var(--danger); }
    .rating { position: relative; display: inline-block; width: 54px; height: 7px; margin-right: 8px; background: #d9e1ec; border-radius: 999px; overflow: hidden; }
    .rating__fill { display: block; height: 100%; background: var(--accent); }
    .compact-input { min-width: 140px; }
    .review-comment { max-width: 360px; white-space: pre-wrap; }
    .login {
      max-width: 420px;
      margin: 80px auto;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 22px;
    }
    @media (max-width: 980px) {
      .toolbar, .split, .form-grid, .grid { grid-template-columns: 1fr; }
      .topbar { align-items: flex-start; flex-direction: column; padding: 14px 18px; }
      .shell { padding: 18px; }
    }
    """
    nav = ""
    if request is not None:
        nav = """
        <nav class="nav">
          <a href="#reviews">Отзывы</a>
          <a href="#institutions">Учреждения</a>
          <a href="#employees">Сотрудники</a>
          <a href="#admins">Админы</a>
          <a href="/admin/logout">Выйти</a>
        </nav>
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
      <header class="topbar">
        <div class="brand">DMED Admin</div>
        {nav}
      </header>
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


def _filters(
    institutions: list[Institution],
    institution_id: int | None,
    days: int,
    status: str | None,
) -> str:
    export_suffix = f"?institution_id={institution_id}" if institution_id else ""
    return f"""
    <form class="toolbar" method="get" action="/admin">
      <div>
        <label>Учреждение</label>
        <select name="institution_id">{_institution_options(institutions, institution_id)}</select>
      </div>
      <div>
        <label>Период</label>
        <select name="days">
          <option value="7"{_selected(7, days)}>7 дней</option>
          <option value="30"{_selected(30, days)}>30 дней</option>
          <option value="90"{_selected(90, days)}>90 дней</option>
          <option value="0"{_selected(0, days)}>Все время</option>
        </select>
      </div>
      <div>
        <label>Статус отзывов</label>
        <select name="status">{_status_options(status)}</select>
      </div>
      <button class="primary" type="submit">Показать</button>
      <div class="inline-actions">
        <a class="button" href="/admin/export.xlsx{export_suffix}">Excel</a>
        <a class="button" href="/admin/export.pdf{export_suffix}">PDF</a>
      </div>
    </form>
    """


def _institutions_section(institutions: list[Institution], settings: Settings) -> str:
    rows = []
    for institution in institutions:
        link = _institution_link(settings, institution)
        status = "Активно" if institution.token_active and not institution.archived else "Закрыто"
        badge_class = "ok" if institution.token_active and not institution.archived else "warn"
        rows.append(
            f"""
            <tr>
              <form id="inst-edit-{institution.id}" method="post" action="/admin/institutions/{institution.id}/update"></form>
              <td>#{institution.id}</td>
              <td><input form="inst-edit-{institution.id}" name="name" value="{_esc(institution.name)}"></td>
              <td><input form="inst-edit-{institution.id}" name="region" value="{_esc(institution.region)}"></td>
              <td><input form="inst-edit-{institution.id}" name="address" value="{_esc(institution.address)}"></td>
              <td><span class="badge {badge_class}">{status}</span></td>
              <td><a href="{_esc(link)}" target="_blank" rel="noreferrer">{_esc(link)}</a></td>
              <td>
                <div class="inline-actions">
                  <button form="inst-edit-{institution.id}" type="submit">Сохранить</button>
                  <form method="post" action="/admin/institutions/{institution.id}/reissue"><button type="submit">Новая ссылка</button></form>
                  <form method="post" action="/admin/institutions/{institution.id}/deactivate"><button type="submit">Отключить</button></form>
                  <form method="post" action="/admin/institutions/{institution.id}/archive"><button class="danger" type="submit">{'Вернуть' if institution.archived else 'Архив'}</button></form>
                </div>
              </td>
            </tr>
            """
        )
    rows_html = "\n".join(rows) or '<tr><td colspan="7" class="muted">Учреждений пока нет.</td></tr>'
    return f"""
    <section id="institutions" class="panel">
      <h2>Учреждения</h2>
      <form class="form-grid" method="post" action="/admin/institutions">
        <div><label>Название</label><input name="name" required></div>
        <div><label>Регион</label><input name="region"></div>
        <div><label>Адрес</label><input name="address"></div>
        <button class="primary" type="submit">Добавить</button>
      </form>
      <div style="height:12px"></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>ID</th><th>Название</th><th>Регион</th><th>Адрес</th><th>Статус</th><th>Ссылка</th><th>Действия</th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
    </section>
    """


def _employees_section(
    employees: list[Employee],
    institutions: list[Institution],
    selected_institution_id: int | None,
) -> str:
    rows = []
    for employee in employees:
        rows.append(
            f"""
            <tr>
              <form id="emp-edit-{employee.id}" method="post" action="/admin/employees/{employee.id}/update"></form>
              <td>#{employee.id}</td>
              <td><input form="emp-edit-{employee.id}" name="full_name" value="{_esc(employee.full_name)}"></td>
              <td><input form="emp-edit-{employee.id}" name="position" value="{_esc(employee.position)}"></td>
              <td>{_esc(employee.institution.name if employee.institution else employee.institution_id)}</td>
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
    <section id="employees" class="panel">
      <h2>Сотрудники-внедренцы</h2>
      <form class="form-grid" method="post" action="/admin/employees">
        <div>
          <label>Учреждение</label>
          <select name="institution_id" required>{_institution_options(institutions, selected_institution_id, include_all=False)}</select>
        </div>
        <div><label>ФИО</label><input name="full_name" required></div>
        <div><label>Должность</label><input name="position"></div>
        <button class="primary" type="submit">Добавить</button>
      </form>
      <div style="height:12px"></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>ID</th><th>ФИО</th><th>Должность</th><th>Учреждение</th><th>Статус</th><th>Действия</th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
    </section>
    """


def _reviews_section(reviews: list[Feedback]) -> str:
    rows = []
    for item in reviews:
        status_class = "ok" if item.status == FeedbackStatus.closed else "warn" if item.status == FeedbackStatus.in_progress else ""
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
              <td>{_esc(item.institution.name)}<br><span class="muted">{_esc(TYPE_LABELS.get(item.feedback_type.value, item.feedback_type.value))}: {_esc(subject)}</span></td>
              <td>{_esc(item.reviewer_full_name or '-')}<br><span class="muted">{_esc(item.reviewer_phone or '-')}</span></td>
              <td>{_rating_bar(average_rating(item.ratings))}<br><span class="muted">{ratings}</span></td>
              <td><span class="muted">{_esc(tags)}</span><div class="review-comment">{_esc(item.comment or '-')}</div></td>
              <td>
                <form method="post" action="/admin/reviews/{item.id}/status">
                  <select name="status">{_status_options(item.status.value, include_all=False)}</select>
                  <div style="height:6px"></div>
                  <button type="submit">Обновить</button>
                </form>
                <div style="height:6px"></div>
                <span class="badge {status_class}">{_esc(STATUS_LABELS.get(item.status, item.status.value))}</span>
              </td>
            </tr>
            """
        )
    rows_html = "\n".join(rows) or '<tr><td colspan="6" class="muted">Отзывов пока нет.</td></tr>'
    return f"""
    <section id="reviews" class="panel">
      <h2>Отзывы</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Дата</th><th>Объект</th><th>Отправитель</th><th>Оценки</th><th>Комментарий</th><th>Статус</th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
    </section>
    """


def _summary_sections(data: dict) -> str:
    summary = data["summary"]
    metrics = [
        _metric("Всего отзывов", summary["feedback_total"], "по выбранному фильтру"),
        _metric("Сотрудников ответило", summary["unique_reviewers_total"], "уникальные Telegram/телефон"),
        _metric("Средняя оценка", summary["average_rating"], "по всем критериям"),
        _metric("Низкие оценки", summary["low_score_total"], "2.5 и ниже"),
        _metric("Отзывы по сотрудникам", summary["employee_feedback_total"], f"средняя {summary['employee_average_rating']}"),
        _metric("Отзывы по внедрению", summary["implementation_feedback_total"], f"средняя {summary['implementation_average_rating']}"),
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
        for row in data["institutions"][:12]
    ) or '<tr><td colspan="4" class="muted">Нет данных.</td></tr>'
    employee_rows = "\n".join(
        f"""
        <tr>
          <td>{_esc(row['name'])}<br><span class="muted">{_esc(row['institution'])}</span></td>
          <td>{_esc(row['feedback_total'])}</td>
          <td>{_rating_bar(row['average_rating'])}</td>
        </tr>
        """
        for row in data["employees"][:12]
    ) or '<tr><td colspan="3" class="muted">Нет данных.</td></tr>'
    return f"""
    <div class="grid">{''.join(metrics)}</div>
    <div class="split">
      <section class="panel">
        <h2>Учреждения по оценкам</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Учреждение</th><th>Отзывы</th><th>Сотрудников</th><th>Средняя</th></tr></thead>
            <tbody>{institution_rows}</tbody>
          </table>
        </div>
      </section>
      <section class="panel">
        <h2>Сотрудники по оценкам</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Сотрудник</th><th>Отзывы</th><th>Средняя</th></tr></thead>
            <tbody>{employee_rows}</tbody>
          </table>
        </div>
      </section>
    </div>
    """


def _admins_section(settings: Settings, admins: list[int]) -> str:
    all_admins = sorted(set(settings.admin_ids) | set(admins))
    rows = "\n".join(f"<tr><td>{admin_id}</td></tr>" for admin_id in all_admins) or '<tr><td class="muted">Админов в БД нет.</td></tr>'
    return f"""
    <section id="admins" class="panel">
      <h2>Администраторы Telegram</h2>
      <form class="form-grid" method="post" action="/admin/admins">
        <div><label>Telegram ID</label><input name="user_id" inputmode="numeric" required></div>
        <button class="primary" type="submit">Добавить</button>
      </form>
      <div style="height:12px"></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Telegram ID</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </section>
    """


async def _load_dashboard(
    session: AsyncSession,
    days: int,
    institution_id: int | None,
    status: str | None,
) -> tuple[dict, list[Institution], list[Employee], list[Feedback], list[int]]:
    data = await feedback_report_data(session, days=days or None, institution_id=institution_id)
    institutions = list(await session.scalars(select(Institution).order_by(Institution.id.desc())))

    employee_query = (
        select(Employee)
        .options(selectinload(Employee.institution))
        .order_by(Employee.archived, Employee.full_name)
    )
    if institution_id is not None:
        employee_query = employee_query.where(Employee.institution_id == institution_id)
    employees = list(await session.scalars(employee_query))

    review_query = (
        select(Feedback)
        .options(selectinload(Feedback.institution), selectinload(Feedback.employee), selectinload(Feedback.user))
        .order_by(Feedback.created_at.desc())
        .limit(200)
    )
    conditions = []
    if institution_id is not None:
        conditions.append(Feedback.institution_id == institution_id)
    if status:
        conditions.append(Feedback.status == FeedbackStatus(status))
    if conditions:
        review_query = review_query.where(*conditions)
    reviews = list(await session.scalars(review_query))

    admins = list(await session.scalars(select(Admin.user_id).order_by(Admin.user_id)))
    return data, institutions, employees, reviews, admins


@app.get("/", response_class=HTMLResponse)
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
    response = _redirect_admin()
    response.set_cookie(
        "dmed_admin_token",
        configured,
        max_age=60 * 60 * 12,
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/admin/logout")
async def logout() -> Response:
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie("dmed_admin_token")
    return response


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    institution_id: int | None = None,
    days: int = 30,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> Response:
    settings = get_settings()
    if _token(settings) is None:
        return _login_page(setup_required=True)
    if not _authenticated(request, settings):
        return RedirectResponse("/admin/login", status_code=303)
    if status and status not in {item.value for item in FeedbackStatus}:
        status = None
    data, institutions, employees, reviews, admins = await _load_dashboard(session, days, institution_id, status)
    body = f"""
    <main class="shell">
      <h1>Админ-панель</h1>
      {_filters(institutions, institution_id, days, status)}
      {_summary_sections(data)}
      {_reviews_section(reviews)}
      {_institutions_section(institutions, settings)}
      {_employees_section(employees, institutions, institution_id)}
      {_admins_section(settings, admins)}
    </main>
    """
    return _page("DMED Admin", body, request)


@app.post("/admin/institutions")
async def web_create_institution(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    settings = get_settings()
    _admin_required(request, settings)
    form = await request.form()
    await create_institution(
        session,
        _form_text(form.get("name"), "Название", required=True) or "",
        _form_text(form.get("region"), "Регион"),
        _form_text(form.get("address"), "Адрес"),
    )
    return _redirect_admin()


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
    return _redirect_admin()


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
    return _redirect_admin()


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
    return _redirect_admin()


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
    return _redirect_admin()


@app.post("/admin/employees")
async def web_create_employee(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    settings = get_settings()
    _admin_required(request, settings)
    form = await request.form()
    institution_id = _form_int(form.get("institution_id"), "Учреждение")
    institution = await session.get(Institution, institution_id)
    if institution is None:
        raise HTTPException(status_code=404, detail="Учреждение не найдено")
    employee = Employee(
        institution_id=institution_id,
        full_name=_form_text(form.get("full_name"), "ФИО", required=True) or "",
        position=_form_text(form.get("position"), "Должность"),
    )
    session.add(employee)
    return _redirect_admin()


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
    employee.full_name = _form_text(form.get("full_name"), "ФИО", required=True) or employee.full_name
    employee.position = _form_text(form.get("position"), "Должность")
    return _redirect_admin()


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
    return _redirect_admin()


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
    return _redirect_admin()


@app.post("/admin/admins")
async def web_add_admin(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    settings = get_settings()
    _admin_required(request, settings)
    form = await request.form()
    await add_admin(session, _form_int(form.get("user_id"), "Telegram ID"))
    return _redirect_admin()


@app.get("/admin/export.xlsx")
async def web_export_xlsx(
    request: Request,
    institution_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> Response:
    settings = get_settings()
    _admin_required(request, settings)
    path = await export_feedback_xlsx(session, institution_id=institution_id)
    filename = f"dmed_feedback_{institution_id}.xlsx" if institution_id else "dmed_feedback.xlsx"
    return FileResponse(
        path,
        filename=filename,
        background=BackgroundTask(lambda: Path(path).unlink(missing_ok=True)),
    )


@app.get("/admin/export.pdf")
async def web_export_pdf(
    request: Request,
    institution_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> Response:
    settings = get_settings()
    _admin_required(request, settings)
    path = await export_feedback_pdf(session, institution_id=institution_id)
    filename = f"dmed_feedback_{institution_id}.pdf" if institution_id else "dmed_feedback.pdf"
    return FileResponse(
        path,
        filename=filename,
        background=BackgroundTask(lambda: Path(path).unlink(missing_ok=True)),
    )
