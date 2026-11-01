# D1 checklist

Steps only the owner can do, in order. Each one says how to check it worked.

## 1. Server: security baseline (before anything else)

Once every source is on, the server holds the most sensitive data you have.

- [ ] **FileVault is on.** `fdesetup status` → `FileVault is On.`
- [ ] **`data/` is not in any plaintext cloud backup.** If the runtime
      directory is backed up at all, the backup is encrypted (e.g. an encrypted
      Time Machine disk or a restic repository with a passphrase). Check:
      `ls -ld ~/services/muninn/data` shows `drwx------`.
- [ ] **Tailnet ACL lets only your own devices reach Muninn.** In the Tailscale
      admin console → Access controls, allow port 8444 on the server only from
      your user (or your phone and laptop by tag), e.g.:

      ```json
      {"action": "accept", "src": ["you@example.com"], "dst": ["<host>:8444"]}
      ```

      Check from a device that should be refused: the page does not load.
- [ ] **Optional extra lock:** list your Tailscale login in
      `data/config.yaml` → `allowed_tailscale_logins: [you@example.com]`. Then
      requests without that identity are refused even with a paired device.

## 2. Server: server and keys

- [ ] `data/config.yaml` has `public_url: https://<host>.<tailnet>.ts.net:8444`
      (see `examples/config.yaml`). It is also the VAPID subject Apple checks.
- [ ] `.venv/bin/muninn init` in `~/services/muninn` → VAPID key and ingest
      token exist in `data/secrets/` (mode 600).
- [ ] The server's launchd job is loaded; `curl -s 127.0.0.1:18795/healthz` → `{"ok":true}`.
- [ ] `tailscale serve status` shows `:8444 → http://127.0.0.1:18795` (tailnet only, not Funnel).

## 3. iPhone

- [ ] **Tailscale always on**: Tailscale app → Settings → VPN On Demand
      (or iOS Settings → VPN → Tailscale → Connect On Demand) so the phone
      can reach the server whenever Muninn's page or a push needs it.
- [ ] Open `https://<host>.<tailnet>.ts.net:8444/` in **Safari**.
- [ ] Share → **Add to Home Screen**. Open Muninn **from the home screen icon**
      from now on (iOS only allows push for home-screen web apps, iOS ≥ 16.4,
      and the home-screen app keeps its own login separate from Safari).
- [ ] On the server: `.venv/bin/muninn pair --name iPhone` → enter the 8-character
      code in the app within 10 minutes.
- [ ] The **我** tab → **開啟通知** → Allow.
- [ ] The **我** tab → **送一則測試通知** → a "muninn: 1 new reminder" notification arrives.
- [ ] iOS Settings → Notifications → Muninn: allow on Lock Screen; Time
      Sensitive is not needed.

## 4. First day

- [ ] Answer the "which domain does this repo belong to" questions on the home
      page (管家). Answers become rules, and the **我** tab lists them to change.
      Unanswered repos stay sensitive: with routing "privacy" only the local
      model classifies them.
- [ ] 08:30 brief and 22:30 summary arrive as notifications; open them from the inbox.
- [ ] When you apply somewhere, say so (「我投了 Acme」) or change it on the desktop's 投遞 page; either comes back with an undo.

To remove a lost device: `.venv/bin/muninn devices`, then `--revoke <id>`.
