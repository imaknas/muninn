# Security

## Reporting a vulnerability

Please report it privately, through GitHub's "Report a vulnerability" (the repository's Security tab), not in a public
issue. Say what an attacker can do, and how to reproduce it. A fix for a confirmed problem goes into the next release,
and the advisory names you unless you would rather it didn't.

Only the latest release is supported.

## What Muninn protects, and how

Muninn runs on its owner's own machine for that one person. It holds their plans, mail, calendar, location and what
they said to it, and it can act for them, so the rules below are the security boundary. A way around any of them is
a vulnerability.

- **Who can reach it.** The server listens on 127.0.0.1 unless told otherwise; the intended way in from a phone is a
  private network such as Tailscale, over HTTPS. Everything it shows or does needs a paired device: a one-time code, then
  an HttpOnly cookie. Devices can be listed and revoked (`muninn devices`), and access can be limited further to listed
  Tailscale logins. The setup page's one-time link works only while no device is paired. Data sent in by other
  programs (activity, location) needs the ingest token.
- **Secrets.** Keys, tokens and the push key live in the data directory's `secrets/`, readable by the owner only, and
  are not kept in the config or the database. A missing or rejected key stops what needs it and says
  so; no other credential or model is used in its place.
- **What leaves the machine.** One router is the only path to a model, and the privacy routing (README.md, "Privacy
  model") decides what may go to a cloud model; known credential formats are masked before anything is sent to one.
  Push notifications are encrypted end to end, and can be set to say only counts.
- **Acting for the user.** Everything Muninn does on its own is logged with an undo. What cannot be taken back once
  out (sending an application form) is asked first and goes out only on the user's yes. Muninn never sends mail,
  answers someone else's invitation or pays; it does not contact anyone on the user's behalf, and it never answers a
  check meant for a person (a CAPTCHA, a code sent to the user's mail).
- **Other programs.** Project sessions that Muninn lends a conversation to ask before doing anything that reaches
  outside their project; what they say is kept apart from what the user said.

## Out of scope

- Someone who already has the owner's account on the machine, or a paired, unlocked phone.
- The model providers' own handling of what they are sent (choose them in `llm.providers`; with privacy routing,
  sensitive material stays with a local model).
- Denial of service against a server that only its owner can reach.
