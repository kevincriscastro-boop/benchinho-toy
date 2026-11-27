"""Acha e lê a configuração gráfica do jogo que está sendo gravado.

Cada jogo guarda em um lugar e formato diferente. Os conhecidos ficam em JOGOS; para os outros,
procuramos sozinhos (Unreal: GameUserSettings.ini; demais: arquivos de config perto do .exe ou em
AppData/Documentos modificados por último).
"""
import os
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

LA = Path(os.environ.get("LOCALAPPDATA", ""))
RA = Path(os.environ.get("APPDATA", ""))
HOME = Path.home()
DOCS = [HOME / "OneDrive" / "Documents", HOME / "Documents", HOME / "OneDrive" / "Documentos"]

# exe (minúsculo) -> (nome bonito, [arquivos], formato)
JOGOS = {
    "polaris-win64-shipping.exe": ("TEKKEN 8", [LA / "TEKKEN 8/Saved/Config/Windows/GameUserSettings.ini"], "ue"),
    "absolver-win64-shipping.exe": ("Absolver", [LA / "Absolver/Saved/Config/WindowsNoEditor/GameUserSettings.ini"], "ue"),
    "hogwartslegacy.exe": ("Hogwarts Legacy", [LA / "Hogwarts Legacy/Saved/Config/WindowsNoEditor/GameUserSettings.ini"], "ue"),
    "darksoulsiii.exe": ("Dark Souls III", [RA / "DarkSoulsIII/GraphicsConfig.xml"], "xml"),
    "needforspeedheat.exe": ("Need for Speed Heat", [d / "Need for Speed Heat/settings/PROFILEOPTIONS_profile" for d in DOCS], "frostbite"),
    "needforspeedheattrial.exe": ("Need for Speed Heat", [d / "Need for Speed Heat/settings/PROFILEOPTIONS_profile" for d in DOCS], "frostbite"),
    # registro do Windows (HKCU\...)
    "milesmorales.exe": ("Marvel's Spider-Man: Miles Morales", [r"Software\Insomniac Games\Marvel's Spider-Man Miles Morales\Graphics"], "registro"),
    # "@exe" = pasta do executável do jogo
    "re4.exe": ("Resident Evil 4", ["@exe/local_config_default.ini",
                                    Path(r"D:\SteamLibrary\steamapps\common\RESIDENT EVIL 4  BIOHAZARD RE4\local_config_default.ini")], "ini"),
}

# o que interessa numa configuração gráfica
CHAVES = re.compile(r"(quality|qualidade|shadow|sombra|texture|textura|antialias|aa_|fxaa|taa|msaa|dlss|fsr|xess|upscal|"
                    r"raytrac|rtx|reflection|vsync|v-sync|framerate|frame_rate|fpslimit|fps_limit|maxfps|resolution|resolucao|"
                    r"screenmode|fullscreen|windowmode|hdr|motionblur|bloom|ambientocclusion|ssao|hbao|volumetric|"
                    r"foliage|viewdistance|draw_?distance|effects|postprocess|shading|anisotrop|lod|reflex|dynamicres|"
                    r"gstrender|scalability|sg\.)", re.I)

NIVEL_UE = {"0": "0 · Baixo", "1": "1 · Médio", "2": "2 · Alto", "3": "3 · Épico", "4": "4 · Cinemático"}
ROTULOS_UE = {
    "sg.ResolutionQuality": "Escala de resolução (%)", "sg.ViewDistanceQuality": "Distância de visão",
    "sg.AntiAliasingQuality": "Anti-aliasing", "sg.ShadowQuality": "Sombras", "sg.GlobalIlluminationQuality": "Iluminação global",
    "sg.ReflectionQuality": "Reflexos", "sg.PostProcessQuality": "Pós-processamento", "sg.TextureQuality": "Texturas",
    "sg.EffectsQuality": "Efeitos", "sg.FoliageQuality": "Vegetação", "sg.ShadingQuality": "Shading",
    "ResolutionSizeX": "Resolução (largura)", "ResolutionSizeY": "Resolução (altura)",
    "FullscreenMode": "Modo de tela", "LastConfirmedFullscreenMode": "Modo de tela (confirmado)",
    "bUseVSync": "V-Sync", "FrameRateLimit": "Limite de FPS", "bUseDynamicResolution": "Resolução dinâmica",
}
MODO_TELA_UE = {"0": "Tela cheia exclusiva", "1": "Janela sem borda", "2": "Janela"}


def _ler_ini(caminho):
    secao, out = "", []
    for linha in caminho.read_text(encoding="utf-8", errors="replace").splitlines():
        linha = linha.strip()
        if linha.startswith("[") and linha.endswith("]"):
            secao = linha[1:-1]
        elif "=" in linha and not linha.startswith(";"):
            k, v = linha.split("=", 1)
            out.append((secao, k.strip(), v.strip()))
    return out


def _ue(caminho):
    itens = {}
    for secao, k, v in _ler_ini(caminho):
        if k in ROTULOS_UE:
            rot = ROTULOS_UE[k]
            if k == "sg.ResolutionQuality":
                try:
                    v = f"{float(v):.0f}%"
                except ValueError:
                    pass
            elif k.startswith("sg."):
                v = NIVEL_UE.get(v, v)
            elif "FullscreenMode" in k:
                v = MODO_TELA_UE.get(v, v)
            elif k.startswith("b"):
                v = {"True": "Ligado", "False": "Desligado"}.get(v, v)
            elif k == "FrameRateLimit":
                v = "sem limite" if v.startswith("0.0") else v.split(".")[0]
            itens[rot] = v
        elif CHAVES.search(k) and "Script/Engine.GameUserSettings" not in secao:
            itens[f"{k}" + (f" [{secao.split('.')[-1]}]" if secao else "")] = v
    if "Resolução (largura)" in itens and "Resolução (altura)" in itens:
        itens = {"Resolução": f"{itens.pop('Resolução (largura)')}x{itens.pop('Resolução (altura)')}", **itens}
    return itens


def _xml(caminho):
    itens = {}
    try:
        raiz = ET.parse(caminho).getroot()
    except ET.ParseError:
        return itens
    for el in raiz.iter():
        txt = (el.text or "").strip()
        if txt and len(el) == 0:
            itens[el.tag] = txt
    return itens


def _ini_tudo(caminho):
    """Arquivo que já é só de gráficos: mostra tudo que tem valor."""
    return {k: v for _, k, v in _ler_ini(caminho) if v != ""}


def _registro(chave, destino):
    """Lê HKCU\\<chave>, guarda uma cópia em texto e devolve os valores."""
    import winreg
    itens = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, chave) as k:
            i = 0
            while True:
                try:
                    nome, valor, _ = winreg.EnumValue(k, i)
                except OSError:
                    break
                itens[nome] = str(valor)
                i += 1
    except OSError:
        return {}
    (destino / "registro.txt").write_text(f"HKCU\\{chave}\n" + "\n".join(f"{k} = {v}" for k, v in itens.items()),
                                          encoding="utf-8")
    return itens


def _frostbite(caminho):
    itens = {}
    for linha in caminho.read_text(encoding="utf-8", errors="replace").splitlines():
        partes = linha.strip().split(None, 1)
        if len(partes) == 2 and partes[0].startswith("GstRender."):
            itens[partes[0].replace("GstRender.", "")] = partes[1]
    return itens


def _generico(caminho):
    suf = caminho.suffix.lower()
    if suf == ".xml":
        brutos = _xml(caminho)
    elif suf in (".ini", ".cfg", ".txt", ""):
        brutos = {}
        for secao, k, v in _ler_ini(caminho):
            brutos[k] = v
        if not brutos:  # formato "chave valor"
            for linha in caminho.read_text(encoding="utf-8", errors="replace").splitlines():
                p = linha.strip().split(None, 1)
                if len(p) == 2:
                    brutos[p[0]] = p[1]
    elif suf == ".json":
        import json
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            return {}
        brutos = {}

        def achatar(o, pref=""):
            if isinstance(o, dict):
                for k, v in o.items():
                    achatar(v, f"{pref}{k}.")
            elif not isinstance(o, list):
                brutos[pref[:-1]] = str(o)
        achatar(dados)
    else:
        return {}
    return {k: v for k, v in brutos.items() if CHAVES.search(k)}


def _procurar(exe, pasta_exe):
    """Para jogos desconhecidos: devolve (arquivos, formato)."""
    nome = exe.lower()
    if nome.endswith("-win64-shipping.exe") or nome.endswith("-wingdk-shipping.exe"):
        cands = list(LA.glob("*/Saved/Config/Windows*/GameUserSettings.ini"))
        base = nome.split("-")[0]
        mesmos = [c for c in cands if base in c.parts[-5].lower().replace(" ", "")]
        cands = mesmos or cands
        if cands:
            return [max(cands, key=lambda p: p.stat().st_mtime)], "ue"
    padrao = re.compile(r"(config|settings|graphics|video|options|prefs|profile)", re.I)
    lugares = []
    if pasta_exe:
        lugares += [pasta_exe, pasta_exe.parent]
    stem = Path(exe).stem.lower()
    for raiz in [LA, RA] + DOCS + [HOME / "Saved Games"]:
        if raiz.exists():
            for d in raiz.iterdir():
                if d.is_dir() and (stem[:5] in d.name.lower().replace(" ", "")):
                    lugares.append(d)
    achados = []
    for lugar in lugares:
        try:
            for f in lugar.rglob("*"):
                if (f.is_file() and padrao.search(f.name) and f.suffix.lower() in (".ini", ".xml", ".json", ".cfg", "")
                        and f.stat().st_size < 512 * 1024):
                    achados.append(f)
        except (PermissionError, OSError):
            continue
    achados.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return achados[:3], "generico"


def capturar(exe, pid, destino):
    """Copia os arquivos de configuração do jogo para `destino` e devolve
    {'jogo', 'arquivos': [...], 'itens': {rótulo: valor}, 'fonte': 'conhecida'|'procurada'}."""
    if not exe:
        return None
    pasta_exe = None
    try:
        import psutil
        pasta_exe = Path(psutil.Process(pid).exe()).parent if pid else None
    except Exception:
        pass
    conhecido = JOGOS.get(exe.lower())
    destino.mkdir(parents=True, exist_ok=True)
    if conhecido and conhecido[2] == "registro":
        nome, chaves, _ = conhecido
        itens = {}
        for c in chaves:
            itens.update(_registro(c, destino))
        return {"jogo": nome, "arquivos": [f"HKCU\\{c}" for c in chaves], "itens": itens, "fonte": "conhecida"}
    if conhecido:
        nome, arquivos, formato = conhecido
        resolvidos = []
        for a in arquivos:
            if isinstance(a, str) and a.startswith("@exe/"):
                if not pasta_exe:
                    continue
                a = pasta_exe / a[5:]
            resolvidos.append(Path(a))
        arquivos = [a for a in resolvidos if a.exists()][:1] if formato == "ini" else [a for a in resolvidos if a.exists()]
        fonte = "conhecida"
    else:
        nome = Path(exe).stem
        arquivos, formato = _procurar(exe, pasta_exe)
        fonte = "procurada"
    leitor = {"ue": _ue, "xml": _xml, "frostbite": _frostbite, "ini": _ini_tudo}.get(formato, _generico)
    itens = {}
    for a in arquivos:
        try:
            shutil.copy2(a, destino / a.name)
            itens.update(leitor(a))
        except OSError:
            continue
    return {"jogo": nome, "arquivos": [str(a) for a in arquivos], "itens": itens, "fonte": fonte}
