import secrets


def generate_institution_token() -> str:
    return f"INST_{secrets.token_hex(4)}"


def build_deep_link(bot_username: str, token: str) -> str:
    clean_username = bot_username.lstrip("@")
    return f"https://t.me/{clean_username}?start={token}"

