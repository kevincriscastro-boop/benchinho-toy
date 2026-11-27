"""Gera icone.ico / icone.png do Monitor de Jogos (velocímetro neon na paleta do overlay)."""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

PASTA = Path(__file__).resolve().parent
S = 1024  # desenha grande e reduz (bordas suaves)
FUNDO, BORDA = (11, 18, 32), (34, 48, 77)
CIANO, VIOLETA, ROSA, TRILHO = (34, 211, 238), (167, 139, 250), (251, 113, 133), (27, 39, 66)


def mistura(a, b, f):
    return tuple(int(x + (y - x) * f) for x, y in zip(a, b))


def gerar():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((24, 24, S - 24, S - 24), radius=220, fill=FUNDO, outline=BORDA, width=14)

    cx, cy, r, larg = S / 2, S * 0.56, S * 0.33, 70
    ini, fim = 150, 390  # arco de 240°, aberto embaixo
    caixa = (cx - r, cy - r, cx + r, cy + r)
    d.arc(caixa, ini, fim, fill=TRILHO, width=larg)

    # brilho: arco colorido borrado por baixo
    brilho = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    db = ImageDraw.Draw(brilho)
    alvo = 330  # até onde o ponteiro chega
    passos = 120
    for i in range(passos):
        a0 = ini + (alvo - ini) * i / passos
        a1 = ini + (alvo - ini) * (i + 1) / passos + 0.6
        cor = mistura(CIANO, VIOLETA, i / (passos - 1))
        db.arc(caixa, a0, a1, fill=cor + (255,), width=larg)
    img.alpha_composite(brilho.filter(ImageFilter.GaussianBlur(28)))
    img.alpha_composite(brilho)

    # ponteiro
    ang = math.radians(alvo)
    px, py = cx + (r - 30) * math.cos(ang), cy + (r - 30) * math.sin(ang)
    d = ImageDraw.Draw(img)
    d.line((cx, cy, px, py), fill=(226, 232, 240), width=34)
    d.ellipse((cx - 52, cy - 52, cx + 52, cy + 52), fill=(226, 232, 240))
    d.ellipse((cx - 22, cy - 22, cx + 22, cy + 22), fill=ROSA)

    # risquinho de "frametime" embaixo
    base = S * 0.83
    pts = []
    for i in range(13):
        x = S * 0.30 + S * 0.40 * i / 12
        y = base - (S * 0.05 if i == 8 else 0)
        pts.append((x, y))
    d.line(pts, fill=CIANO, width=22, joint="curve")

    img.save(PASTA / "icone.png")
    img.save(PASTA / "icone.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    gerar()
    print("ok")
