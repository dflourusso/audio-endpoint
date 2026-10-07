SENSITIVE_PARTS = ("token", "password", "secret", "hash", "cookie", "authorization")


def redact(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if any(part in str(key).lower() for part in SENSITIVE_PARTS):
                cleaned[key] = "***"
            else:
                cleaned[key] = redact(item)
        return cleaned
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
