# Local dashboard access

The Flask dashboard binds to `127.0.0.1`. Set a strong random `ADMIN_TOKEN` in
the process environment before starting it. With no token configured every
management route fails closed. Do not put the token in a URL or source control.

The browser prompts for HTTP Basic authentication: username `admin`, password
equal to ADMIN_TOKEN. Keep this loopback-only, or use an authenticated HTTPS
reverse proxy if remote administration is required. HTTP Basic is not encryption.
Programmatic local clients can send `X-Admin-Token` instead. Browser mutations
must send the exact same Origin; an API client may omit Origin only when sending
the explicit header. Do not open this dashboard through the public image tunnel.

`/output_img/<directory>/<filename>` and `/output_video/<directory>/<filename>`
remain public for media fetching. They serve only allowed media types inside
the resolved output directory and reject parent paths, Windows separators,
alternate streams and symlink/junction escapes. Keep secrets out of output.

Telegram commands and approval callbacks require both `TELEGRAM_CHAT_ID` and
`TELEGRAM_ALLOWED_USER_ID` (numeric IDs). Missing either disables inbound
commands. For group chats choose one authorized human user ID, not just the
group ID. Outbound notifications remain addressed to TELEGRAM_CHAT_ID.

Offline verification: `python -m pytest tests -q` with ALGO_ENV=test and explicitly
injected temporary ALGO_DB_PATH/TRACKING_DB_PATH. Tests use synthetic credentials
and fake model responses; never use production tokens or databases.
