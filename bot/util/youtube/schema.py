from functools import cached_property

from aiogram import types, Bot
from pydantic import BaseModel, Field
from pytubefix import YouTube

from .enum import SourceLang, TargetLang
from bot.config import settings


class YouTubeVideoData(BaseModel):
    link: str
    target_lang: TargetLang = TargetLang.ORIGINAL

    #
    # populated after the video was processed
    #
    file_ids: list[str] = Field(default_factory=list)

    # real video size
    width: int | None = None
    height: int | None = None

    # Detected video language.
    # None if target_lang=ORIGINAL (detection was not performed)
    #   or detected language is not supported.
    source_lang: SourceLang | None = None

    # whether the video was translated to the target_lang (if target lang is not ORIGINAL)
    translated_lang: TargetLang | None = None

    # Telegram file_id of the audio-only extraction, populated on first "🎵 Get audio" request
    audio_file_id: str | None = None

    @cached_property
    def yt(self):
        # https://github.com/JuanBindez/pytubefix/pull/209
        # return YouTube(self.link, "WEB")
        return YouTube(self.link)

    @property
    def cache_key(self):
        return f"yt:{self.yt.video_id}"

    @property
    def caption(self):
        return (
            f"{self.yt.title} [{self.translated_lang or self.source_lang or TargetLang.ORIGINAL} audio]\n"
            f"{self.link}\n"
        )

    @property
    def media_group(self):
        the_group = [
            types.InputMediaVideo(
                media=file_id,
                width=self.width,
                height=self.height,
                caption=self.caption if i == 0 else None,
            )
            for i, file_id in enumerate(self.file_ids)
        ]
        return the_group

    @property
    def single_video(self):
        return dict(
            video=self.file_ids[0],
            width=self.width,
            height=self.height,
            caption=self.caption,
        )

    @property
    def audio_button_markup(self) -> types.InlineKeyboardMarkup:
        return types.InlineKeyboardMarkup(
            inline_keyboard=[[
                types.InlineKeyboardButton(text="🎵 Get audio", callback_data=f"aud:{self.yt.video_id}")
            ]]
        )

    async def send_to_chat(self, bot: Bot, chat_id: int, reply_to_message_id: int | None = None):
        if len(self.file_ids) > 1:
            await bot.send_media_group(chat_id, self.media_group, reply_to_message_id=reply_to_message_id)
        else:
            await bot.send_video(chat_id, **self.single_video, reply_to_message_id=reply_to_message_id)
        await bot.send_message(chat_id, "🎵 Want just the audio?", reply_markup=self.audio_button_markup)

    async def reply_to(self, message: types.Message):
        if len(self.file_ids) > 1:
            await message.reply_media_group(self.media_group)
        else:
            await message.reply_video(**self.single_video)
        await message.reply("🎵 Want just the audio?", reply_markup=self.audio_button_markup)
