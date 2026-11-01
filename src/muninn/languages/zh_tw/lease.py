# lending a conversation to a project's session, and the permission requests it makes (core/lease.py)


def _talked_until(project, at):
    return f"跟 {project} 談到 {at.month}/{at.day} {at:%H:%M}"


def _heard(said):
    return "".join(x if x[-1:] in "。!?!?" else f"{x}。" for x in said)


TEXTS = {
    # why the runner refused, by its category
    "not_registered": "這個專案不能借用(不在登錄裡)。",
    "cap_reached": "同時借出的對話已經到上限,先收回一個。",
    "busy": "那邊還在忙(終端機還開著,或正在交接),先關掉終端機再試。",
    "not_found": "找不到這個租約。",
    "agent_login": "專案那邊的 session 沒有登入,要先在那台電腦上重新登入。",
    "rate_limit": "額度用完了,等額度重置再談。",
    "unreachable": "連不到負責跑 session 的服務。",
    "work_not_allowed": "這個專案現在不能動手(專案登錄沒開,或沙盒還沒準備好)。",
    "runtime_unsupported": "跑 session 的那台電腦現在的執行環境開不了深談。",
    "trouble": "那邊出了問題({category})。",
    # what a switch of mode is noted as in the talk
    "mode_work": "允許動手:之後它改檔案、跑指令前會先問你(先前允許過的要重新允許)",
    "mode_discuss": "改回唯讀",
    # the user's side
    "quota_used": "額度已用 {used}",
    "quota_window": "{window} {percent}%",
    "quota_confirm": "{warning}。還是要開始?",
    "already_lent": "這個對話已經借出去了,先收回",
    "not_attachable": "「{project}」不能借用;可以的有:{allowed}",
    "none_reachable": "(連不到負責的服務)",
    "handed_over": "把剛剛談的交給 {project},它從那裡接著做",
    "handed_over_files": "把剛剛談的和傳的檔案交給 {project},它從那裡接著做",
    "rejoin_after_handover": "接回 {project}:它寫完上一段的交接就接著回你。",
    "file_gone": "有個檔案已經不在了",
    "handover_stuck": "交接一直沒寫完",
    "not_rejoined": "沒能接回 {project}({why});要的話再說一次。",
    "not_lent": "這個對話沒有借出去",
    "in_terminal": "這段對話正在終端機裡,先按「收回」",
    "taking_back": "正在收回,等交接完成",
    "sent_files": "(傳了 {names})",
    "talk_gone": "那段深談已經不在了,這個對話回到 Muninn。",
    "which_mode": "要「動手」還是「唯讀」?",
    "mode_busy": "那邊正在回答或等你回覆,這輪結束再切換。",
    # the runner's side
    "not_resumed": "接不回上次那段對話({detail}),從新的對話開始。",
    "a_file": "檔案",
    "saved_files": "存進 {project}({n} 個檔)",
    "talk_lost": "跟 {project} 的深談斷掉了(那邊已經沒有這段 session),對話回到 Muninn。",
    "talked_until": _talked_until,
    # what a talk is told
    "files_for_you": "他傳了這些檔案給你。",
    "left_undecided": "你上次交接時留了這些還沒決定的事,在談的時候直接問他:\n{questions}",
    "said_by_user": "他",
    "said_line": "{who}:{text}",
    "back_since": "他回來接著跟你談。你們上次談完之後,他跟 Muninn 又說了:\n\n{said}\n\n從這裡接著做。",
    "take_over": "他剛剛跟 Muninn 談到這裡,接下來由你接手,實際去查、去試:\n\n{said}\n\n從這裡接著做,先說你打算怎麼試。",
    "heard": _heard,
    "relayed_alone": "(Muninn 剛才在旁邊回他,跟你有關的是:{heard}你知道就好,不用回。)",
    "relayed_before": "(Muninn 剛才在旁邊回他,跟你有關的是:{heard})",
    # what Muninn keeps of what it said beside a talk
    "told": "告訴 {project}:{text}",
    "not_yet_told": "還沒來得及告訴 {project}:{text}",
    "said_beside": "跟 {project} 談的時候,我在旁邊說了:\n{lines}",
    # the line that says the conversation was handed over
    "lent_resumed": "接回剛才跟 {project} 的那段:接下來你說的會直接給它。談完或你去談別的,它會自己交回來。",
    "lent_work": "開了 {project} 在試:接下來你說的會直接給它(它可以在自己的資料夾裡改檔案;跑指令先問你,你允許過的那種之後自動跑)。"
                 "談完或你去談別的,它會自己交回來。",
    "lent_discuss": "接下來你說的會直接給 {project}(它只讀、查、問你,不會改東西)。談完或你去談別的,它會自己交回來。",
    # a talk's handover, as it joins the conversation
    "handover_none": "跟 {project} 的深談結束了(沒有交接內容)。",
    "handover_failed": "跟 {project} 的深談結束了,但交接沒有成功產生;要的話再接回去問一次。",
    "handover_over": "跟 {project} 的深談結束了。",
    "handover_decided": "決定:{items}",
    "handover_open": "還沒決定:{items}",
    "handover_tasks": "給你的待辦:{items}",
    "listed": lambda items: ";".join(items),
    # a permission request's push
    "a_project": "專案",
    "push_ask": "{project} 問你一件事",
    "push_write": "{project} 想改一個檔案",
    "push_run": "{project} 想跑一個指令",
    "push_web": "{project} 想打開一個網頁",
    "push_other": "{project} 想做一件事",
    # deciding one
    "already_decided": "這個請求已經決定過了",
    "commands_each": "指令每一個都要問;只能允許這一次",
    "session_web_files": "只有網頁和寫檔可以「這次對話都允許」",
    "open_site": "這個網站任何人都能寫入,不能整段對話都允許;只能允許這一次",
    "pick_option": "回答問題要選一個選項",
    # what became of one
    "ask_unanswered": "沒回答這個問題",
    "ask_declined": "沒用選項回答,讓它用文字問",
    "ask_chose": "你選了:{answer}",
    "ask_pair": "{question} → {answer}",
    "write_timeout": "沒回應,沒讓它改 {what}",
    "write_allowed": "允許改 {what} 一次",
    "write_allowed_session": "這次深談都允許改這個專案資料夾裡的檔案",
    "write_denied": "拒絕改 {what}",
    "run_timeout": "沒回應,沒讓它跑:{what}",
    "run_auto": "自動允許跑:{what}(你之前都允許這種)",
    "run_allowed": "允許跑:{what}",
    "run_denied": "拒絕跑:{what}",
    "this_thing": "這件事",
    "other_timeout": "沒回應,沒讓它做:{what}",
    "other_allowed": "允許:{what}",
    "other_denied": "拒絕:{what}",
    "this_page": "這個網頁",
    "web_timeout": "沒回應,沒讓它打開 {host}",
    "web_allowed": "允許打開 {host} 一次",
    "web_allowed_session": "這次對話都允許 {host}",
    "web_denied": "拒絕打開 {host}",
}
