# what the ideas say when one cannot be renamed, settled, explored or made a project (core/ideas.py)


def _refused(kind, doing, limit, current, detail):
    doing = {"explore": "開探討用的專案", "rename": "改名", "promote": "升級成專案"}.get(doing, doing)
    if kind == "too_many":
        many = f"已經開了 {current} 個,上限 {limit} 個" if limit else "已經到上限"
        return f"探討用的專案{many};先把不用的收掉再開"
    if kind == "taken":
        return f"這個名稱已經被別的專案用掉了,{doing}不行;換一個名字"
    if kind == "bad_name":
        return f"專案那一邊不收這個名字,{doing}不行;換一個(小寫英文、數字和 -)"
    if kind == "dirty":
        return f"{doing}之前,那個專案裡還有沒 commit 的變更;先在那邊收好再說一次"
    if kind == "unreachable":
        return "專案那一邊現在連不上,等一下再試"
    return f"{doing}失敗了({detail or kind})"


TEXTS = {
    "which_idea": "是哪一個想法?",
    "bad_name": "名稱只能用小寫英文、數字和 -(像 shelf-planner),最多 40 個字;「{name}」不行",
    "which_decision": "要擱著、不要了,還是拿回來?",
    "already_project": "{name} 已經是正式專案了,不是用想法的方式擱著或丟掉",
    "no_sandbox_yet": "現在還不能開探討用的專案(專案那一邊還沒準備好)",
    "no_promote_here": "這邊還不能把想法升級成正式專案;{sandbox} 先照樣用,實驗和討論都不受影響",
    "refused": _refused,
}
