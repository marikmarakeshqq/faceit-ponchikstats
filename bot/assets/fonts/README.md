# Custom Fonts For Match Cards

Put your project fonts in this folder to make card rendering identical on Windows and Linux.

Recommended file names:

- `Card-Regular.ttf`
- `Card-Bold.ttf`

How loading works (priority order):

1. Paths from env vars: `CARD_FONT_REGULAR_PATH`, `CARD_FONT_BOLD_PATH`
2. Fonts from this folder (`Card-Regular.ttf`, `Card-Bold.ttf`, etc.)
3. System fallback fonts (DejaVu/Liberation/Noto/Segoe/Arial)
4. Pillow default bitmap font (last resort, low quality)

If you commit your font files to this folder, both local Windows and Linux server will use the same font.
