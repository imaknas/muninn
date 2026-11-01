# Muninn logo

The mark: the head of a raven, Muninn being one of Odin's two ravens (the one for memory), drawn by the traits that
tell a raven from other birds: the heavy, curved beak, the flat forehead running into it, and the shaggy throat
feathers. Ink `#1f2937` with an amber eye `#d97706`; on dark backgrounds `#e5e7eb` with `#fbbf24`. The app's icon
stands on the app's own light background `#F7F7F5`. It is drawn the same way as agent-hub's (flat shapes, the same
ink), so the two read as one family.

| File | Use |
|---|---|
| `mark.svg`, `mark-dark.svg` | the mark alone, transparent, for light / dark backgrounds |
| `wordmark.svg`, `wordmark-dark.svg` | mark + name |
| `banner-en.svg`, `banner-zh.svg` (and `-dark`) | the README's header, with the line under the name in each README's language |
| `icon.svg`, `icon-512.png`, `apple-touch-icon.png` | square app icon, full bleed with the mark inside the maskable safe zone (the app's own icons in `src/muninn/adapters/web/static/` are rendered from it) |
| `icon-rounded.svg` | the icon with rounded corners, for avatars |
| `favicon.ico`, `favicon-16.png`, `favicon-32.png` | browser tabs |
| `social.svg`, `social.png` | GitHub social preview (1280×640; set it in the repository settings) |

The text uses the system sans-serif, so it renders slightly differently per platform. PNGs are rendered from the SVGs
with `rsvg-convert`.
