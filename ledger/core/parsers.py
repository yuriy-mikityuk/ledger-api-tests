import codecs
import json
from decimal import Decimal

from django.conf import settings
from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser


def _reject_constant(name: str):
    raise ValueError(f"{name} is not valid JSON")


def loads_without_float(text: str | bytes):
    """json.loads where a JSON number with a fraction becomes Decimal, never float."""
    return json.loads(text, parse_float=Decimal, parse_constant=_reject_constant)


class DecimalJSONParser(JSONParser):
    def parse(self, stream, media_type=None, parser_context=None):
        encoding = (parser_context or {}).get("encoding", settings.DEFAULT_CHARSET)
        try:
            return loads_without_float(codecs.getreader(encoding)(stream).read())
        except ValueError as exc:
            raise ParseError(f"JSON parse error - {exc}")
