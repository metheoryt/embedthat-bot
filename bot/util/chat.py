def is_group_chat(chat_id: int) -> bool:
    """Telegram private chats have positive ids; groups, supergroups and
    channels have negative ids. The bot stays quiet on errors in the latter."""
    return chat_id < 0
