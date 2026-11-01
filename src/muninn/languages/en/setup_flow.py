# the setup page, as the server says it (setup_flow.py)
TEXTS = {
    "no_answer": "\"{model}\" did not answer a plain question; pick another model (it may be one that cannot chat, such as an embedding model, or one this key may not use): {why}",
    "no_folder": "\"{path}\" is not a folder on this computer",
    "google_needs_https": "Google sends you back to Muninn's HTTPS address after you sign in, and Muninn has none yet: pair a phone first (Settings, Pair a phone), which gives it one",
    "google_not_json": "That is not the file Google gives you: download the client's JSON from Google Cloud and choose that file",
    "google_desktop": "That client is a Desktop app: create one of type \"Web application\" instead",
    "google_not_client": "That file has no Web application client in it (client_id and client_secret): download the client's JSON again",
    "google_redirect": "Add this to the client's Authorized redirect URIs in Google Cloud, save, and download the JSON again: {uri}",
    "no_list": "The service did not answer, so its models cannot be listed; check the address and your connection, or try again in a minute: {why}",
}
