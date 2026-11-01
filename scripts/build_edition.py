"""
Transforme un PDF d'édition en images pour le composant EditionBook.

    python scripts/build_edition.py "a-convertir/Mon livre.pdf"

Pour appliquer la limite HD et le filigrane aux éditions déjà converties
(sans les PDF) :

    python scripts/build_edition.py --appliquer-existant

Options :
    --filigrane "texte"   texte du filigrane (par défaut : FILIGRANE ci-dessous)
    --filigrane-mode fond filigrane en fond, répété en diagonale (par défaut : coin)
    --sans-filigrane      aucune marque
    --filigrane-std       marque aussi les pages std (par défaut : HD seulement)

Résultat dans editions/<nom-du-livre>/ :
    std/p-001.webp …   pages intérieures, 800 px de large (lecture)
    hd/p-001.webp …    pages intérieures, 1600 px de large max et 2400 px de
                       haut max (zoom), avec filigrane
    couverture.webp    première page, à uploader dans Framer
    dos.webp           dernière page, à uploader dans Framer
    infos.txt          les valeurs à copier dans Framer

Le PDF peut être exporté page par page ou en planches (doubles pages) :
toute page plus large que haute est coupée en deux au milieu.
"""

import argparse
import re
import shutil
import unicodedata
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

STD_W = 800
HD_W = 1600          # largeur max des pages HD
HD_MAX_H = 2400      # hauteur max des pages HD : lisible et zoomable, pas imprimable
COVER_MAX_H = 1600   # couverture et dos : assez pour Framer, pas plus
SITE = "https://lauriemalassenet.github.io/editions"
FILIGRANE = "© Laurie Malassenet"

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Ubuntu (GitHub Actions)
    "/System/Library/Fonts/Helvetica.ttc",                # macOS
    "C:/Windows/Fonts/arial.ttf",                         # Windows
]


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "edition"


def load_font(size: int) -> ImageFont.ImageFont:
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size=size)


def fit(w: int, h: int, max_w: int, max_h: int) -> tuple[int, int]:
    """Taille réduite pour tenir dans max_w x max_h, sans jamais agrandir."""
    s = min(max_w / w, max_h / h, 1.0)
    return round(w * s), round(h * s)


def watermark(im: Image.Image, text: str, mode: str) -> Image.Image:
    """Ajoute un filigrane discret : petit texte en bas à droite, ou motif de fond."""
    base = im.convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    W, H = base.size

    if mode == "fond":
        # texte répété sur une grande toile, tournée de 30° puis recadrée à la page
        font = load_font(max(14, W // 28))
        D = int((W ** 2 + H ** 2) ** 0.5)
        big = Image.new("RGBA", (D, D), (0, 0, 0, 0))
        d = ImageDraw.Draw(big)
        step_x, step_y = int(W * 0.6), int(W * 0.3)
        for row, y in enumerate(range(0, D, step_y)):
            for x in range(-(row % 2) * step_x // 2, D, step_x):
                d.text((x, y), text, font=font, fill=(128, 128, 128, 28))
        big = big.rotate(30, resample=Image.BICUBIC)
        layer = big.crop(((D - W) // 2, (D - H) // 2, (D - W) // 2 + W, (D - H) // 2 + H))
    else:  # coin
        font = load_font(max(12, W // 55))
        m = max(10, W // 50)
        ImageDraw.Draw(layer).text((W - m, H - m), text, font=font, anchor="rd",
                                   fill=(255, 255, 255, 150),
                                   stroke_width=max(1, W // 800), stroke_fill=(0, 0, 0, 90))

    return Image.alpha_composite(base, layer).convert("RGB")


MARQUEUR = ".protege"  # fichier témoin : évite de filigraner deux fois


def apply_existing(root: Path, mark: str | None, mode: str, mark_std: bool) -> None:
    """Plafonne et filigrane les éditions déjà présentes dans le dépôt."""
    dossiers = sorted({d.parent for d in root.rglob("hd") if d.is_dir() and ".git" not in d.parts})
    for d in dossiers:
        if (d / MARQUEUR).exists():
            print(f"{d.relative_to(root)} : déjà traité, ignoré")
            continue
        n = 0
        for f in sorted((d / "hd").glob("*.webp")):
            im = Image.open(f).convert("RGB")
            size = fit(*im.size, HD_W, HD_MAX_H)
            if size != im.size:
                im = im.resize(size, Image.LANCZOS)
            if mark:
                im = watermark(im, mark, mode)
            im.save(f, "WEBP", quality=85, method=5)
            n += 1
        if mark and mark_std:
            for f in sorted((d / "std").glob("*.webp")):
                watermark(Image.open(f).convert("RGB"), mark, mode).save(f, "WEBP", quality=80, method=5)
        for name in ("couverture.webp", "dos.webp"):
            f = d / name
            if f.exists():
                im = Image.open(f).convert("RGB")
                size = fit(*im.size, HD_W, COVER_MAX_H)
                if size != im.size:
                    im.resize(size, Image.LANCZOS).save(f, "WEBP", quality=88, method=5)
        (d / MARQUEUR).write_text(f"filigrane={mark or ''} mode={mode}\n", encoding="utf-8")
        print(f"{d.relative_to(root)} : {n} pages HD traitées")


def render_pages(pdf: Path) -> list[Image.Image]:
    doc = fitz.open(pdf)
    # Si le PDF a été exporté avec fonds perdus / marge, on recadre au format fini
    # (zone de rognage, TrimBox) : la page fait alors exactement sa taille réelle.
    for p in doc:
        tb = p.trimbox
        if tb.width < p.rect.width - 0.5 or tb.height < p.rect.height - 0.5:
            p.set_cropbox(tb)
    # Une page est une planche (double page) si elle est nettement plus large que
    # la page la plus étroite du document (ex. couverture seule + planches), ou,
    # quand toutes les pages ont la même largeur, si elle est à l'italienne.
    widths = [p.rect.width for p in doc]
    min_w = min(widths)
    mixed = max(widths) > 1.4 * min_w

    def is_spread(w: float, h: float) -> bool:
        return w > 1.4 * min_w if mixed else w > h

    singles = [p.rect.width / 2 if is_spread(p.rect.width, p.rect.height) else p.rect.width for p in doc]
    single_w = min(singles)
    zoom = HD_W / single_w

    pages: list[Image.Image] = []
    for p in doc:
        spread = is_spread(p.rect.width, p.rect.height)
        pix = p.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        if spread:  # planche : on coupe au pli
            half = im.width // 2
            pages += [im.crop((0, 0, half, im.height)), im.crop((half, 0, im.width, im.height))]
        else:
            pages.append(im)
    return pages


def build(pdf: Path, root: Path, mark: str | None, mode: str, mark_std: bool) -> str:
    slug = slugify(pdf.stem)
    out = root / "editions" / slug
    if out.exists():
        shutil.rmtree(out)  # repart de zéro : pas de vieilles pages qui traînent
    (out / "std").mkdir(parents=True)
    (out / "hd").mkdir(parents=True)

    pages = render_pages(pdf)
    if len(pages) < 3:
        raise SystemExit(f"{pdf.name} : il faut au moins une couverture, une page et un dos.")

    W, H = pages[0].size
    pages = [p if p.size == (W, H) else p.resize((W, H), Image.LANCZOS) for p in pages]
    hd_size = fit(W, H, HD_W, HD_MAX_H)
    cover_size = fit(W, H, HD_W, COVER_MAX_H)
    std_size = (STD_W, round(STD_W * H / W))

    cover, inside, back = pages[0], pages[1:-1], pages[-1]
    cover.resize(cover_size, Image.LANCZOS).save(out / "couverture.webp", "WEBP", quality=88, method=5)
    back.resize(cover_size, Image.LANCZOS).save(out / "dos.webp", "WEBP", quality=88, method=5)
    for i, p in enumerate(inside, 1):
        hd = p.resize(hd_size, Image.LANCZOS) if hd_size != p.size else p
        std = p.resize(std_size, Image.LANCZOS)
        if mark:
            hd = watermark(hd, mark, mode)
            if mark_std:
                std = watermark(std, mark, mode)
        hd.save(out / "hd" / f"p-{i:03d}.webp", "WEBP", quality=85, method=5)
        std.save(out / "std" / f"p-{i:03d}.webp", "WEBP", quality=80, method=5)

    ratio = round(W / H, 3)
    base = f"{SITE}/editions/{slug}"
    infos = f"""Édition : {pdf.stem}
Dossier : editions/{slug}/
HD : {hd_size[0]} x {hd_size[1]} px{f' · filigrane « {mark} » ({mode})' if mark else ' · sans filigrane'}

Valeurs à mettre dans le composant EditionBook (Framer) :
  Nb de pages     {len(inside)}
  Mise en page    Planches
  Pages (URL)     {base}/std/p-{{nnn}}.webp
  Pages HD (URL)  {base}/hd/p-{{nnn}}.webp
  Ratio page      {ratio}
  Taille conseillée du composant : 340 x {round(340 / ratio)} px

Couverture et dos à télécharger puis uploader dans Framer :
  {base}/couverture.webp
  {base}/dos.webp
"""
    (out / "infos.txt").write_text(infos, encoding="utf-8")
    (out / MARQUEUR).write_text(f"filigrane={mark or ''} mode={mode}\n", encoding="utf-8")
    return infos


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="PDF d'édition → images EditionBook")
    ap.add_argument("pdfs", nargs="*", type=Path, help="fichier(s) PDF")
    ap.add_argument("--appliquer-existant", action="store_true",
                    help="plafonner et filigraner les éditions déjà converties")
    ap.add_argument("--filigrane", default=FILIGRANE, help="texte du filigrane")
    ap.add_argument("--filigrane-mode", choices=["coin", "fond"], default="coin")
    ap.add_argument("--sans-filigrane", action="store_true")
    ap.add_argument("--filigrane-std", action="store_true", help="marquer aussi les pages std")
    a = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    mark = None if a.sans_filigrane else a.filigrane
    if a.appliquer_existant:
        apply_existing(root, mark, a.filigrane_mode, a.filigrane_std)
    elif not a.pdfs:
        ap.error("indique un PDF, ou --appliquer-existant")
    for f in a.pdfs:
        print(build(f, root, mark, a.filigrane_mode, a.filigrane_std))
