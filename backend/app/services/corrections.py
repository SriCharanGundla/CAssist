from copy import deepcopy
from typing import Any

from app.models import Correction


class InvalidCorrectionPath(ValueError):
    pass


def _decode_token(token: str) -> str:
    decoded: list[str] = []
    index = 0
    while index < len(token):
        character = token[index]
        if character != "~":
            decoded.append(character)
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            raise InvalidCorrectionPath("JSON Pointer contains an invalid escape")
        decoded.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(decoded)


def _tokens(field_path: str) -> list[str]:
    if not field_path.startswith("/"):
        raise InvalidCorrectionPath("Correction path must be a JSON Pointer")
    return [_decode_token(token) for token in field_path[1:].split("/")]


def _list_index(token: str, length: int) -> int:
    if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
        raise InvalidCorrectionPath("JSON Pointer list index is invalid")
    index = int(token)
    if index >= length:
        raise InvalidCorrectionPath("JSON Pointer list index is out of bounds")
    return index


def replace_pointer(document: dict[str, Any], field_path: str, value: Any) -> Any:
    tokens = _tokens(field_path)
    current: Any = document
    for token in tokens[:-1]:
        if isinstance(current, dict):
            if token not in current:
                raise InvalidCorrectionPath("JSON Pointer field does not exist")
            current = current[token]
        elif isinstance(current, list):
            current = current[_list_index(token, len(current))]
        else:
            raise InvalidCorrectionPath("JSON Pointer traverses a scalar value")

    final_token = tokens[-1]
    if isinstance(current, dict):
        if final_token not in current:
            raise InvalidCorrectionPath("JSON Pointer field does not exist")
        previous = current[final_token]
        current[final_token] = value
        return previous
    if isinstance(current, list):
        index = _list_index(final_token, len(current))
        previous = current[index]
        current[index] = value
        return previous
    raise InvalidCorrectionPath("JSON Pointer targets a scalar value")


def apply_corrections(
    canonical_data: dict[str, Any],
    corrections: list[Correction],
) -> dict[str, Any]:
    effective_data = deepcopy(canonical_data)
    for correction in corrections:
        replace_pointer(effective_data, correction.field_path, correction.corrected_value)
    return effective_data
