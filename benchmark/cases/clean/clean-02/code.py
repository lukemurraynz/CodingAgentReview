import json


def parse_payload(raw: bytes) -> dict:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError('payload must be an object')
    return data
