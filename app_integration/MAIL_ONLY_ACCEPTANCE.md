# Local Apple Mail migration — implementation boundary

This candidate replaces the runtime Gmail-API dependency with the user's existing
Apple Mail receiver. It is an in-place Account Center update, not another application.
The builder targets 2.0.10 / Build 20016; this version is NOT yet claimed installed.

## Implemented

- LocalMailReader uses the system Mail.sdef / generated Scripting Bridge interface.
  It binds to an already-running Mail PID and uses data operations only. No AppleScript,
  System Events, UI scraping, direct database reading, passwords or Mail token access.
- One explicitly selected receiving account/inbox; only recent GLaDOS code subjects
  are considered. Raw source is fetched only after account, subject, time and ID match.
  It is returned through an anonymous process pipe and never saved as plaintext files.
- Normal macOS Mail automation consent is requested only by an explicit setup action.
  Background reads cannot prompt, activate, start, quit or otherwise operate the GUI.
- The existing original recipient, sender authentication, baseline IDs and original
  timestamp/expiry validation remain in use. New forwarding arrival does not revive
  an expired code. Codes retain leading zeroes and ambiguous candidates stop.
- Before requesting a new code, the runner checks the local receiving account and
  requests Mail to fetch it and the original account when that account exists locally.
  Polling can request new mail at bounded intervals even if Mail is set to manual fetch.
- The maintenance UI removes Google client JSON selection, Google consent links and
  API authorization prerequisites. It instead shows Connection to Local Mail and
  Check Mail. The GitHub account/policy and protected credential publication paths
  remain in the same application.
- Runner acceptance version is 3, so a different prior receipt path does not silently
  satisfy this mode's real-account acceptance. No monthly task is enabled by building.

## Verification and outstanding work

The session's LocalAnt action was unavailable and DevSpace repeatedly returned
Connection failed, including when reusing the last known workspace. The new files
were therefore prepared and tested outside the user's Mac and committed through
the existing GitHub connection. No local app, Mail permission, code, Cookie, Secret,
mailbox contents, existing rule or schedule was modified in this session.

Use the associated Mail-only integration checks run to verify native compilation on
an isolated macOS runner. That compilation is NOT a test of the user's Mail account,
TCC permission, forwarding rule, fresh login, website challenge or cloud publication.
All mailbox tests use synthetic data. The updated app cannot be marked DONE until it
is installed on the user's Mac and the actual requested end-to-end acceptance passes.

Remaining live gates: restore the local tunnel, build against that Mac's Mail.sdef,
install/sign the same app, connect the selected Mail account using normal system
consent, verify a new code and exact authenticated account identity, then verify the
existing Secret's update through a new read-only cloud job. A human challenge still
requires a human response; never change identity/domain to evade it.

The current native component does not start a closed Mail app. Cold-start remains a
separate DCF-background integration requirement and must not be marked complete.
Existing per-account email mappings and unknown mappings remain unchanged.

Google OAuth source remains only as retained development compatibility for the
Keychain wrapper and offline tests; this new live receipt/authorization UI does not
invoke Google OAuth or require a client JSON. No imported Google credential was read.
