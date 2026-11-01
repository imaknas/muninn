# what Muninn's entry point says: settings that cannot work, and what the command line prints (main.py)
TEXTS = {
    "no_git": "This install has no git, so it can't tell which version is running",
    "cloud_without_cloud": "llm.routing is cloud, but no cloud model is set up: add a provider under llm.providers (or llm.cloud), "
                           "or set routing back to privacy",
    "privacy_without_local": "Privacy mode (llm.routing: privacy) needs a local model to read what must stay on this machine, but none is "
                             "set up (a provider with location: local under llm.providers, or llm.local_model): set up a local model, "
                             "or change llm.routing to cloud (everything goes to the cloud)",
    "logins_need_serve": "allowed_tailscale_logins only works when tailscale serve forwards to Muninn; host is {host}, so other "
                         "machines can connect directly and fill in that header themselves. Set host back to 127.0.0.1 and put "
                         "tailscale serve in front.",
    "handoff_item": " → {effect} \"{item}\"",
    # features this installation does without (features_off): the feature, then why and what turns it on
    "off_understanding": "Understanding what you say (anything you say is kept as a note; only rule reminders run)",
    "why_no_model": "No model is set up: choose one on the setup page that `muninn serve` prints, or under llm.providers in config.yaml",
    "off_deep_talks": "Deep talks with projects (sandboxes, permission cards, asking a project, promoting an idea to a repo)",
    "off_calendar": "Calendar",
    "off_mail": "Mail",
    "off_reflection": "Weekly reflection",
    "off_form_filling": "Filling in application forms",
    "off_git_activity": "Activity from your project folders",
    "off_session_activity": "Activity from coding-agent sessions",
    "why_no_runner": "No session runner is connected (lease_runner_url and lease_runner_token_file)",
    "why_no_google": "The Google client isn't set up (secrets/google_client.json, and public_url for its sign-in callback)",
    "why_no_mail_accounts": "No mail account is listed (mail_accounts)",
    "why_no_mail_client": "mail_accounts lists accounts, but their client isn't set up ({clients}, and public_url)",
    "why_reflect_false": "Turned off (reflect: false)",
    "why_reflect_privacy": "In privacy mode only a local model may read your conversations, and none is set up",
    "why_no_browser": "No browser to fill them in: install the autofill extra and run playwright install chromium; packets are applied to by hand",
    "why_no_workspace_roots": "No project folders are listed (workspace_roots)",
    "why_no_transcript_roots": "No session transcript folders are listed (transcript_roots)",
    "budget_not_a_sum": "budget.daily_usd is {limit}: it is a sum in US dollars above 0 (or leave it out for no limit)",
    "budget_uncounted": "budget.daily_usd is set, but {model} doesn't say how many tokens its replies take, so what it costs can't be "
                        "counted toward the limit: give that work a model that does (an API provider), or leave the limit out. "
                        "Until then the commands that ask models don't run",
    "budget_unpriced": "budget.daily_usd is set, but {model} has no price, so what it costs can't be counted: add {where} "
                       "as {{input: <dollars>, output: <dollars>}} per million tokens. Until then the commands that ask models don't run",
}
