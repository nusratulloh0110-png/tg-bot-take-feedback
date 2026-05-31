MESSAGES = {
    "ru": {
        "access_link_required": "Для доступа к боту воспользуйтесь ссылкой от вашего учреждения.",
        "invalid_link": "Эта ссылка недействительна. Пожалуйста, запросите новую ссылку у администратора учреждения.",
        "archived_institution": "Сбор отзывов для этого учреждения сейчас закрыт.",
        "welcome": "👋 Добро пожаловать! Вы подключились как сотрудник {institution}. Здесь вы можете оставить анонимный отзыв о процессе внедрения DMED. Ваши данные не передаются коллегам.",
        "main_menu": "Выберите действие:",
        "anonymity_banner": "🔒 Ваш отзыв полностью анонимен. Никто из сотрудников и коллег не узнает, что именно вы оставили этот отзыв.",
        "choose_employee": "Выберите сотрудника-внедренца:",
        "no_employees": "Для вашего учреждения пока не добавлены сотрудники-внедренцы.",
        "unknown_employee": "Не знаю имени / не уверен",
        "comment_prompt": "💬 Хотите рассказать подробнее что произошло? Это необязательно.",
        "write_comment": "Написать комментарий",
        "skip": "Пропустить",
        "send": "Отправить",
        "edit": "Изменить",
        "thanks": "✅ Спасибо! Ваш анонимный отзыв принят.",
        "spam_ok": "✅ Спасибо, ваш голос учтён.",
        "choose_tags": "Выберите быстрые метки. Можно отметить несколько вариантов.",
        "done": "Готово",
        "summary_title": "Проверьте отзыв перед отправкой:",
        "ask_comment": "Напишите комментарий одним сообщением.",
        "my_reviews_empty": "У вас пока нет отзывов.",
        "settings_prompt": "Выберите язык интерфейса:",
        "language_saved": "Язык сохранён.",
        "help": "Бот принимает анонимные отзывы только после перехода по ссылке учреждения. Используйте /feedback для нового отзыва, /my_reviews для истории и /settings для выбора языка.",
        "not_bound": "Сначала откройте бота по ссылке вашего учреждения.",
        "feedback_menu": "Что хотите оценить?",
        "employee_feedback": "👨‍⚕️ Оценить сотрудника",
        "implementation_feedback": "🏥 Оценить внедрение",
        "my_reviews": "📋 Мои отзывы",
        "settings": "⚙️ Настройки",
    },
    "uz": {
        "access_link_required": "Botdan foydalanish uchun muassasangiz bergan havoladan o'ting.",
        "invalid_link": "Bu havola yaroqsiz. Muassasa administratoridan yangi havolani so'rang.",
        "archived_institution": "Bu muassasa bo'yicha fikr-mulohaza yig'ish hozir yopilgan.",
        "welcome": "👋 Xush kelibsiz! Siz {institution} xodimi sifatida ulandingiz. Bu yerda DMED joriy etilishi bo'yicha anonim fikr qoldirishingiz mumkin.",
        "main_menu": "Amalni tanlang:",
        "anonymity_banner": "🔒 Fikringiz to'liq anonim. Hamkasblaringiz aynan siz bu fikrni qoldirganingizni bilmaydi.",
        "choose_employee": "Joriy etish xodimini tanlang:",
        "no_employees": "Muassasangiz uchun xodimlar hali qo'shilmagan.",
        "unknown_employee": "Ismini bilmayman / ishonchim komil emas",
        "comment_prompt": "💬 Batafsil izoh qoldirmoqchimisiz? Bu majburiy emas.",
        "write_comment": "Izoh yozish",
        "skip": "O'tkazib yuborish",
        "send": "Yuborish",
        "edit": "O'zgartirish",
        "thanks": "✅ Rahmat! Anonim fikringiz qabul qilindi.",
        "spam_ok": "✅ Rahmat, ovozingiz hisobga olindi.",
        "choose_tags": "Tezkor belgilarni tanlang. Bir nechta variantni belgilash mumkin.",
        "done": "Tayyor",
        "summary_title": "Yuborishdan oldin fikrni tekshiring:",
        "ask_comment": "Izohni bitta xabarda yozing.",
        "my_reviews_empty": "Sizda hali fikrlar yo'q.",
        "settings_prompt": "Interfeys tilini tanlang:",
        "language_saved": "Til saqlandi.",
        "help": "Bot faqat muassasa havolasi orqali kirilgandan keyin anonim fikr qabul qiladi. Yangi fikr uchun /feedback, tarix uchun /my_reviews, til uchun /settings.",
        "not_bound": "Avval botni muassasangiz havolasi orqali oching.",
        "feedback_menu": "Nimani baholamoqchisiz?",
        "employee_feedback": "👨‍⚕️ Xodimni baholash",
        "implementation_feedback": "🏥 Joriy etishni baholash",
        "my_reviews": "📋 Fikrlarim",
        "settings": "⚙️ Sozlamalar",
    },
}


def normalize_lang(language: str | None) -> str:
    return language if language in MESSAGES else "ru"


def t(language: str | None, key: str, **kwargs: object) -> str:
    lang = normalize_lang(language)
    message = MESSAGES[lang].get(key) or MESSAGES["ru"][key]
    return message.format(**kwargs)

