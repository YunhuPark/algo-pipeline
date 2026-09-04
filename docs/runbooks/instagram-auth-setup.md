# Instagram Login authentication setup

## Scope and stop conditions

This repository uses **Instagram API with Instagram Login** for publishing. Do not
mix it with the Facebook Login flow or its `instagram_basic` and
`instagram_content_publish` permissions. The publishing scopes used here are:

- `instagram_business_basic`
- `instagram_business_content_publish`

The account must be an Instagram professional Business or Creator account. Meta's
current setup and publishing references are:

- <https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/get-started.md/>
- <https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/business-login.md/>
- <https://developers.facebook.com/documentation/instagram-platform/content-publishing.md/>

The OAuth setup and preflight do not authorize a public image upload, Instagram
publish, or Scheduler activation.

## 1. Rotate previously exposed credentials

An earlier tracked OAuth helper embedded a Meta app secret in repository history and
printed token responses. Treat that app secret and every token derived from it as
exposed even after the helper is fixed.

Before creating a usable token:

1. Rotate the Meta app secret in the Meta developer dashboard.
2. Revoke old Instagram access tokens or remove their authorization.
3. Do not paste the replacement secret or token into chat, screenshots, issues, logs,
   shell history, or Git.

History rewriting is a separate repository-wide operation and is not required to
rotate the live credential.

## 2. Configure the Meta app

Configure Instagram API with Instagram Login and add the exact redirect URI that will
be used for OAuth. Request only the two publishing scopes listed above. Comment and
message management require separate review and are not enabled by this setup.

Put the values only in the local ignored `.env` file:

```dotenv
META_APP_ID=<new app id>
META_APP_SECRET=<rotated app secret>
IG_REDIRECT_URI=<exact registered redirect URI>
IG_ACCESS_TOKEN=
IG_USER_ID=
IG_IMAGE_BASE_URL=
```

Confirm `.env` is ignored before continuing:

```powershell
git check-ignore -v .env
```

## 3. Run OAuth setup

Keep the dashboard, Scheduler, login task, and every `main.py` process stopped.

```powershell
python scripts/get_ig_token.py
```

The script opens the Instagram authorization page. After approval, copy the complete
callback URL from the browser address bar and paste it into the hidden prompt. The
callback origin/path and CSRF state must match. The script exchanges and verifies the
token before writing `IG_ACCESS_TOKEN` and `IG_USER_ID` to `.env`; it never prints the
authorization code, app secret, token, or provider response.

## 4. Configure image delivery and run read-only preflight

Set `IG_IMAGE_BASE_URL` to an operator-controlled public HTTPS base URL. The value
`catbox` is allowed only after separate approval because it publicly uploads every
generated image to a third party.

```powershell
python scripts/ig_preflight.py
```

This command performs a read-only `/me` request and checks that the token account ID
matches `IG_USER_ID`. It must report success without changing `data/algo.db`, uploading
media, creating a container, or publishing a post.

If it reports `TOKEN_INVALID`, `TOKEN_EXPIRED`, `PERMISSION_DENIED`,
`ACCOUNT_MISMATCH`, or a network error, stop. Do not dequeue the pending row and do not
clear publish-attempt fields.

## 5. Token refresh

Refresh is an explicit maintenance command, not a Scheduler task:

```powershell
python scripts/refresh_ig_token.py
```

It verifies the refreshed token against the configured account before replacing the
local ignored `.env` value. It does not print tokens or provider responses. Run the
read-only preflight again afterward.
