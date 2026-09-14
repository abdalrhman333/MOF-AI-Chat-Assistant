# Brand font — not in git

The UI uses **DIN Next LT Arabic** (Regular, Medium, Bold). It is a licensed
Monotype font, so the files are deliberately gitignored rather than copied
into source control.

Place these three files here:

```
DINNextLTArabic-Regular.ttf
DINNextLTArabic-Medium.ttf
DINNextLTArabic-Bold.ttf
```

They come from the MOF brand pack (`الخطوط`). Without them the page still
works and falls back to a system font; only the typography differs.
