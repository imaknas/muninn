# filling in a job's application form (core/apply.py)
TEXTS = {
    "gone": "The posting for {title} is no longer on {site}: it looks closed. Set this packet aside?",
    "gone_set_aside": "Set aside",
    "gone_why": "the posting has closed",
    "asking": "The rest of the application form for {title} is filled in; these are left to ask you: {questions}",
    "still_asking": "Noted “{label}”. Still left: {questions}",
    "filled_now": "All filled in. Send the application form for {title} once you have looked it over.",
    "held": "The application form for {title} could not be sent by itself: {why}",
    "unsure": "The {title} form was sent, but the site did not say it was received, so it may or may not have gone. Look for a "
              "confirmation email, or the site's list of your applications. If it went, press \"It went through\"; if not, open the "
              "application page and send it yourself. I won't send it again myself, so it is not sent twice.",
    "unsure_sent": "It went through",
    "no_form": "This packet has no application form being filled in",
    "no_field": "The form has no such field; it has: {fields}",
    "not_an_option": "“{label}” can only be one of: {options}",
    "why_captcha": "the site wants a person to pass a check (CAPTCHA), so press send on the page yourself",
    "why_code": "the site sent a code to your mailbox, so enter it on the page and send it yourself",
    "why_invalid": lambda fields: f"the site would not take these fields: {fields}; tell me what to change them to",
    "why_unreachable": "the page would not open or was not what was expected, so fill it in on the page yourself",
    "site": "Filled in on {site}",
    "unsupported": "Forms on this site cannot be filled in yet: apply on the page yourself",
}
