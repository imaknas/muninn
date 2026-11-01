# Muninn beside a deep talk (core/sidecar.py)
TEXTS = {
    # a reply's first sentences, run together as one line
    "first_sentences": lambda parts: "".join(parts),
    # told to the project's session with the sentence
    "aside": "(Muninn will take care of \"{part}\" in this sentence; you don't need to.)",
}
