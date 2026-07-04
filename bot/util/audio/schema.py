import hashlib
import html
import math
from typing import cast

from aiogram import types, Bot
from pydantic import BaseModel, Field

PAGE_SIZE = 10  # Telegram's InputMediaAudio media-group cap


class AudioTrackData(BaseModel):
    extractor: str
    id: str
    webpage_url: str
    title: str | None = None
    uploader: str | None = None
    duration: int | None = None
    file_id: str | None = None

    @property
    def cache_key(self) -> str:
        return f"au:{self.extractor}:{self.id}"

    @property
    def as_input_media(self) -> types.InputMediaAudio:
        assert self.file_id is not None
        return types.InputMediaAudio(
            media=cast(str, self.file_id),
            title=self.title,
            performer=self.uploader,
            duration=self.duration,
        )


class AudioRequestData(BaseModel):
    link: str
    tracks: list[AudioTrackData] = Field(default_factory=list)

    @property
    def hash16(self) -> str:
        return hashlib.sha256(self.link.encode()).hexdigest()[:16]

    @property
    def cache_key(self) -> str:
        return f"da:{self.hash16}"

    @property
    def total_pages(self) -> int:
        return math.ceil(len(self.tracks) / PAGE_SIZE) if self.tracks else 0

    def page(self, page: int) -> list[AudioTrackData]:
        start = (page - 1) * PAGE_SIZE
        return self.tracks[start:start + PAGE_SIZE]

    def pager_markup(self, page: int, root_message_id: int) -> types.InlineKeyboardMarkup | None:
        if self.total_pages <= 1:
            return None
        buttons = []
        if page > 1:
            buttons.append(
                types.InlineKeyboardButton(
                    text="◀️ Back", callback_data=f"apg:{self.hash16}:{page - 1}:{root_message_id}"
                )
            )
        if page < self.total_pages:
            buttons.append(
                types.InlineKeyboardButton(
                    text="Next ▶️", callback_data=f"apg:{self.hash16}:{page + 1}:{root_message_id}"
                )
            )
        return types.InlineKeyboardMarkup(inline_keyboard=[buttons])

    def _footer_text(self, page: int, skipped: int) -> str:
        parts = []
        if skipped:
            parts.append(f"⚠️ {skipped} unavailable")
        if self.total_pages > 1:
            parts.append(f"Page {page}/{self.total_pages}")
        parts.append(f'🔗 <a href="{html.escape(self.link)}">Source</a>')
        return "\n".join(parts)

    async def send_to_chat(self, bot: Bot, chat_id: int, reply_to_message_id: int, page: int = 1) -> list[int]:
        page_tracks = self.page(page)
        deliverable = [t for t in page_tracks if t.file_id]
        if not deliverable:
            message = await bot.send_message(
                chat_id, "❌ No tracks on this page could be downloaded.", reply_to_message_id=reply_to_message_id
            )
            return [message.message_id]

        skipped = len(page_tracks) - len(deliverable)
        markup = self.pager_markup(page, reply_to_message_id)
        footer = self._footer_text(page, skipped)

        if len(deliverable) == 1:
            t = deliverable[0]
            message = await bot.send_audio(
                chat_id, cast(str, t.file_id), performer=t.uploader, title=t.title, duration=t.duration,
                reply_to_message_id=reply_to_message_id,
            )
            message_ids = [message.message_id]
        else:
            media: list[types.InputMediaAudio] = [t.as_input_media for t in deliverable]
            messages = await bot.send_media_group(chat_id, media, reply_to_message_id=reply_to_message_id)
            message_ids = [m.message_id for m in messages]

        footer_message = await bot.send_message(chat_id, footer, parse_mode="HTML", reply_markup=markup)
        message_ids.append(footer_message.message_id)
        return message_ids
