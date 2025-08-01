from aiosignal import Signal


def signal_handler(signal: Signal):
    def decorator(func):
        signal.append(func)
        return func

    return decorator


def freeze_signals():
    for sig in [on_link_received, on_link_sent, on_yt_video_sent, on_yt_video_fail]:
        sig.freeze()


on_yt_video_sent = Signal(
    "on_yt_video_sent(link: str, message: Message, video: YouTubeVideoData, fresh: bool)"
)
on_yt_video_fail = Signal(
    "on_yt_video_fail(link: str, message: Message)"
)
on_link_sent = Signal("on_link_sent(link: str, message: Message, origin: LinkOrigin)")
on_link_received = Signal("on_link_received(message: Message, origin: LinkOrigin)")
