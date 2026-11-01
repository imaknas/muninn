# what the ideas say when one cannot be renamed, settled, explored or made a project (core/ideas.py)


def _refused(kind, doing, limit, current, detail):
    doing = {"explore": "opening a project to explore it", "rename": "renaming it",
             "promote": "making it a project"}.get(doing, doing)
    if kind == "too_many":
        many = f"{current} are open and the limit is {limit}" if limit else "the limit is reached"
        return f"Projects for exploring ideas: {many}; close the ones you don't use first"
    if kind == "taken":
        return f"That name is already another project's, so {doing} didn't work; pick another name"
    if kind == "bad_name":
        return f"The projects side doesn't take that name, so {doing} didn't work; pick another (lowercase letters, digits and -)"
    if kind == "dirty":
        return f"Before {doing}: that project has changes not committed yet; tidy them up there, then say it again"
    if kind == "unreachable":
        return "Can't reach the projects side right now; try again in a moment"
    return f"{doing[0].upper()}{doing[1:]} failed ({detail or kind})"


TEXTS = {
    "which_idea": "Which idea?",
    "bad_name": "A name is lowercase letters, digits and - (like shelf-planner), at most 40 characters; \"{name}\" won't do",
    "which_decision": "Shelve it, drop it, or take it back up?",
    "already_project": "{name} is a real project now; it isn't shelved or dropped the way an idea is",
    "no_sandbox_yet": "Can't open a project to explore it yet (the projects side isn't ready)",
    "no_promote_here": "Ideas can't be made real projects here yet; keep using {sandbox} as it is, experiments and discussion aren't affected",
    "refused": _refused,
}
