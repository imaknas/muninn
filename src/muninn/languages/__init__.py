"""The languages Muninn speaks (core/language.py): each is a catalog of what it says, by key, and how it is written.

A language's words live in its package, one module per area (`zh_tw/lease.py` …), each a `TEXTS` dict; a key is
used as `<area>.<key>` ("lease.busy"). A text is a template (`str.format`, braces doubled when meant literally) or,
when the wording itself depends on a value (a plural, a list), a function of the same keyword parameters. Every
language has every key, with the same parameters (tests/test_languages.py).
"""

from __future__ import annotations

import os
import re
from types import ModuleType
from typing import Callable, Iterable, Mapping, Union

Text = Union[str, Callable[..., str]]


class Catalog:
    """A Language (core/language.py) from a catalog of texts."""

    def __init__(self, code: str, name: str, texts: Mapping[str, Text], *, separator: str, sentence: re.Pattern[str]):
        self.code, self.name = code, name
        self.texts = dict(texts)
        self._separator, self._sentence = separator, sentence

    def __call__(self, key: str, /, **params: object) -> str:
        text = self.texts[key]
        return text(**params) if callable(text) else text.format(**params)

    def join(self, items: Iterable[str]) -> str:
        return self._separator.join(items)

    def sentences(self, text: str) -> list[str]:
        return [s for s in self._sentence.findall(text) if s]

    def __repr__(self) -> str:
        return f"Catalog({self.code})"


def texts(*areas: ModuleType) -> dict[str, Text]:
    """The areas' TEXTS as one catalog, each key under its area's name."""
    out: dict[str, Text] = {}
    for area in areas:
        prefix = area.__name__.rsplit(".", 1)[1]
        out.update({f"{prefix}.{k}": v for k, v in area.TEXTS.items()})
    return out


def _languages() -> dict[str, Catalog]:
    from muninn.languages import en, zh_tw

    return {lang.code: lang for lang in (zh_tw.LANGUAGE, en.LANGUAGE)}


CODES = ("zh-TW", "en")


def language(code: str) -> Catalog:
    """The language `code` names (one of CODES)."""
    langs = _languages()
    if code not in langs:
        raise ValueError(f"language: one of {', '.join(CODES)} (not {code!r})")
    return langs[code]


def system_language(environ: Mapping[str, str] = os.environ) -> str:
    """The language the machine is set to, as one Muninn speaks: Chinese for a zh locale, otherwise English."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if value := environ.get(var, ""):
            return "zh-TW" if value.lower().startswith("zh") else "en"
    return "en"
