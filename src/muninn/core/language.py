"""The language Muninn speaks to the user in.

Everything Muninn says (a reply, a card, a notification, what it tells a project's session) is asked of a Language
by key, so the code says what it means once and each language says it its own way. The few things that differ in
how a language is written, not in what it says (joining a list, where a sentence ends), are asked of it too. Each
language is one implementation (muninn/languages/), chosen where Muninn starts and passed in.
"""

from __future__ import annotations

from typing import Iterable, Protocol


class Language(Protocol):
    code: str  # BCP 47, as config and the page's <html lang> say it: "zh-TW", "en"
    name: str  # the language's name in English, for telling a model which language to write in

    def __call__(self, key: str, /, **params: object) -> str:
        """The words for `key` with `params` filled in; a key the language does not have is a KeyError."""
        ...

    def join(self, items: Iterable[str]) -> str:
        """A list run together the way this language writes one inside a sentence."""
        ...

    def sentences(self, text: str) -> list[str]:
        """`text` cut into its sentences, each with its own ending and the space or line break after it, so that
        joining them gives `text` back."""
        ...


def written_in(prompt: str, words: Language) -> str:
    """A model's prompt with the language a person reads what it writes filled in where it says {language}: the
    prompt itself is the same in every language (its JSON braces are left as they are)."""
    return prompt.replace("{language}", words.name)
