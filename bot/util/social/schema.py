import hashlib

from aiogram import Bot, types
from pydantic import BaseModel, Field


class SocialVideoData(BaseModel):
    link: str
    origin: str = ""
    file_ids: list[str] = Field(default_factory=list)
    video_id: str | None = None
    width: int | None = None
    height: int | None = None
    title: str | None = None

    @property
    def cache_key(self) -> str:
        return f"dl:{hashlib.sha256(self.link.encode()).hexdigest()[:16]}"

    @property
    def caption(self) -> str:
        # title_line = f"{self.title}\n" if self.title else ""
        # return f"{title_line}{self.link}\nby @{settings.bot_username}"
        return self.link

    async def reply_to(self, message: types.Message) -> None:
        if len(self.file_ids) > 1:
            group = [
                types.InputMediaVideo(
                    media=fid,
                    width=self.width,
                    height=self.height,
                    caption=self.caption if i == 0 else None,
                )
                for i, fid in enumerate(self.file_ids)
            ]
            await message.reply_media_group(group)
        else:
            await message.reply_video(
                video=self.file_ids[0],
                width=self.width,
                height=self.height,
                caption=self.caption,
            )

    async def send_to_chat(self, bot: Bot, chat_id: int, reply_to_message_id: int | None = None) -> None:
        if len(self.file_ids) > 1:
            group = [
                types.InputMediaVideo(
                    media=fid,
                    width=self.width,
                    height=self.height,
                    caption=self.caption if i == 0 else None,
                )
                for i, fid in enumerate(self.file_ids)
            ]
            await bot.send_media_group(chat_id, group, reply_to_message_id=reply_to_message_id)
        else:
            await bot.send_video(
                chat_id,
                video=self.file_ids[0],
                width=self.width,
                height=self.height,
                caption=self.caption,
                reply_to_message_id=reply_to_message_id,
            )
