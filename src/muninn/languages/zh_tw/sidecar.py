# Muninn beside a deep talk (core/sidecar.py)
TEXTS = {
    # a reply's first sentences, run together as one line
    "first_sentences": lambda parts: "".join(p.strip() for p in parts),
    # told to the project's session with the sentence
    "aside": "(這句裡「{part}」Muninn 會處理,你不用做。)",
}
