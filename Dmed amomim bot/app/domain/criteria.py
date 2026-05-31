from dataclasses import dataclass


@dataclass(frozen=True)
class Criterion:
    code: str
    ru: str
    uz: str


@dataclass(frozen=True)
class QuickTag:
    code: str
    ru: str
    uz: str


EMPLOYEE_CRITERIA = [
    Criterion("professionalism", "Насколько компетентен специалист?", "Mutaxassis qanchalik malakali?"),
    Criterion("communication", "Насколько понятно объяснял?", "Qanchalik tushunarli tushuntirdi?"),
    Criterion("punctuality", "Приходил вовремя / был доступен?", "O'z vaqtida keldimi yoki aloqada bo'ldimi?"),
    Criterion("attitude", "Был ли уважителен и терпелив?", "Hurmatli va sabrli munosabatda bo'ldimi?"),
    Criterion("overall", "Ваша итоговая оценка сотрудника", "Xodimga umumiy bahoingiz"),
]

IMPLEMENTATION_CRITERIA = [
    Criterion("training_quality", "Было ли обучение понятным?", "O'qitish tushunarli bo'ldimi?"),
    Criterion("system_usability", "Насколько удобна система DMED?", "DMED tizimi qanchalik qulay?"),
    Criterion("tech_support", "Быстро ли решались проблемы?", "Muammolar tez hal qilindimi?"),
    Criterion("implementation_completeness", "Все ли функции работают?", "Barcha funksiyalar ishlayaptimi?"),
    Criterion("staff_readiness", "Чувствуете ли уверенность в работе с DMED?", "DMED bilan ishlashda o'zingizni ishonchli his qilyapsizmi?"),
    Criterion("overall", "Итоговая оценка процесса", "Jarayonga umumiy baho"),
]

IMPLEMENTATION_TAGS = [
    QuickTag("unstable", "Система работает нестабильно", "Tizim barqaror ishlamayapti"),
    QuickTag("training_lack", "Не хватает обучения", "O'qitish yetarli emas"),
    QuickTag("complex_ui", "Сложный интерфейс", "Interfeys murakkab"),
    QuickTag("good_support", "Хорошая техподдержка", "Texnik yordam yaxshi"),
    QuickTag("works_well", "Всё работает отлично", "Hammasi yaxshi ishlayapti"),
    QuickTag("need_help", "Нужна дополнительная помощь", "Qo'shimcha yordam kerak"),
]


def criterion_by_code(criteria: list[Criterion], code: str) -> Criterion | None:
    return next((item for item in criteria if item.code == code), None)


def label_for_rating(value: int) -> str:
    return "⭐" * value

