import os
import redis

redis_host = os.getenv("REDIS_HOST", "localhost")
redis_port = int(os.getenv("REDIS_PORT", 6379))

r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)

def save_user_message(user_id, message):
    key = f"user:{user_id}:messages"
    r.rpush(key, message)

def get_user_history(user_id, limit=10):
    key = f"user:{user_id}:messages"
    return r.lrange(key, -limit, -1)

def clear_user_history(user_id):
    key = f"user:{user_id}:messages"
    r.delete(key)
