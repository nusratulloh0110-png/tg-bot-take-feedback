from pathlib import Path
from tempfile import NamedTemporaryFile

from openpyxl import Workbook
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Feedback
from app.services.feedback import average_rating


HEADERS = [
    "ID",
    "Тип",
    "Учреждение",
    "Сотрудник",
    "Дата",
    "Средняя оценка",
    "Оценки",
    "Метки",
    "Комментарий",
    "Профиль отправителя",
    "Статус",
]


async def _load_feedback(session: AsyncSession) -> list[Feedback]:
    result = await session.scalars(
        select(Feedback)
        .options(selectinload(Feedback.institution), selectinload(Feedback.employee), selectinload(Feedback.user))
        .order_by(Feedback.created_at.desc())
    )
    return list(result)


def _row(feedback: Feedback) -> list[str | float]:
    return [
        str(feedback.id),
        feedback.feedback_type.value,
        feedback.institution.name,
        feedback.employee.full_name if feedback.employee else "Не указан",
        feedback.created_at.strftime("%Y-%m-%d %H:%M"),
        average_rating(feedback.ratings),
        ", ".join(f"{key}: {value}" for key, value in feedback.ratings.items()),
        ", ".join(feedback.tags or []),
        feedback.comment or "",
        feedback.user.telegram_link or "",
        feedback.status.value,
    ]


async def export_feedback_xlsx(session: AsyncSession) -> Path:
    feedback_items = await _load_feedback(session)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Feedback"
    sheet.append(HEADERS)
    for item in feedback_items:
        sheet.append(_row(item))
    for column_cells in sheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column_cells)
        sheet.column_dimensions[column_cells[0].column_letter].width = min(max_length + 2, 60)

    tmp = NamedTemporaryFile(prefix="dmed_feedback_", suffix=".xlsx", delete=False)
    tmp.close()
    workbook.save(tmp.name)
    return Path(tmp.name)


def _register_pdf_font() -> str:
    candidates = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for path in candidates:
        if path.exists():
            pdfmetrics.registerFont(TTFont("DMEDFont", str(path)))
            return "DMEDFont"
    return "Helvetica"


async def export_feedback_pdf(session: AsyncSession) -> Path:
    feedback_items = await _load_feedback(session)
    tmp = NamedTemporaryFile(prefix="dmed_feedback_", suffix=".pdf", delete=False)
    tmp.close()

    font = _register_pdf_font()
    doc = canvas.Canvas(tmp.name, pagesize=A4)
    width, height = A4
    y = height - 40
    doc.setFont(font, 14)
    doc.drawString(40, y, "Краткий отчет по отзывам DMED")
    y -= 30
    doc.setFont(font, 9)

    for item in feedback_items[:200]:
        lines = [
            f"{item.created_at:%Y-%m-%d %H:%M} | {item.feedback_type.value} | {item.institution.name}",
            f"Сотрудник: {item.employee.full_name if item.employee else 'Не указан'} | Средняя: {average_rating(item.ratings)}",
            f"Комментарий: {(item.comment or '')[:120]}",
        ]
        for line in lines:
            doc.drawString(40, y, line)
            y -= 14
        y -= 8
        if y < 60:
            doc.showPage()
            doc.setFont(font, 9)
            y = height - 40
    doc.save()
    return Path(tmp.name)

