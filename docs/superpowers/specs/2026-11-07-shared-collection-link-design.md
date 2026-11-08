# Shared collection link: design

Date: 2026-11-07
Status: draft for review. Nothing here is built.

## Intent

Rose wants friends and her playgroup to look at her collection. A friend opens a link she sends them and
browses it. They use it for three things: picking minis to borrow, planning games against her, and
looking at her minis.

This replaces the broader "phase 2" in `handoff/requirements.md` (visibility levels, public front page,
public API) with one small feature: a private link to a read-only copy of one user's collection.

## Goals

- A signed-in user can create a share link for their own collection.
- Anyone with the link can open it without an account and browse that collection.
- The shared view is the normal collection view with every edit control removed. Search, filters,
  company view and the unit detail panel all work.
- Saved forces appear in the shared view too.
- The owner can turn the link off, and can create a new one afterward.
- A visitor can build a force from the shared minis in the page. What they build does not change the
  owner's data.

## Non-goals

- No visibility levels. A collection is either unshared or shared by link.
- No public front page and no list of other people's catalogs.
- No public API beyond what the shared page itself needs.
- No hiding of individual fields. Notes, tags, painted status and photos are all shown.
- No accounts for visitors, and no way for a visitor to save anything to the server.

## Anti-goals

Any of these makes the feature a failure even if the page loads.

- A visitor changes or deletes something. The server must refuse every write on the shared routes. Hiding
  the buttons is not enough.
- The owner's own use gets worse: slower, or changed in behavior, while nobody is visiting.
- Local mode breaks. `python3 -m batchall serve` on a single-user database keeps working as it does
  today, with no login.

Rose said a leaked link is not something she would mind, so link secrecy is a convenience here and not
a security boundary. The off switch is still included (see constraints).

## Constraints

- Sharing exists in hosted (multi-user) mode only. Local mode has no accounts, so there is nothing to
  share from. It is unchanged.
- `batchall/` and `tests/` stay standard library only. Hosted code lives in `hosted/` (Django).
- Each user's catalog is its own SQLite file. A link resolves to one user's file and reads from it.
- Schema changes are additive and run on start, as in the existing migrations.
- Existing tests keep passing. New behavior gets new tests.
- Code, comments, commit messages and docs stay plain and professional.

## Decisions

Made by Rose:

- The audience is friends and the playgroup, not the public.
- Visitors reach the collection by link, with no account.
- The shared view is the owner's normal view minus the edit buttons (the "A" mockup).
- Nothing is hidden from visitors.
- Saved forces are visible on the shared view. (Offered by the assistant as the default, not objected to.)

Calls made by the assistant while writing this. Rose has not confirmed them separately.

- **Off by default.** A collection has no link until the owner creates one.
- **One active link per user.** Creating a new link replaces the old one, which stops working.
- **Unguessable token in the path.** The token is long and random, generated with `secrets`, and stored
  in the accounts database. Compare it in constant time.
- **No visitor persistence.** A force a visitor builds lives in the page only. Nothing is written
  server-side.
- **Invalid, revoked and replaced links look the same.** All three return the same 404 with no detail.
- **Identity shown.** The shared page shows the owner's handle as a title. It exposes no email, account
  id, file path or admin data.
- **Read-only access.** Shared routes open the owner's SQLite file read-only (`mode=ro`) and never write
  to it. "No worse for the owner" means no added writes or locks on the owner's own path. There is no
  numeric speed target.

## Facts about the current code

Checked on 2026-11-07 against `main` at `498631c`.

- **Force building is a server call.** The UI sends `POST /api/build` (`web/app.js`, `post_build` in
  `batchall/server.py`). `batchall/builder.py` contains no INSERT, UPDATE, DELETE or commit, so the call
  stores nothing. The shared routes allow this one POST and refuse every other write. It must stay
  stateless.
- **Saving a force is separate.** `POST` and `PUT /api/forces` are the calls that persist. The shared
  routes refuse them, and the shared UI does not offer them.
- **Photos are served by `GET /api/minis/<id>/photo`.** The shared routes must serve these under the
  token, scoped to the owner's minis, or photos break for visitors.
- **The web UI is one file.** `web/app.js` is about 2,500 lines and sends edits through its `api()`
  helper. Server-side refusal is the safety net. The UI change is a convenience.
- **Journal mode.** The shared catalog database uses WAL (`batchall/db.py`). Confirm the per-user files
  do too before relying on concurrent reads while the owner writes.

## Left to the builder

- URL shape for the shared page and its data routes.
- How the existing `web/` UI is reused in a read-only mode, and how it learns it is read-only.
- Whether the shared routes call the existing adapter in `hosted/api.py` with writes disabled, or are a
  separate small set of GET routes. The anti-goal above is the requirement.
- Where the owner finds the create, copy and turn-off controls in the UI.
- Caching, rate limiting and headers for the shared routes, consistent with the existing middleware.
- Test layout.

## Open questions

None outstanding. The builder check raised five questions. Four were answered from the code or settled as
calls above. The fifth (how the UI hides edit controls) is left to the builder.
