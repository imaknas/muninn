# how the user reads Muninn's own values (core/models.py)
TEXTS = {
    "status": lambda status: {"todo": "to apply", "applied": "applied", "screening": "screening", "interview": "interview",
                              "offer": "offer", "rejected": "rejected", "withdrawn": "withdrawn"}.get(status, status),
}
