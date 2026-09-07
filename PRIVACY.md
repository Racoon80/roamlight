# Privacy

**Roamlight has no servers.** There is no Roamlight company holding your
photographs, no account with us, and nothing to sign up for. The app talks to
one machine: the one whoever set it up is running — at home, in a cupboard, on
a rented box. Where that is, and who may see what, is their decision and not
ours.

This page says what the app does with what it touches. It is short because
there is not much.

## What the app keeps on your phone

| | |
|---|---|
| **The address of your site** | Typed by you, or read out of the QR code you scanned. |
| **A device token** | What signs you in. Minted by your site, never seen by anybody else. |
| **Photographs, briefly** | Only in memory, only what is on screen, thrown away when the phone needs the room. |

⚠ Nothing here is backed up to iCloud or to Google, and nothing travels to a
new phone. That is deliberate: a token that quietly moved would make the list
of devices on your site a lie. On a new phone you sign in again.

⚠ Signing the device out deletes the token and empties the pictures out of
memory. Whoever runs the site can also take the device off the list from there
— which works even when the phone is gone.

## What leaves your phone, and to whom

**To your own site, and nowhere else:** your sign-in, the photographs you look
at, and the photographs you send it. That is the whole point of the app.

**To Apple**, and only if you switch notifications on: an address for this
device, and the one line of any notice — the album's title and a count, such as
*"2026 Ostende — 12 new photographs"*. Never a name, never a place beyond that
album title, never a photograph. Notifications are off until you allow them,
and off entirely unless whoever runs the site has set them up.

**To nobody else.** There is no analytics, no crash reporting, no advertising
identifier, no third-party framework of any kind in this app. The map is drawn
from tiles fetched **through your own site**, so not even OpenStreetMap learns
which part of the world your albums are in.

## What your site keeps

That is up to whoever runs it, and the answer is in the open: Roamlight is
[AGPL-3.0 and the whole of it can be read](https://github.com/Racoon80/roamlight).
In short, it holds your photographs, who is allowed to see which album, the
devices you have signed in, and a log of sign-ins. It sends nothing outward on
its own, except to look up once where a place is, and to fetch map tiles.

## Children

Roamlight is a family photo album. It collects nothing from anybody, of any
age, beyond what is above.

## Asking

The person who runs your site holds your photographs, and is the person to ask
about them. For the software itself, open an issue at
<https://github.com/Racoon80/roamlight>.

*Last changed: 7 September 2026.*
