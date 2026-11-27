"""Gera o ícone do Benchinho Toy.

    python gerar_icone.py            -> gera as 3 opções (icone_A/B/C.png) + previa_icones.png
    python gerar_icone.py B          -> instala a opção B como icone.ico / icone.png
    python gerar_icone.py paletas    -> gera icone_strix.* (D) e icone_tuf.* (A), usados conforme a paleta

Estilo "industrial gamer": preto, amarelo e cantos chanfrados (lembra o visual TUF sem usar o logo).
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

PASTA = Path(__file__).resolve().parent
S = 1024
PRETO, GRAFITE, BORDA = (14, 15, 18), (28, 30, 36), (52, 56, 66)
AMARELO, LARANJA, CIANO, BRANCO, ROSA = (255, 196, 0), (255, 122, 26), (34, 211, 238), (236, 239, 244), (251, 113, 133)


def mistura(a, b, f):
    return tuple(int(x + (y - x) * f) for x, y in zip(a, b))


def hexagono(cx, cy, r, rot=0):
    return [(cx + r * math.cos(math.radians(60 * i + rot)), cy + r * math.sin(math.radians(60 * i + rot))) for i in range(6)]


def chanfrado(x0, y0, x1, y1, c):
    return [(x0 + c, y0), (x1 - c, y0), (x1, y0 + c), (x1, y1 - c), (x1 - c, y1), (x0 + c, y1), (x0, y1 - c), (x0, y0 + c)]


def velocimetro(img, cx, cy, r, larg, cor_ini, cor_fim, alvo=330, brilho=True, trilho=GRAFITE):
    d = ImageDraw.Draw(img)
    caixa = (cx - r, cy - r, cx + r, cy + r)
    d.arc(caixa, 150, 390, fill=trilho, width=larg)
    camada = Image.new("RGBA", img.size, (0, 0, 0, 0))
    dc = ImageDraw.Draw(camada)
    n = 120
    for i in range(n):
        a0 = 150 + (alvo - 150) * i / n
        dc.arc(caixa, a0, a0 + (alvo - 150) / n + 0.6, fill=mistura(cor_ini, cor_fim, i / (n - 1)) + (255,), width=larg)
    if brilho:
        img.alpha_composite(camada.filter(ImageFilter.GaussianBlur(26)))
    img.alpha_composite(camada)
    d = ImageDraw.Draw(img)
    ang = math.radians(alvo)
    d.line((cx, cy, cx + (r - 28) * math.cos(ang), cy + (r - 28) * math.sin(ang)), fill=BRANCO, width=34)
    d.ellipse((cx - 50, cy - 50, cx + 50, cy + 50), fill=BRANCO)
    d.ellipse((cx - 20, cy - 20, cx + 20, cy + 20), fill=PRETO)


def opcao_a():
    """Hexágono preto com borda amarela grossa e velocímetro amarelo→laranja."""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon(hexagono(S / 2, S / 2, 490, 30), fill=AMARELO)
    d.polygon(hexagono(S / 2, S / 2, 430, 30), fill=PRETO)
    d.polygon(hexagono(S / 2, S / 2, 400, 30), outline=BORDA, width=6)
    velocimetro(img, S / 2, S * 0.54, 260, 64, AMARELO, LARANJA)
    return img


def opcao_b():
    """Placa chanfrada com faixas de alerta embaixo e velocímetro amarelo."""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon(chanfrado(30, 30, S - 30, S - 30, 170), fill=PRETO, outline=AMARELO, width=26)
    # faixas zebradas de alerta
    faixa = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    df = ImageDraw.Draw(faixa)
    for x in range(-400, S + 400, 110):
        df.polygon([(x, S * 0.80), (x + 55, S * 0.80), (x + 55 - 120, S * 0.93), (x - 120, S * 0.93)], fill=AMARELO)
    mascara = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mascara).polygon(chanfrado(80, 80, S - 80, S - 80, 130), fill=255)
    faixa.putalpha(Image.composite(faixa.getchannel("A"), Image.new("L", (S, S), 0), mascara))
    img.alpha_composite(faixa)
    velocimetro(img, S / 2, S * 0.50, 250, 62, AMARELO, AMARELO, brilho=True)
    return img


def opcao_c():
    """Escudo angular grafite, ponteiro amarelo e arco ciano→amarelo (mistura com o overlay)."""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    escudo = [(S * 0.5, 40), (S - 70, 170), (S - 70, S * 0.55), (S * 0.5, S - 40), (70, S * 0.55), (70, 170)]
    d.polygon(escudo, fill=AMARELO)
    interno = [(S * 0.5, 100), (S - 125, 210), (S - 125, S * 0.53), (S * 0.5, S - 115), (125, S * 0.53), (125, 210)]
    d.polygon(interno, fill=PRETO)
    velocimetro(img, S / 2, S * 0.50, 230, 58, CIANO, AMARELO)
    # sobrescreve o ponteiro em amarelo
    d = ImageDraw.Draw(img)
    ang = math.radians(330)
    cx, cy = S / 2, S * 0.50
    d.line((cx, cy, cx + 200 * math.cos(ang), cy + 200 * math.sin(ang)), fill=AMARELO, width=34)
    d.ellipse((cx - 48, cy - 48, cx + 48, cy + 48), fill=AMARELO)
    d.ellipse((cx - 18, cy - 18, cx + 18, cy + 18), fill=PRETO)
    return img


VERMELHO, MAGENTA = (255, 40, 70), (139, 92, 246)  # "MAGENTA" virou roxo a pedido do Kevin


def opcao_d():
    """Estilo Strix: placa preta com cortes diagonais, filetes vermelho→magenta e brilho RGB."""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    forma = [(150, 40), (S - 40, 40), (S - 40, S - 260), (S - 260, S - 40), (40, S - 40), (40, 150)]
    # contorno com degradê vermelho -> magenta
    contorno = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for i in range(S):
        gd.line((i, 0, i, S), fill=mistura(VERMELHO, MAGENTA, i / S) + (255,))
    mascara = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mascara).polygon(forma, fill=255)
    contorno = Image.composite(grad, contorno, mascara)
    img.alpha_composite(contorno.filter(ImageFilter.GaussianBlur(18)))
    img.alpha_composite(contorno)
    interno = [(170, 80), (S - 80, 80), (S - 80, S - 280), (S - 280, S - 80), (80, S - 80), (80, 170)]
    d = ImageDraw.Draw(img)
    d.polygon(interno, fill=PRETO)
    # cortes diagonais (slashes)
    for k, off in enumerate((0, 70)):
        cor = mistura(VERMELHO, MAGENTA, 0.3 + 0.4 * k)
        d.polygon([(S - 330 + off, S - 140), (S - 290 + off, S - 140), (S - 170 + off, S - 260), (S - 210 + off, S - 260)],
                  fill=cor)
    velocimetro(img, S * 0.47, S * 0.50, 250, 60, VERMELHO, MAGENTA)
    return img


OPCOES = {"A": opcao_a, "B": opcao_b, "C": opcao_c, "D": opcao_d}


def previa():
    fundo = Image.new("RGB", (1580, 560), (11, 18, 32))
    d = ImageDraw.Draw(fundo)
    try:
        f = ImageFont.truetype("bahnschrift.ttf", 40)
        fp = ImageFont.truetype("bahnschrift.ttf", 22)
    except OSError:
        f = fp = ImageFont.load_default()
    for i, (k, fn) in enumerate(OPCOES.items()):
        img = fn()
        img.save(PASTA / f"icone_{k}.png")
        x = 60 + i * 390
        fundo.paste(img.resize((300, 300), Image.LANCZOS), (x, 50), img.resize((300, 300), Image.LANCZOS))
        for j, t in enumerate((64, 32, 16)):
            p = img.resize((t, t), Image.LANCZOS)
            fundo.paste(p, (x + j * 90 + (64 - t) // 2, 400 + (64 - t) // 2), p)
        d.text((x + 120, 480), f"Opção {k}", fill=(255, 196, 0), font=f)
    fundo.save(PASTA / "previa_icones.png")


TAMANHOS = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def instalar(k, nome="icone"):
    img = OPCOES[k]()
    img.save(PASTA / f"{nome}.png")
    img.save(PASTA / f"{nome}.ico", sizes=TAMANHOS)


def paletas():
    """Um ícone para cada paleta do app: Strix (D) e TUF (A)."""
    instalar("D", "icone_strix")
    instalar("A", "icone_tuf")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() == "paletas":
        paletas()
    elif len(sys.argv) > 1:
        instalar(sys.argv[1].upper())
    else:
        previa()
    print("ok")
