# The fonts in this folder

They are **self-hosted on purpose**: the site's own Content-Security-Policy says
`font-src 'self'`, and a share page must fetch nothing at all from a third
party. Loading them from a font CDN would tell that CDN who is looking at which
family's photographs, and when.

All three families are published under the **SIL Open Font License 1.1**, which
allows bundling and redistribution — including inside this AGPL project —
as long as the licence travels with them and they are not sold on their own.

| Font | Used for | Author / foundry | Licence |
|---|---|---|---|
| **Bodoni Moda** | headings, album titles | indestructible type* | [OFL 1.1](https://github.com/indestructible-type/Bodoni/blob/master/OFL.txt) |
| **Spectral** | running text | Production Type, for Google | [OFL 1.1](https://github.com/productiontype/Spectral/blob/master/OFL.txt) |
| **IBM Plex Mono** | metadata, numbers, paths | Mike Abbink / Bold Monday, for IBM | [OFL 1.1](https://github.com/IBM/plex/blob/master/LICENSE.txt) |

The files here are `woff2` subsets of the upstream releases. The full licence
text of the OFL 1.1 is at <https://openfontlicense.org>.

⚠ The OFL forbids using a **reserved font name** for a modified version. If you
subset or otherwise change one of these, that is fine — but do not ship the
result under the same name.
