import dramatiq
from dramatiq.brokers.redis import RedisBroker

from bot.config import settings

broker = RedisBroker(url=str(settings.redis_dsn))
dramatiq.set_broker(broker)
