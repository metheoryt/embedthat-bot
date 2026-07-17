from collections.abc import Callable
from functools import wraps

from pytubefix import exceptions as pytubefix_exc


class YouTubeError(Exception):
    pass


# pytubefix folds two very different situations into VideoUnavailable subclasses:
# "this one video is off-limits" (nothing an operator can do -- just tell the user)
# and "YouTube is refusing *us*" (BotDetection/PoTokenRequired -- someone must rotate
# tokens or back off). Only the former is listed here.
#
# This is deliberately an allowlist rather than "VideoUnavailable minus a denylist":
# an unlisted exception keeps propagating and pages the admin, so a pytubefix upgrade
# that adds a new per-video case costs one alert, while a new *systemic* case still
# gets shouted about instead of being silently swallowed as a user error.
#
# The reason text is user-visible verbatim -- actors.py renders it as
# "Couldn't process this video: {e}" -- so keep it a plain clause.
_PERMANENT_REASONS: tuple[tuple[type[Exception], str], ...] = (
    (pytubefix_exc.LoginRequired, "YouTube requires signing in to view it"),
    (pytubefix_exc.MembersOnly, "it's for channel members only"),
    (pytubefix_exc.VideoPrivate, "it's private"),
    (pytubefix_exc.AgeRestrictedError, "it's age-restricted"),
    (pytubefix_exc.AgeCheckRequiredError, "it needs an age check"),
    (pytubefix_exc.AgeCheckRequiredAccountError, "it needs an age-verified account"),
    (pytubefix_exc.VideoRemovedByUploader, "the uploader removed it"),
    (pytubefix_exc.VideoRemovedByYouTubeForViolatingTOS, "YouTube removed it"),
    (pytubefix_exc.VideoBlockedByCopyright, "it's blocked on copyright grounds"),
    (pytubefix_exc.AccountTerminated, "the uploader's account was terminated"),
    (pytubefix_exc.RecordingUnavailable, "the recording isn't available"),
    (pytubefix_exc.LiveStreamError, "it's a live stream"),
    (pytubefix_exc.LiveStreamOffline, "the live stream is offline"),
    (pytubefix_exc.LiveStreamEnded, "the live stream has ended"),
)

def _permanent_reason(exc: BaseException) -> str | None:
    for cls, reason in _PERMANENT_REASONS:
        if isinstance(exc, cls):
            return reason
    return None


def translates_youtube_errors[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Convert pytubefix's per-video "off-limits" exceptions into YouTubeError.

    Callers already treat YouTubeError as unrecoverable-but-expected: the actors list
    it in `throws`, so dramatiq stops retrying and skips the admin alert, and the
    waiter gets a real explanation instead of a spinner that never resolves.
    Anything unlisted propagates untouched and still pages.
    """

    @wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except YouTubeError:
            raise
        except pytubefix_exc.PytubeFixError as e:
            reason = _permanent_reason(e)
            if reason is None:
                raise
            raise YouTubeError(reason) from e

    return wrapper
