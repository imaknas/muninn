# how the user reads Muninn's own values (core/models.py)
TEXTS = {
    "status": lambda status: {"todo": "待投遞", "applied": "已投遞", "screening": "篩選中", "interview": "面試",
                              "offer": "offer", "rejected": "被拒", "withdrawn": "已撤回"}.get(status, status),
}
