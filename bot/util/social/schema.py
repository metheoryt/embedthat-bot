from aiogram import types, Bot
from pydantic import BaseModel, Field

from bot.config import settings


class SocialVideoData(BaseModel):
    link: str
    origin: str = ""
    file_ids: list[str] = Field(default_factory=list)
    video_id: str | None = None
    width: int | None = None
    height: int | None = None
    title: str | None = None

    @property
    def caption(self) -> str:
        # title_line = f"{self.title}\n" if self.title else ""
        # return f"{title_line}{self.link}\nby @{settings.bot_username}"
        return self.link

    async def reply_to(self, message: types.Message) -> None:
        if len(self.file_ids) > 1:
            group = [
                types.InputMediaVideo(media=fid, width=self.width, height=self.height)
                for fid in self.file_ids
            ]
            group[0].caption = self.caption
            await message.reply_media_group(group)
        else:
            await message.reply_video(
                video=self.file_ids[0],
                width=self.width,
                height=self.height,
                caption=self.caption,
            )

    async def send_to_chat(self, bot: Bot, chat_id: int) -> None:
        if len(self.file_ids) > 1:
            group = [
                types.InputMediaVideo(media=fid, width=self.width, height=self.height)
                for fid in self.file_ids
            ]
            group[0].caption = self.caption
            await bot.send_media_group(chat_id, group)
        else:
            await bot.send_video(
                chat_id,
                video=self.file_ids[0],
                width=self.width,
                height=self.height,
                caption=self.caption,
            )
