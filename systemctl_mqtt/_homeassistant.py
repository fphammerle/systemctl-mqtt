import re

import systemctl_mqtt._utils

NODE_ID_ALLOWED_CHARS = r"a-zA-Z0-9_-"


def get_default_node_id() -> str:
    return re.sub(
        r"[^{}]".format(NODE_ID_ALLOWED_CHARS),
        "",
        # pylint: disable=protected-access
        systemctl_mqtt._utils.get_hostname(),
    )


def validate_node_id(node_id: str) -> bool:
    return re.match(r"^[{}]+$".format(NODE_ID_ALLOWED_CHARS), node_id) is not None
