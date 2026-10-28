#!/usr/bin/env python3
"""Make a public demo copy of an AdAgent HTML report.

Usage: python3 make_demo_report.py report.html [-o out.html] [--photos blur|placeholder]
                                   [--blur-thumb 3] [--blur-card 5]
"""
import argparse, re, sys

ap = argparse.ArgumentParser()
ap.add_argument("src")
ap.add_argument("-o", "--out")
ap.add_argument("--photos", choices=["blur", "placeholder"], default="blur")
ap.add_argument("--blur-thumb", type=int, default=3, help="px, leaderboard thumbnails")
ap.add_argument("--blur-card", type=int, default=5, help="px, detail-card photos")
a = ap.parse_args()
out = a.out or re.sub(r"\.html$", "", a.src) + "-demo.html"

html = open(a.src, encoding="utf-8").read()

# 1. strip raw scraped data (seller names, e-mails, phones, VINs)
html, n_raw = re.subn(
    r'<details class="raw">\s*<summary>[^<]*</summary>.*?</details>', "", html, flags=re.S)

# 2. photos
if a.photos == "placeholder":
    html, n_img = re.subn(r'<img src="https://[^"]*"[^>]*>',
                          '<span class="ph" aria-hidden="true">🚗</span>', html)
    css = (".ph{display:flex;width:100%;height:100%;align-items:center;justify-content:center;"
           "background:linear-gradient(135deg,#e2e8f0,#cbd5e1);font-size:1.5rem}\n")
    note = "Zdjęcia i dane kontaktowe sprzedających zostały usunięte"
else:
    n_img = html.count("<img")
    css = (f":root{{--blur-thumb:{a.blur_thumb}px;--blur-card:{a.blur_card}px}}\n"
           ".lb-thumb img{filter:blur(var(--blur-thumb));transform:scale(1.1)}\n"
           ".ad-thumb img{filter:blur(var(--blur-card));transform:scale(1.1)}\n")
    note = "Zdjęcia są rozmyte, a dane kontaktowe sprzedających usunięte"
html = html.replace("</style>", css + "</style>", 1)

# 3. banner + title
banner = ('<div class="freemium-banner" style="background:var(--blue-bg);border-color:#93c5fd">'
          f'<p style="color:#1e3a5f">Przykładowy raport demonstracyjny. {note}; '
          'kliknij miniaturę lub tytuł, aby zobaczyć oryginalne ogłoszenie.</p></div>\n')
html = html.replace("<!-- Freemium banner -->", banner, 1)
html = re.sub(r"<title>.*?</title>", "<title>Raport AdAgent — przykład</title>", html, count=1)

# 4. leak check
for pat in [r"Wyświetl numer", r"[\w.]+@[\w.]+\.\w+", r"\bVIN:"]:
    if re.search(pat, html):
        print(f"WARNING: pattern still present: {pat}", file=sys.stderr)

open(out, "w", encoding="utf-8").write(html)
print(f"{out}: removed {n_raw} raw sections, {n_img} photos ({a.photos})")
