import json


def encode_bool(value: bool) -> str:
    return json.dumps(value)
