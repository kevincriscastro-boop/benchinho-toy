"""Monitor de Jogos: grava FPS (PresentMon) e recursos (CPU, GPU, RAM, discos) e gera relatório HTML.

Uso:
    pythonw monitor_jogos.py                 abre a janela (pede administrador: o PresentMon precisa)
    python  monitor_jogos.py --teste 10      grava 10 s sem janela e gera o relatório (teste rápido)

Atalho global: Ctrl+Alt+F10 inicia/para a gravação mesmo com o jogo em tela cheia.
"""
import csv
import ctypes
import datetime as dt
import html
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import webbrowser
from collections import Counter, defaultdict, deque
from ctypes import wintypes
from pathlib import Path

import psutil

PASTA = Path(__file__).resolve().parent
sys.path.insert(0, str(PASTA))
import config_jogos  # noqa: E402
SESSOES = PASTA / "sessoes"
def _achar_presentmon():
    """PresentMon oficial da Intel em bin\\ (o que vem no FrameView SDK da NVIDIA não roda sozinho)."""
    for p in sorted((PASTA / "bin").glob("PresentMon*.exe"), reverse=True):
        return p
    return None


PRESENTMON = _achar_presentmon()
SESSAO_ETW = "MonitorDeJogos"
CREATE_NO_WINDOW = 0x08000000

# Processos que desenham na tela mas não são jogo.
IGNORAR = {
    "dwm.exe", "explorer.exe", "firefox.exe", "chrome.exe", "msedge.exe", "msedgewebview2.exe",
    "code.exe", "antigravity.exe", "antigravity ide.exe", "discord.exe", "steamwebhelper.exe",
    "steam.exe", "obs64.exe", "whatsapp.exe", "whatsapp.root.exe", "notion.exe", "ms-teams.exe",
    "searchhost.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe", "textinputhost.exe",
    "pythonw.exe", "python.exe", "nvidia overlay.exe", "armourycrate.exe", "applicationframehost.exe",
    "phoneexperiencehost.exe", "lockapp.exe", "systemsettings.exe", "riotclientux.exe", "epicgameslauncher.exe",
}


def letras_discos():
    """'C' -> 'PhysicalDrive0' (via PowerShell, uma vez só)."""
    try:
        saida = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Partition | Where-Object DriveLetter | ForEach-Object { \"$($_.DriveLetter)|$($_.DiskNumber)\" }"],
            capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW).stdout
        return {l.split("|")[0].strip().upper(): f"PhysicalDrive{l.split('|')[1].strip()}"
                for l in saida.splitlines() if "|" in l}
    except Exception:
        return {}


def nome_discos():
    """PhysicalDriveN -> modelo (via PowerShell, uma vez só)."""
    try:
        saida = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-PhysicalDisk | ForEach-Object { \"$($_.DeviceId)|$($_.FriendlyName)\" }"],
            capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW).stdout
        nomes = {}
        for linha in saida.splitlines():
            if "|" in linha:
                i, n = linha.split("|", 1)
                nomes[f"PhysicalDrive{i.strip()}"] = n.replace("NVMe ", "").strip()
        return nomes
    except Exception:
        return {}


# --------------------------------------------------------------------------- telas

class _DEVMODEW(ctypes.Structure):
    _fields_ = [("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD), ("dmDriverVersion", wintypes.WORD),
                ("dmSize", wintypes.WORD), ("dmDriverExtra", wintypes.WORD), ("dmFields", wintypes.DWORD),
                ("dmPositionX", wintypes.LONG), ("dmPositionY", wintypes.LONG), ("dmDisplayOrientation", wintypes.DWORD),
                ("dmDisplayFixedOutput", wintypes.DWORD), ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
                ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short),
                ("dmFormName", wintypes.WCHAR * 32), ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD),
                ("dmPelsWidth", wintypes.DWORD), ("dmPelsHeight", wintypes.DWORD), ("dmDisplayFlags", wintypes.DWORD),
                ("dmDisplayFrequency", wintypes.DWORD), ("dmICMMethod", wintypes.DWORD), ("dmICMIntent", wintypes.DWORD),
                ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD), ("dmReserved1", wintypes.DWORD),
                ("dmReserved2", wintypes.DWORD), ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD)]


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32), ("DeviceString", wintypes.WCHAR * 128),
                ("StateFlags", wintypes.DWORD), ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


def telas():
    """{'\\\\.\\DISPLAY1': {'res': '2560x1600', 'hz': 165, 'principal': True, 'nome': '...'}}"""
    u = ctypes.windll.user32
    out, i = {}, 0
    while True:
        dd = _DISPLAY_DEVICEW()
        dd.cb = ctypes.sizeof(dd)
        if not u.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            break
        i += 1
        if not dd.StateFlags & 0x1:  # ATTACHED_TO_DESKTOP
            continue
        dm = _DEVMODEW()
        dm.dmSize = ctypes.sizeof(dm)
        if not u.EnumDisplaySettingsW(dd.DeviceName, -1, ctypes.byref(dm)):
            continue
        mon = _DISPLAY_DEVICEW()
        mon.cb = ctypes.sizeof(mon)
        u.EnumDisplayDevicesW(dd.DeviceName, 0, ctypes.byref(mon), 0)
        out[dd.DeviceName] = {"res": f"{dm.dmPelsWidth}x{dm.dmPelsHeight}", "hz": int(dm.dmDisplayFrequency),
                              "principal": bool(dd.StateFlags & 0x4), "nome": mon.DeviceString or dd.DeviceString}
    return out


def pid_em_foco():
    """PID do processo dono da janela ativa (a que recebe teclado e mouse)."""
    u = ctypes.windll.user32
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return None
    p = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
    return p.value


def tela_do_processo(pid):
    """Nome do monitor (\\\\.\\DISPLAYn) onde está a maior janela visível do processo."""
    u = ctypes.windll.user32
    melhor = {"area": 0, "hwnd": None}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cada(hwnd, _):
        p = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and u.IsWindowVisible(hwnd):
            r = wintypes.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            area = (r.right - r.left) * (r.bottom - r.top)
            if area > melhor["area"]:
                melhor.update(area=area, hwnd=hwnd)
        return True

    u.EnumWindows(cada, 0)
    if not melhor["hwnd"]:
        return None
    hmon = u.MonitorFromWindow(melhor["hwnd"], 2)  # MONITOR_DEFAULTTONEAREST
    mi = _MONITORINFOEXW()
    mi.cbSize = ctypes.sizeof(mi)
    if u.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        return mi.szDevice
    return None


# --------------------------------------------------------------------------- coleta

class LeitorGPU(threading.Thread):
    """Mantém um nvidia-smi em loop (1 leitura/s) e guarda o último valor."""

    CAMPOS = ["gpu_uso", "gpu_temp", "gpu_watts", "gpu_mhz", "vram_mb"]

    def __init__(self):
        super().__init__(daemon=True)
        self.valores = {c: None for c in self.CAMPOS}
        self.vram_total_mb = None
        self.proc = None

    def run(self):
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=10, creationflags=CREATE_NO_WINDOW)
            self.vram_total_mb = float(r.stdout.strip().splitlines()[0])
        except Exception:
            pass
        try:
            self.proc = subprocess.Popen(
                ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,power.draw,clocks.gr,memory.used",
                 "--format=csv,noheader,nounits", "-lms", "1000"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=CREATE_NO_WINDOW)
            for linha in self.proc.stdout:
                partes = [p.strip() for p in linha.split(",")]
                if len(partes) == 5:
                    for c, v in zip(self.CAMPOS, partes):
                        try:
                            self.valores[c] = float(v)
                        except ValueError:
                            self.valores[c] = None
        except FileNotFoundError:
            pass

    def parar(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


class LeitorFPS:
    """Recebe as linhas CSV do PresentMon (pela saída dele), grava o frames.csv e mantém o FPS ao vivo.

    O PresentMon 2.x tranca o arquivo que ele mesmo escreve; por isso ele manda o CSV para a saída
    padrão e quem grava o arquivo somos nós."""

    def __init__(self, caminho):
        self.caminho = caminho  # None = só ao vivo (overlay), sem gravar arquivo
        self.cab = None
        self.recentes = defaultdict(deque)  # app -> tempos (s) dos últimos presents
        self.fts = defaultdict(deque)  # app -> (tempo, frametime ms) dos últimos 30 s (1% low ao vivo)
        self.pids = {}  # app -> ProcessID
        self.total = Counter()  # app -> quadros na sessão inteira (escolhe o jogo de forma estável)
        self.ultimo_t = 0.0
        self.zero_relogio = None  # horário real (time.time) em que o TimeInSeconds do PresentMon vale 0
        self._trava = threading.Lock()
        self._thread = None

    def ligar(self, proc):
        """Lê a saída do PresentMon numa thread até ele terminar."""
        def ler():
            if self.caminho is None:
                for linha in proc.stdout:
                    if linha.strip():
                        self._processar(linha)
                return
            with open(self.caminho, "w", encoding="utf-8", newline="") as f:
                for linha in proc.stdout:
                    if not linha.strip():
                        continue
                    f.write(linha if linha.endswith("\n") else linha + "\n")
                    self._processar(linha)
        self._thread = threading.Thread(target=ler, daemon=True)
        self._thread.start()

    def esperar(self, segundos=10):
        if self._thread:
            self._thread.join(timeout=segundos)

    def atualizar(self):
        """Mantido por compatibilidade: a leitura agora é contínua."""
        with self._trava:
            for fila in self.recentes.values():
                while fila and fila[0] < self.ultimo_t - 2.0:
                    fila.popleft()

    def _processar(self, linha):
        campos = linha.rstrip("\r\n").split(",")
        with self._trava:
            if self.cab is None:
                self.cab = {c.strip().lstrip("﻿").lower(): i for i, c in enumerate(campos)}
                return
            try:
                app = campos[self.cab["application"]]
                t = float(campos[self.cab["timeinseconds"]])
            except (KeyError, ValueError, IndexError):
                return
            if self.zero_relogio is None:
                self.zero_relogio = time.time() - t
            if app.lower() in IGNORAR:
                return
            self.recentes[app].append(t)
            self.total[app] += 1
            if "processid" in self.cab:
                try:
                    self.pids[app] = int(campos[self.cab["processid"]])
                except (ValueError, IndexError):
                    pass
            self.ultimo_t = max(self.ultimo_t, t)
            fila = self.recentes[app]
            while fila and fila[0] < self.ultimo_t - 2.0:
                fila.popleft()
            if "msbetweenpresents" in self.cab:
                try:
                    ft = float(campos[self.cab["msbetweenpresents"]])
                except (ValueError, IndexError):
                    ft = None
                if ft and ft > 0:
                    filaft = self.fts[app]
                    filaft.append((t, ft))
                    while filaft and filaft[0][0] < self.ultimo_t - 60.0:
                        filaft.popleft()

    def frametimes(self, jogo):
        """Cópia de (tempo, frametime ms) do último minuto."""
        with self._trava:
            return list(self.fts.get(jogo, ()))

    def baixo_1(self, jogo):
        """1% low dos últimos 30 s (1000 ÷ frametime no percentil 99)."""
        with self._trava:
            fts = [ft for t, ft in self.fts.get(jogo, ()) if t >= self.ultimo_t - 30.0]
        if len(fts) < 30:
            return None
        p99 = _percentil(fts, 99)
        return 1000 / p99 if p99 else None

    def atual(self):
        """(jogo, fps no último segundo)"""
        with self._trava:
            if not self.total:
                return None, None
            # o jogo é quem mais desenhou na sessão inteira: não troca quando ele perde o foco e cai de FPS
            jogo = self.total.most_common(1)[0][0]
            fps = sum(1 for t in self.recentes[jogo] if t >= self.ultimo_t - 1.0)
            return jogo, fps


class Gravador:
    def __init__(self):
        self.ativo = False
        self.pasta = None
        self.nome = ""
        self.discos = nome_discos()
        self.letras = letras_discos()
        self.gpu = LeitorGPU()
        self.gpu.start()
        self.ao_vivo = {}
        self._pm = None
        self._fps = None
        self._thread = None
        self._parar = threading.Event()
        psutil.cpu_percent(None)

    # ---- controle
    def iniciar(self, nome):
        if self.ativo:
            return
        self.nome = nome.strip() or "Sessao"
        carimbo = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        seguro = re.sub(r'[<>:"/\\|?*]', "-", self.nome)[:60]
        self.pasta = SESSOES / f"{carimbo} - {seguro}"
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.info = {"nome": self.nome, "inicio": dt.datetime.now().isoformat(timespec="seconds"),
                     "discos": self.discos, "telas": telas(), "tela_do_jogo": None}
        self._salvar_info()
        frames = self.pasta / "frames.csv"
        # CSV pela saída padrão: o PresentMon 2.x tranca o arquivo que ele mesmo escreve.
        cmd = [str(PRESENTMON), "--output_stdout", "--no_console_stats", "--v1_metrics",
               "--session_name", SESSAO_ETW, "--stop_existing_session"]
        for p in sorted(IGNORAR):
            cmd += ["--exclude", p]
        self._log_pm = open(self.pasta / "presentmon.log", "w", encoding="utf-8")
        self._pm = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=self._log_pm, text=True,
                                    encoding="utf-8", errors="replace", bufsize=1, creationflags=CREATE_NO_WINDOW)
        self._fps = LeitorFPS(frames)
        self._fps.ligar(self._pm)
        self._contagem_telas = Counter()
        self._parar.clear()
        self._thread = threading.Thread(target=self._amostrar, daemon=True)
        self._thread.start()
        self.ativo = True

    def _registrar_disco_do_jogo(self, jogo):
        """Em qual SSD o jogo está instalado (pela pasta do .exe)."""
        pid = self._fps.pids.get(jogo) if jogo else None
        if not pid or self.info.get("disco_do_jogo"):
            return
        try:
            exe = Path(psutil.Process(pid).exe())
        except Exception:
            return
        letra = exe.drive.rstrip(":").upper()
        disco = self.letras.get(letra)
        self.info["disco_do_jogo"] = {"letra": f"{letra}:", "disco": disco,
                                      "modelo": self.discos.get(disco, disco or "?"), "pasta": str(exe.parent)}
        self._salvar_info()

    def _capturar_config(self, jogo):
        """Lê a configuração gráfica do jogo (no início e de novo ao parar: vale a última)."""
        if not jogo:
            return
        try:
            cfg = config_jogos.capturar(jogo, self._fps.pids.get(jogo), self.pasta / "configuracao_jogo")
        except Exception as e:
            cfg = {"jogo": jogo, "arquivos": [], "itens": {}, "fonte": f"erro: {e}"}
        if cfg:
            self.info["config"] = cfg
            self._salvar_info()

    def parar(self):
        if not self.ativo:
            return None
        self.ativo = False
        self._parar.set()
        self._thread.join(timeout=5)
        # cada passo protegido: aconteça o que acontecer, o PresentMon para e o relatório sai
        try:
            self._capturar_config(self._fps.atual()[0])
        except Exception as e:
            self._erro(f"config ao parar: {e}")
        try:
            subprocess.run([str(PRESENTMON), "--terminate_existing_session", "--session_name", SESSAO_ETW],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            self._erro(f"terminate_existing_session: {e}")
        try:
            self._pm.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._pm.kill()
        self._fps.esperar(10)  # termina de gravar o frames.csv
        try:
            self._log_pm.close()
        except Exception:
            pass
        self.info["relogio_presentmon"] = self._fps.zero_relogio
        self._salvar_info()
        return gerar_relatorio(self.pasta)

    def _erro(self, msg):
        try:
            with open(self.pasta / "erros.log", "a", encoding="utf-8") as f:
                f.write(f"{dt.datetime.now():%H:%M:%S} {msg}\n")
        except Exception:
            pass

    def fechar(self):
        if self.ativo:
            self.parar()
        self.gpu.parar()

    def _salvar_info(self):
        (self.pasta / "info.json").write_text(json.dumps(self.info, ensure_ascii=False, indent=1), encoding="utf-8")

    def _registrar_tela(self, jogo):
        """Guarda em que monitor a janela do jogo está (a tela onde ela passou mais tempo vence)."""
        pid = self._fps.pids.get(jogo) if jogo else None
        if not pid:
            return
        try:
            tela = tela_do_processo(pid)
        except Exception:
            tela = None
        if not tela:
            return
        self._contagem_telas[tela] += 1
        maior = self._contagem_telas.most_common(1)[0][0]
        if self.info.get("tela_do_jogo") != maior:
            self.info["tela_do_jogo"] = maior
            self._salvar_info()

    # ---- amostragem 1x por segundo
    def _amostrar(self):
        caminho = self.pasta / "recursos.csv"
        io0 = psutil.disk_io_counters(perdisk=True)
        t0 = time.time()
        self.info["relogio_recursos"] = t0  # para alinhar com o relógio do PresentMon no relatório
        self._salvar_info()
        ultimo = t0
        discos = sorted(io0)
        cab = ["t", "cpu", "ram_pct", "ram_gb"] + LeitorGPU.CAMPOS
        for d in discos:
            cab += [f"{d}_ler_mbs", f"{d}_gravar_mbs", f"{d}_ativo_pct"]
        cab.append("paginacao_gb")  # memória que o Windows jogou para o arquivo de paginação (aprox.)
        cab.append("foco")  # 1 = janela do jogo ativa, 0 = jogo em segundo plano, vazio = jogo ainda não detectado
        with open(caminho, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cab)
            while not self._parar.wait(1.0):
                agora = time.time()
                dt_s = max(0.001, agora - ultimo)
                ultimo = agora
                io1 = psutil.disk_io_counters(perdisk=True)
                mem = psutil.virtual_memory()
                linha = [round(agora - t0, 2), psutil.cpu_percent(None), mem.percent, round(mem.used / 2**30, 2)]
                linha += [self.gpu.valores[c] for c in LeitorGPU.CAMPOS]
                vivo_discos = {}
                for d in discos:
                    a, b = io0.get(d), io1.get(d)
                    if not a or not b:
                        linha += [None, None, None]
                        continue
                    ler = (b.read_bytes - a.read_bytes) / 2**20 / dt_s
                    grav = (b.write_bytes - a.write_bytes) / 2**20 / dt_s
                    ativo = min(100.0, ((b.read_time - a.read_time) + (b.write_time - a.write_time)) / (dt_s * 10))
                    linha += [round(ler, 2), round(grav, 2), round(ativo, 1)]
                    vivo_discos[d] = (ler, grav, ativo)
                io0 = io1
                jogo, fps, foco = None, None, None
                try:  # nada aqui pode derrubar a coleta de recursos
                    jogo, fps = self._fps.atual()
                    pid = self._fps.pids.get(jogo) if jogo else None
                    if pid:
                        foco = 1 if pid_em_foco() == pid else 0
                    self._registrar_tela(jogo)
                    self._registrar_disco_do_jogo(jogo)
                    if jogo and (self.info.get("config") or {}).get("jogo_exe") != jogo:
                        self._capturar_config(jogo)
                        self.info.setdefault("config", {})["jogo_exe"] = jogo
                        self._salvar_info()
                except Exception as e:
                    self._erro(f"amostra: {e}")
                try:
                    linha.append(round(psutil.swap_memory().used / 2**30, 2))
                except Exception:
                    linha.append(None)
                linha.append(foco)
                w.writerow(linha)
                f.flush()
                self.ao_vivo = {"t": agora - t0, "cpu": linha[1], "ram": mem.percent, "jogo": jogo, "fps": fps,
                                "foco": foco, "gpu": self.gpu.valores.copy(), "discos": vivo_discos}


# --------------------------------------------------------------------------- relatório

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _percentil(valores, p):
    if not valores:
        return None
    s = sorted(valores)
    k = (len(s) - 1) * p / 100
    i = int(k)
    return s[i] if i + 1 >= len(s) else s[i] + (s[i + 1] - s[i]) * (k - i)


def ler_frames(caminho):
    por_app = defaultdict(list)  # app -> [(t, ft)]
    if not caminho.exists():
        return None, []
    with open(caminho, newline="", encoding="utf-8-sig", errors="replace") as f:  # o PresentMon 2.x põe BOM
        r = csv.reader(f)
        cab = {c.strip().lstrip("﻿").lower(): i for i, c in enumerate(next(r, []))}
        it, ift, iapp = cab.get("timeinseconds"), cab.get("msbetweenpresents"), cab.get("application")
        if None in (it, ift, iapp):
            return None, []
        for linha in r:
            try:
                app = linha[iapp]
                if app.lower() in IGNORAR:
                    continue
                por_app[app].append((float(linha[it]), float(linha[ift])))
            except (ValueError, IndexError):
                continue
    if not por_app:
        return None, []
    jogo = max(por_app, key=lambda a: len(por_app[a]))
    return jogo, por_app[jogo][1:]


def estatisticas_fps(frames):
    if len(frames) < 10:
        return {}
    fts = [ft for _, ft in frames if ft > 0]
    dur = sum(fts) / 1000  # soma dos frametimes: funciona mesmo com trechos fora de foco removidos
    med = statistics.median(fts)
    p99, p999 = _percentil(fts, 99), _percentil(fts, 99.9)
    travadas = sum(1 for ft in fts if ft > max(2.5 * med, med + 20))
    return {
        "duracao_s": dur, "frames": len(fts),
        "fps_medio": len(fts) / dur if dur > 0 else None,
        "fps_1low": 1000 / p99 if p99 else None,
        "fps_01low": 1000 / p999 if p999 else None,
        "ft_medio": statistics.fmean(fts), "ft_p99": p99, "ft_max": max(fts),
        "travadas": travadas,
    }


def fps_por_segundo(frames):
    if not frames:
        return [], []
    t0 = frames[0][0]
    c = Counter(int(t - t0) for t, _ in frames)
    xs = list(range(0, max(c)))  # o último segundo está incompleto: fica de fora
    return xs, [c.get(x, 0) for x in xs]


def ler_recursos(caminho):
    if not caminho.exists():
        return {}
    with open(caminho, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        col = defaultdict(list)
        for linha in r:
            for k, v in linha.items():
                col[k].append(_num(v))
    return col


def trechos_fora_de_foco(rec):
    """[(inicio, fim)] em segundos do relógio dos recursos em que o jogo não estava em foco."""
    trechos = []
    for t, fo in zip(rec.get("t", []), rec.get("foco", [])):
        if t is None or fo != 0:
            continue
        a, b = max(0.0, t - 1.0), t
        if trechos and a <= trechos[-1][1] + 0.01:
            trechos[-1] = (trechos[-1][0], b)
        else:
            trechos.append((a, b))
    return trechos


def tirar_trechos(frames, trechos):
    """Remove os quadros que caem dentro dos trechos (mesmo relógio dos frames)."""
    if not trechos:
        return frames
    out, i = [], 0
    for t, ft in frames:
        while i < len(trechos) and t > trechos[i][1]:
            i += 1
        if i < len(trechos) and trechos[i][0] <= t <= trechos[i][1]:
            continue
        out.append((t, ft))
    return out


def _resumo(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return None, None
    return statistics.fmean(v), max(v)


CORES = ["#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed", "#0891b2"]


def grafico(titulo, xs, series, unidade="", y_min=0, linha_ref=None, sombras=()):
    """SVG de linhas sem dependências. series = [(nome, valores)]; sombras = [(x0, x1)] em cinza (fora de foco)."""
    W, H, ml, mr, mt, mb = 920, 240, 52, 16, 28, 30
    pts_validos = [v for _, vs in series for v in vs if v is not None]
    if not xs or not pts_validos:
        return f'<div class="graf"><h3>{html.escape(titulo)}</h3><p class="muted">sem dados</p></div>'
    y_max = max(pts_validos + ([linha_ref] if linha_ref else [])) * 1.1 or 1
    x_max = max(xs) or 1
    sx = lambda x: ml + (W - ml - mr) * x / x_max
    sy = lambda y: H - mb - (H - mt - mb) * (y - y_min) / (y_max - y_min)
    out = [f'<div class="graf"><h3>{html.escape(titulo)}</h3><svg viewBox="0 0 {W} {H}" role="img">']
    for a, b in sombras:
        a, b = max(0, a), min(x_max, b)
        if b > a:
            out.append(f'<rect x="{sx(a):.1f}" y="{mt}" width="{max(1.0, sx(b) - sx(a)):.1f}" height="{H - mt - mb}" class="sombra"/>')
    for i in range(5):
        y = y_min + (y_max - y_min) * i / 4
        out.append(f'<line x1="{ml}" x2="{W-mr}" y1="{sy(y):.1f}" y2="{sy(y):.1f}" class="grade"/>'
                   f'<text x="{ml-6}" y="{sy(y)+4:.1f}" text-anchor="end" class="eixo">{y:.0f}</text>')
    for i in range(6):
        x = x_max * i / 5
        out.append(f'<text x="{sx(x):.1f}" y="{H-8}" text-anchor="middle" class="eixo">{int(x//60)}:{int(x%60):02d}</text>')
    if linha_ref:
        out.append(f'<line x1="{ml}" x2="{W-mr}" y1="{sy(linha_ref):.1f}" y2="{sy(linha_ref):.1f}" class="ref"/>')
    leg = []
    for k, (nome, vs) in enumerate(series):
        cor = CORES[k % len(CORES)]
        segs, atual = [], []
        for x, v in zip(xs, vs):
            if v is None:
                if atual:
                    segs.append(atual)
                atual = []
            else:
                atual.append(f"{sx(x):.1f},{sy(v):.1f}")
        if atual:
            segs.append(atual)
        for s in segs:
            out.append(f'<polyline points="{" ".join(s)}" fill="none" stroke="{cor}" stroke-width="1.6"/>')
        leg.append(f'<span><i style="background:{cor}"></i>{html.escape(nome)}</span>')
    if sombras:
        leg.append('<span><i class="sombra-leg"></i>jogo fora de foco (fora das estatísticas)</span>')
    out.append("</svg>")
    out.append(f'<div class="leg">{"".join(leg)}{" <span class=muted>" + html.escape(unidade) + "</span>" if unidade else ""}</div></div>')
    return "".join(out)


CSS = """
:root{--bg:#f5f7fa;--card:#fff;--ink:#17202e;--muted:#5d6879;--line:#e3e8ef}
@media (prefers-color-scheme:dark){:root{--bg:#0f141b;--card:#171e28;--ink:#e7ecf3;--muted:#98a3b3;--line:#283242}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 "Segoe UI",system-ui,sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 48px}h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:28px 0 10px}
h3{font-size:14px;margin:0 0 6px;color:var(--muted);font-weight:600}.muted{color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card b{display:block;font-size:24px}.card span{font-size:12px;color:var(--muted)}
.graf{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:10px 0}
svg{width:100%;height:auto}.grade{stroke:var(--line)}.eixo{fill:var(--muted);font-size:11px}
.ref{stroke:var(--muted);stroke-dasharray:4 4}.leg{font-size:12px;color:var(--muted);display:flex;gap:14px;flex-wrap:wrap}
.leg i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:13px}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:right}th:first-child,td:first-child{text-align:left}
th{color:var(--muted);font-weight:600}a{color:#3b82f6}.bar{height:10px;border-radius:3px;background:#2563eb}
.sombra{fill:var(--muted);opacity:.18}.leg i.sombra-leg{background:var(--muted);opacity:.35}
"""


def _fmt(v, casas=0, suf=""):
    return "—" if v is None else f"{v:,.{casas}f}{suf}".replace(",", "X").replace(".", ",").replace("X", ".")


def gerar_relatorio(pasta):
    info = json.loads((pasta / "info.json").read_text(encoding="utf-8"))
    jogo, frames = ler_frames(pasta / "frames.csv")
    rec = ler_recursos(pasta / "recursos.csv")
    xs = [x or 0 for x in rec.get("t", [])]

    # trechos com o jogo fora de foco: saem das estatísticas e aparecem sombreados
    fora_rec = trechos_fora_de_foco(rec)
    r0, p0 = info.get("relogio_recursos"), info.get("relogio_presentmon")
    if r0 is not None and p0 is not None:
        desloc = r0 - p0  # tempo PresentMon = tempo recursos + desloc
    else:
        desloc = frames[0][0] if frames else 0.0
    fora_pm = [(a + desloc, b + desloc) for a, b in fora_rec]
    frames_foco = tirar_trechos(frames, fora_pm)
    fora_s = sum(b - a for a, b in fora_rec)
    est = estatisticas_fps(frames_foco)

    # RAM cheia (só no tempo em foco): quando passa de ~95% o Windows começa a paginar no disco
    em_foco = [r for r, fo in zip(rec.get("ram_pct", []), rec.get("foco", []) or [None] * len(rec.get("ram_pct", [])))
               if r is not None and fo != 0]
    ram_cheia_pct = (100 * sum(1 for r in em_foco if r >= 95) / len(em_foco)) if em_foco else None
    pag = [p for p in rec.get("paginacao_gb", []) if p is not None]
    aviso_ram = ""
    if ram_cheia_pct is not None and ram_cheia_pct >= 20:
        pag_txt = (f" O arquivo de paginação foi de {_fmt(pag[0], 1)} GB para até {_fmt(max(pag), 1)} GB." if pag else "")
        aviso_ram = (f"<div class='card' style='border-color:#dc2626;margin:12px 0'>⚠️ <b style='display:inline;font-size:inherit'>RAM cheia</b> "
                     f"(95% ou mais) em {ram_cheia_pct:.0f}% do tempo de jogo, máximo {_fmt(max(em_foco), 0)}%.{pag_txt} "
                     f"Com a memória cheia o Windows usa o disco como RAM: parte das travadas pode ser de memória, não do SSD. "
                     f"Para comparar SSDs, feche navegador/IDE/Steam ou baixe a qualidade de texturas.</div>")

    dj = info.get("disco_do_jogo") or {}
    disco_jogo_txt = f"{dj.get('letra', '')} {dj.get('modelo', '')}".strip() if dj else "não identificado"
    discos = info.get("discos", {})
    ids = sorted({k.rsplit("_", 2)[0] for k in rec if k.startswith("PhysicalDrive")})

    todas = info.get("telas") or {}
    tid = info.get("tela_do_jogo")
    tj = todas.get(tid) if tid else None
    descr = lambda t: f"{t['res']} @ {t['hz']} Hz ({'principal' if t['principal'] else 'externa'})"
    tela_txt = descr(tj) if tj else "não identificada"
    aviso_tela = ""
    if tj and tj["hz"] <= 75 and est.get("fps_medio") and abs(est["fps_medio"] - tj["hz"]) < 3:
        aviso_tela = (f"<div class='card' style='border-color:#d97706;margin:12px 0'>⚠️ O jogo estava numa tela de <b style='display:inline;font-size:inherit'>"
                      f"{tj['hz']} Hz</b> e o FPS ficou em ~{est['fps_medio']:.0f}: provavelmente limitado pela tela (V-Sync). "
                      f"Para comparar SSDs, grave os testes na mesma tela e com o V-Sync desligado.</div>")

    cpu = _resumo(rec.get("cpu", []))
    gpu = _resumo(rec.get("gpu_uso", []))
    gtemp = _resumo(rec.get("gpu_temp", []))
    gw = _resumo(rec.get("gpu_watts", []))
    vram = _resumo(rec.get("vram_mb", []))
    ram = _resumo(rec.get("ram_gb", []))
    disco_res = {}
    for d in ids:
        ler = [v or 0 for v in rec.get(f"{d}_ler_mbs", [])]
        grav = [v or 0 for v in rec.get(f"{d}_gravar_mbs", [])]
        disco_res[d] = {"nome": discos.get(d, d), "lido_gb": sum(ler) / 1024, "gravado_gb": sum(grav) / 1024,
                        "pico_ler": max(ler, default=0), "pico_grav": max(grav, default=0),
                        "ativo_medio": _resumo(rec.get(f"{d}_ativo_pct", []))[0]}

    cfg = info.get("config") or {}
    cfg_itens = cfg.get("itens") or {}
    resumo = {"pasta": pasta.name, "nome": info["nome"], "inicio": info["inicio"], "jogo": jogo, "tela": tela_txt,
              "jogo_nome": cfg.get("jogo") or jogo, "config": cfg_itens,
              **{k: est.get(k) for k in ("duracao_s", "fps_medio", "fps_1low", "fps_01low", "ft_p99", "travadas")},
              "fora_foco_s": fora_s, "disco_jogo": disco_jogo_txt, "ram_cheia_pct": ram_cheia_pct,
              "cpu_medio": cpu[0], "gpu_medio": gpu[0], "gpu_temp_max": gtemp[1], "ram_max_gb": ram[1],
              "discos": {v["nome"]: {"lido_gb": v["lido_gb"], "pico_ler": v["pico_ler"]} for v in disco_res.values()}}
    (pasta / "resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=1), encoding="utf-8")

    fx, fy = fps_por_segundo(frames)
    t0 = frames[0][0] if frames else 0
    passo = max(1, len(frames) // 4000)
    ftx = [t - t0 for t, _ in frames[::passo]]
    fty = [ft for _, ft in frames[::passo]]
    sombras_fps = [(a - t0, b - t0) for a, b in fora_pm]

    cards = [
        ("FPS médio", _fmt(est.get("fps_medio"), 1)), ("1% low", _fmt(est.get("fps_1low"), 1)),
        ("0,1% low", _fmt(est.get("fps_01low"), 1)), ("Frametime p99", _fmt(est.get("ft_p99"), 1, " ms")),
        ("Travadas", _fmt(est.get("travadas"))), ("Jogando (em foco)", f"{int((est.get('duracao_s') or 0)//60)}min{int((est.get('duracao_s') or 0)%60):02d}s"),
        ("CPU média", _fmt(cpu[0], 0, "%")), ("GPU média", _fmt(gpu[0], 0, "%")),
        ("GPU temp. máx", _fmt(gtemp[1], 0, " °C")), ("GPU consumo méd.", _fmt(gw[0], 0, " W")),
        ("VRAM máx", _fmt((vram[1] or 0) / 1024 if vram[1] else None, 1, " GB")), ("RAM máx", _fmt(ram[1], 1, " GB")),
    ]
    linhas_disco = "".join(
        f"<tr><td>{html.escape(v['nome'])}{' 🎮 <b>(jogo instalado aqui)</b>' if d == dj.get('disco') else ''}</td>"
        f"<td>{_fmt(v['lido_gb'], 2)} GB</td><td>{_fmt(v['gravado_gb'], 2)} GB</td>"
        f"<td>{_fmt(v['pico_ler'], 0)} MB/s</td><td>{_fmt(v['pico_grav'], 0)} MB/s</td><td>{_fmt(v['ativo_medio'], 1)}%</td></tr>"
        for d, v in disco_res.items())

    corpo = [
        f"<h1>{html.escape(info['nome'])}</h1>",
        f"<p class='muted'>{html.escape(jogo or 'jogo não detectado')} · {info['inicio'].replace('T', ' ')}"
        f" · <a href='../comparativo.html'>comparativo de todas as sessões</a></p>",
        f"<p class='muted'>Jogo instalado em: <b>{html.escape(disco_jogo_txt)}</b>"
        f"{' · ' + html.escape(dj['pasta']) if dj.get('pasta') else ''}<br>"
        f"Tela do jogo: <b>{html.escape(tela_txt)}</b> · telas ligadas: "
        + html.escape(" | ".join(descr(t) for t in todas.values()) or "—") + "</p>",
        aviso_tela, aviso_ram,
        (f"<p class='muted'>⏸ {int(fora_s//60)}min{int(fora_s%60):02d}s com o jogo <b>fora de foco</b> "
         f"(Alt+Tab, minimizado ou outra janela ativa): esses trechos ficaram de fora das estatísticas "
         f"e aparecem sombreados nos gráficos.</p>" if fora_s >= 1 else ""),
        "<div class='cards'>" + "".join(f"<div class='card'><b>{v}</b><span>{k}</span></div>" for k, v in cards) + "</div>",
        "<h2>FPS</h2>", grafico("FPS por segundo", fx, [("FPS", fy)], "quadros/s", linha_ref=est.get("fps_medio"), sombras=sombras_fps),
        grafico("Frametime", ftx, [("ms por quadro", fty)], "ms (picos = travadas)", sombras=sombras_fps),
        "<h2>CPU e GPU</h2>",
        grafico("Uso", xs, [("CPU %", rec.get("cpu", [])), ("GPU %", rec.get("gpu_uso", []))], "%", sombras=fora_rec),
        grafico("GPU: temperatura e consumo", xs, [("°C", rec.get("gpu_temp", [])), ("W", rec.get("gpu_watts", []))], sombras=fora_rec),
        grafico("Memória", xs, [("RAM (GB)", rec.get("ram_gb", [])), ("VRAM (GB)", [(v / 1024 if v else None) for v in rec.get("vram_mb", [])]),
                                ("Paginação no disco (GB)", rec.get("paginacao_gb", []))], "GB", sombras=fora_rec),
        grafico("RAM em uso", xs, [("RAM %", rec.get("ram_pct", []))], "% (acima de 95% o Windows pagina no disco)",
                linha_ref=95, sombras=fora_rec),
        "<h2>Discos</h2>",
        "<table><tr><th>SSD</th><th>Lido</th><th>Gravado</th><th>Pico leitura</th><th>Pico gravação</th><th>Atividade média</th></tr>"
        + linhas_disco + "</table>",
    ]
    corpo.append("<h2>Configuração gráfica do jogo</h2>")
    if cfg_itens:
        corpo.append("<table><tr><th>Opção</th><th>Valor</th></tr>" + "".join(
            f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>" for k, v in cfg_itens.items()) + "</table>")
        corpo.append(f"<p class='muted'>Lido de: {html.escape(' | '.join(cfg.get('arquivos') or []))}"
                     f"{' (arquivo achado automaticamente: confira se é o certo)' if cfg.get('fonte') == 'procurada' else ''}. "
                     "Cópia dos arquivos na pasta <code>configuracao_jogo</code> da sessão. "
                     "Ajustes do painel da NVIDIA (fora do jogo) não entram aqui.</p>")
    else:
        corpo.append("<p class='muted'>Não encontrei a configuração deste jogo"
                     + (f" ({html.escape(cfg.get('fonte'))})" if cfg.get("fonte", "").startswith("erro") else "")
                     + ". Me diga onde ele salva que eu incluo no app.</p>")
    corpo.append("<h2>Discos: gráficos</h2>")
    for d in ids:
        corpo.append(grafico(f"{discos.get(d, d)}: leitura e gravação", xs,
                             [("leitura MB/s", rec.get(f"{d}_ler_mbs", [])), ("gravação MB/s", rec.get(f"{d}_gravar_mbs", []))], "MB/s",
                             sombras=fora_rec))
    corpo.append("<p class='muted'>FPS e frametime: PresentMon (cada quadro apresentado). 1% low = 1000 ÷ frametime no percentil 99. "
                 "Travada = quadro acima de 2,5× a mediana. Atividade do disco = tempo de E/S no segundo (aproximado).</p>")
    _escrever_html(pasta / "relatorio.html", info["nome"], "".join(corpo))
    gerar_comparativo()
    return pasta / "relatorio.html"


def _escrever_html(caminho, titulo, corpo):
    caminho.write_text(f"<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                       f"<title>{html.escape(titulo)}</title><style>{CSS}</style></head><body><main>{corpo}</main></body></html>", encoding="utf-8")


def gerar_comparativo():
    res = []
    for p in sorted(SESSOES.glob("*/resumo.json")):
        try:
            res.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    if not res:
        return
    top = max((r.get("fps_medio") or 0) for r in res) or 1
    # o que mudou na configuração desde a sessão anterior do mesmo jogo
    anterior = {}
    for r in res:  # ordem cronológica
        chave = r.get("jogo_nome") or r.get("jogo")
        atual, antes = r.get("config") or {}, anterior.get(chave)
        if not atual:
            r["_mudou"] = "—"
        elif antes is None:
            r["_mudou"] = "primeira sessão"
        else:
            dif = [f"{k}: {antes.get(k, '—')} → {v}" for k, v in atual.items() if antes.get(k) != v]
            r["_mudou"] = "igual à anterior" if not dif else "; ".join(dif[:6]) + (" …" if len(dif) > 6 else "")
        if atual:
            anterior[chave] = atual
    linhas = []
    for r in reversed(res):
        lido = sum(d["lido_gb"] for d in r.get("discos", {}).values())
        larg = 100 * (r.get("fps_medio") or 0) / top
        linhas.append(
            f"<tr><td><a href='{html.escape(r['pasta'])}/relatorio.html'>{html.escape(r['nome'])}</a><br><span class='muted'>{html.escape(r.get('jogo') or '—')} · {r['inicio'][:16].replace('T',' ')}"
            f" · tela {html.escape(r.get('tela') or '—')}<br>🎮 jogo no <b>{html.escape(r.get('disco_jogo') or '—')}</b>"
            f"{' · ⚠️ RAM cheia ' + _fmt(r['ram_cheia_pct'], 0, '%') + ' do tempo' if (r.get('ram_cheia_pct') or 0) >= 20 else ''}</span></td>"
            f"<td><div class='bar' style='width:{larg:.0f}%'></div>{_fmt(r.get('fps_medio'), 1)}</td><td>{_fmt(r.get('fps_1low'), 1)}</td>"
            f"<td>{_fmt(r.get('fps_01low'), 1)}</td><td>{_fmt(r.get('travadas'))}</td><td>{_fmt(r.get('cpu_medio'), 0, '%')}</td>"
            f"<td>{_fmt(r.get('gpu_medio'), 0, '%')}</td><td>{_fmt(r.get('gpu_temp_max'), 0, ' °C')}</td><td>{_fmt(lido, 2, ' GB')}</td>"
            f"<td style='text-align:left;max-width:260px'>{html.escape(r.get('_mudou', '—'))}</td></tr>")
    corpo = ("<h1>Comparativo das sessões</h1><p class='muted'>Mais recente primeiro. Clique no nome para ver o relatório completo. "
             "Para comparar SSDs: o jogo tem que estar instalado no SSD testado (🎮), a configuração gráfica "
             "\"igual à anterior\" (última coluna) e sem ⚠️ de RAM cheia.</p>"
             "<table><tr><th>Sessão</th><th>FPS médio</th><th>1% low</th><th>0,1% low</th><th>Travadas</th><th>CPU</th><th>GPU</th>"
             "<th>GPU máx</th><th>Lido dos SSDs</th><th>Config. gráfica</th></tr>" + "".join(linhas) + "</table>")
    _escrever_html(SESSOES / "comparativo.html", "Comparativo das sessões", corpo)


# --------------------------------------------------------------------------- janela

def eh_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# --------------------------------------------------------------------------- overlay

def retangulo_da_tela(pid):
    """(esquerda, topo, direita, base) do monitor onde está o jogo; sem jogo, o monitor principal."""
    u = ctypes.windll.user32
    if pid:
        try:
            tela = tela_do_processo(pid)
        except Exception:
            tela = None
        if tela:
            achado = {}

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
            def cada(hmon, _hdc, _r, _p):
                mi = _MONITORINFOEXW()
                mi.cbSize = ctypes.sizeof(mi)
                if u.GetMonitorInfoW(wintypes.HMONITOR(hmon), ctypes.byref(mi)) and mi.szDevice == tela:
                    r = mi.rcMonitor
                    achado["r"] = (r.left, r.top, r.right, r.bottom)
                return True

            u.EnumDisplayMonitors(None, None, cada, 0)
            if "r" in achado:
                return achado["r"]
    return (0, 0, u.GetSystemMetrics(0), u.GetSystemMetrics(1))


class _PDH_VALOR(ctypes.Structure):
    _fields_ = [("status", wintypes.DWORD), ("valor", ctypes.c_double)]


class ContadorPDH:
    """Lê um contador de desempenho do Windows (o mesmo do Monitor de Desempenho)."""

    def __init__(self, caminho):
        self.ok = False
        try:
            self.pdh = ctypes.windll.pdh
            self.q, self.c = ctypes.c_void_p(), ctypes.c_void_p()
            if (self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.q)) == 0
                    and self.pdh.PdhAddEnglishCounterW(self.q, caminho, None, ctypes.byref(self.c)) == 0):
                self.pdh.PdhCollectQueryData(self.q)  # a 1ª coleta só serve de base para a taxa
                self.ok = True
        except Exception:
            pass

    def ler(self):
        if not self.ok:
            return None
        try:
            if self.pdh.PdhCollectQueryData(self.q) != 0:
                return None
            v = _PDH_VALOR()
            if self.pdh.PdhGetFormattedCounterValue(self.c, 0x200, None, ctypes.byref(v)) != 0:  # PDH_FMT_DOUBLE
                return None
            return v.valor
        except Exception:
            return None


class AoVivo:
    """Sessão própria do PresentMon só para o overlay (funciona com ou sem gravação)."""

    SESSAO = SESSAO_ETW + "AoVivo"

    def __init__(self, gpu):
        self.gpu = gpu
        self.proc = None
        self.leitor = None
        self.paginas = ContadorPDH(r"\Memory\Pages Input/sec")  # páginas lidas do disco por falta de RAM
        psutil.cpu_percent(None)

    def ligar(self):
        if self.proc and self.proc.poll() is None:
            return
        cmd = [str(PRESENTMON), "--output_stdout", "--no_console_stats", "--v1_metrics",
               "--session_name", self.SESSAO, "--stop_existing_session"]
        for p in sorted(IGNORAR):
            cmd += ["--exclude", p]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                     encoding="utf-8", errors="replace", bufsize=1, creationflags=CREATE_NO_WINDOW)
        self.leitor = LeitorFPS(None)
        self.leitor.ligar(self.proc)

    def desligar(self):
        if not self.proc:
            return
        try:
            subprocess.run([str(PRESENTMON), "--terminate_existing_session", "--session_name", self.SESSAO],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc = None

    def amostra(self):
        jogo, fps = self.leitor.atual() if self.leitor else (None, None)
        pid = self.leitor.pids.get(jogo) if (self.leitor and jogo) else None
        foco = None
        if pid:
            try:
                foco = 1 if pid_em_foco() == pid else 0
            except Exception:
                pass
        # frametime: 90 colunas cobrindo 30 s, cada uma com o PIOR quadro dela (assim a travada não some na média)
        fts = self.leitor.frametimes(jogo) if (self.leitor and jogo) else []
        colunas, limite, travadas = [], None, 0
        if len(fts) >= 30:
            med = statistics.median(ft for _, ft in fts)
            limite = max(2.5 * med, med + 20)  # mesma regra de "travada" do relatório
            travadas = sum(1 for _, ft in fts if ft > limite)  # no último minuto
            fim, n = fts[-1][0], 90
            colunas = [None] * n
            for t, ft in fts:
                i = int((t - (fim - 30)) / 30 * n)
                if 0 <= i < n and (colunas[i] is None or ft > colunas[i]):
                    colunas[i] = ft
        mem = psutil.virtual_memory()
        pag = self.paginas.ler()
        return {"jogo": jogo, "pid": pid, "fps": fps, "low1": self.leitor.baixo_1(jogo) if (self.leitor and jogo) else None,
                "foco": foco, "cpu": psutil.cpu_percent(None), "ram_livre": mem.available / 2**30, "ram_total": mem.total / 2**30,
                "ram_pct": mem.percent, "gpu": self.gpu.valores.get("gpu_uso"), "gpu_temp": self.gpu.valores.get("gpu_temp"),
                "vram": self.gpu.valores.get("vram_mb"), "vram_total": self.gpu.vram_total_mb,
                "pag_mbs": pag * 4096 / 2**20 if pag is not None else None,
                "ft": colunas, "ft_limite": limite, "travadas": travadas}


class Overlay:
    """Caixinha transparente por cima do jogo: o clique atravessa e ela não rouba o foco.
    Não aparece em jogos em tela cheia EXCLUSIVA (use 'janela sem bordas')."""

    # paleta "neon noturno"
    CHAVE = "#010203"  # cor que vira transparente (cantos arredondados)
    FUNDO, BORDA, TRILHO = "#0b1220", "#22304d", "#16213a"
    TEXTO, APAGADO = "#e2e8f0", "#7c8aa5"
    CIANO, VIOLETA, VERDE, AMBAR, ROSA = "#22d3ee", "#a78bfa", "#4ade80", "#fbbf24", "#fb7185"
    AZUL = "#60a5fa"
    W = 280
    ALTURAS = {"Compacto": 176, "Completo": 244}

    @property
    def H(self):
        return self.ALTURAS.get(self.tamanho.get(), 176)

    def __init__(self, raiz, aovivo, gravador, canto_var, tamanho_var):
        import tkinter as tk
        self.raiz, self.aovivo, self.g, self.canto = raiz, aovivo, gravador, canto_var
        self.tamanho = tamanho_var
        self.visivel = False
        self.pisca = False
        self.top = tk.Toplevel(raiz)
        self.top.withdraw()
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.attributes("-transparentcolor", self.CHAVE)
        self.top.attributes("-alpha", 0.94)
        self.cv = tk.Canvas(self.top, width=self.W, height=self.H, bg=self.CHAVE, highlightthickness=0, bd=0)
        self.cv.pack()
        self.top.update_idletasks()
        self._clique_atravessa()

    def _clique_atravessa(self):
        u = ctypes.windll.user32
        hwnd = u.GetParent(self.top.winfo_id()) or self.top.winfo_id()
        GWL_EXSTYLE = -20
        estilo = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        # LAYERED | TRANSPARENT (clique atravessa) | TOOLWINDOW (fora da barra) | NOACTIVATE (não rouba foco)
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, estilo | 0x80000 | 0x20 | 0x80 | 0x08000000)

    def alternar(self):
        if self.visivel:
            self.esconder()
        else:
            self.mostrar()
        return self.visivel

    def mostrar(self):
        self.visivel = True
        self.aovivo.ligar()
        self.top.deiconify()
        self.top.attributes("-topmost", True)
        self._tick()

    def esconder(self):
        self.visivel = False
        self.top.withdraw()
        threading.Thread(target=self.aovivo.desligar, daemon=True).start()

    def _posicionar(self, pid):
        l, t, r, b = retangulo_da_tela(pid)
        m = 16
        canto = self.canto.get()
        x = l + m if "esquerdo" in canto else r - self.W - m
        y = t + m if "Superior" in canto else b - self.H - m - 40
        self.top.geometry(f"{self.W}x{self.H}+{x}+{y}")
        self.cv.config(height=self.H)

    def _cor_fps(self, fps):
        if fps is None:
            return self.APAGADO
        return self.CIANO if fps >= 100 else self.VERDE if fps >= 60 else self.AMBAR if fps >= 30 else self.ROSA

    @staticmethod
    def _mistura(c1, c2, f):
        a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
        return "#" + "".join(f"{int(x + (y - x) * f):02x}" for x, y in zip(a, b))

    def _arredondado(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.cv.create_polygon(pts, smooth=True, **kw)

    def _barra(self, y, rotulo, pct, cor, texto):
        cv, x0, x1 = self.cv, 54, self.W - 96
        cv.create_text(16, y, text=rotulo, anchor="w", fill=self.APAGADO, font=("Bahnschrift", 9))
        self._arredondado(x0, y - 4, x1, y + 4, 4, fill=self.TRILHO, outline="")
        if pct:
            fim = x0 + (x1 - x0) * max(0.04, min(1.0, pct / 100))
            self._arredondado(x0, y - 4, fim, y + 4, 4, fill=cor, outline="")
        cv.create_text(self.W - 16, y, text=texto, anchor="e", fill=self.TEXTO, font=("Bahnschrift SemiBold", 10))

    def _grafico_ft(self, d, gx0, gy0, gx1, gy1):
        cv = self.cv
        self._arredondado(gx0, gy0, gx1, gy1, 6, fill="#0f1a2e", outline="")
        cols, lim = d["ft"], d["ft_limite"]
        if not cols or lim is None:
            cv.create_text((gx0 + gx1) / 2, (gy0 + gy1) / 2, text="frametime: esperando quadros…",
                           fill=self.APAGADO, font=("Bahnschrift", 8))
            return
        vals = [v for v in cols if v is not None]
        topo = max(max(vals) * 1.1, lim * 1.25, 25)
        larg, alt, n = gx1 - gx0 - 8, gy1 - gy0 - 6, len(cols)

        def yv(v):
            return gy1 - 3 - alt * min(v, topo) / topo

        cv.create_line(gx0 + 4, yv(lim), gx1 - 4, yv(lim), fill="#5b2a3c", dash=(3, 3))
        pts = [(gx0 + 4 + larg * i / (n - 1), yv(v)) for i, v in enumerate(cols) if v is not None]
        if len(pts) >= 2:
            area = [pts[0][0], gy1 - 3] + [c for p in pts for c in p] + [pts[-1][0], gy1 - 3]
            cv.create_polygon(area, fill="#0b3b4a", outline="")
            cv.create_line(*[c for p in pts for c in p], fill=self.CIANO, width=1.5)
        for i, v in enumerate(cols):  # travadas em rosa
            if v is not None and v > lim:
                x = gx0 + 4 + larg * i / (n - 1)
                cv.create_line(x, gy1 - 3, x, yv(v), fill=self.ROSA, width=2)
        cv.create_text(gx0 + 6, gy0 + 8, text="frametime", anchor="w", fill="#4b5b78", font=("Bahnschrift", 7))

    def _tick(self):
        if not self.visivel:
            return
        try:
            d = self.aovivo.amostra()
            self._posicionar(d["pid"])
            self._desenhar(d)
        except Exception:
            pass
        self.top.attributes("-topmost", True)
        self.raiz.after(500, self._tick)

    def _desenhar(self, d):
        cv, W, H = self.cv, self.W, self.H
        cv.delete("all")
        self._arredondado(1, 1, W - 1, H - 1, 18, fill=self.FUNDO, outline=self.BORDA, width=1)
        for i in range(40):  # filete em degradê ciano -> violeta no topo
            x = 18 + (W - 36) * i / 40
            cv.create_line(x, 4, x + (W - 36) / 40 + 1, 4, fill=self._mistura(self.CIANO, self.VIOLETA, i / 39), width=2)

        fps = d["fps"]
        cor = self._cor_fps(fps)
        num = cv.create_text(16, 40, text=str(fps) if fps is not None else "—", anchor="w", fill=cor,
                             font=("Bahnschrift SemiBold", 32))
        fim_num = (cv.bbox(num) or (0, 0, 60, 0))[2]
        cv.create_text(fim_num + 6, 48, text="FPS", anchor="w", fill=self.APAGADO, font=("Bahnschrift", 11))
        low = d["low1"]
        cv.create_text(W - 16, 26, text="1% LOW", anchor="e", fill=self.APAGADO, font=("Bahnschrift", 9))
        cv.create_text(W - 16, 46, text=f"{low:.0f}" if low else "—", anchor="e", fill=self._cor_fps(low), font=("Bahnschrift SemiBold", 18))

        completo = H > 200
        nt = d["travadas"]
        cor_trav = self.VERDE if nt == 0 else self.AMBAR if nt <= 3 else self.ROSA

        # gráfico de frametime (30 s): picos acima da linha tracejada são travadas
        gx0, gx1, gy0, gy1 = 16, W - 16, 64, (106 if completo else 96)
        self._grafico_ft(d, gx0, gy0, gx1, gy1)
        if not completo and d["ft_limite"] is not None:
            cv.create_text(gx1 - 6, gy0 + 8, text=f"⚡{nt}", anchor="e", fill=cor_trav, font=("Bahnschrift SemiBold", 8))

        y = 112
        if completo:
            # linha de alertas: travadas no último minuto e paginação
            cv.create_text(16, 124, text=f"⚡ {nt} travada{'s' if nt != 1 else ''}/min", anchor="w", fill=cor_trav,
                           font=("Bahnschrift SemiBold", 10))
            pag = d["pag_mbs"]
            if pag is None:
                tpag, cpag = "PAG —", self.APAGADO
            else:
                cpag = self.VERDE if pag < 2 else self.AMBAR if pag < 20 else self.ROSA
                tpag = f"PAG {pag:.0f} MB/s" + ("  ok" if pag < 2 else "")
            cv.create_text(W - 16, 124, text=tpag, anchor="e", fill=cpag, font=("Bahnschrift SemiBold", 10))
            cv.create_line(16, 138, W - 16, 138, fill=self.TRILHO)
            y = 152

        self._barra(y, "CPU", d["cpu"], self.VIOLETA, f"{d['cpu']:.0f}%")
        y += 18
        gtemp = f" {d['gpu_temp']:.0f}°" if d.get("gpu_temp") is not None else ""
        self._barra(y, "GPU", d["gpu"], self.CIANO, f"{(d['gpu'] or 0):.0f}%{gtemp}")
        y += 18
        if completo:
            vr, vt = d.get("vram"), d.get("vram_total")
            vpct = vr / vt * 100 if (vr is not None and vt) else None
            cvr = self.AZUL if vpct is None or vpct < 85 else self.AMBAR if vpct < 95 else self.ROSA
            tvr = f"{vr / 1024:.1f}/{vt / 1024:.0f} GB" if vpct is not None else "—"
            self._barra(y, "VRAM", vpct, cvr, tvr)
            y += 18
        corram = self.ROSA if d["ram_pct"] >= 95 else self.AMBAR if d["ram_pct"] >= 85 else self.VERDE
        self._barra(y, "RAM", d["ram_pct"], corram, f"{d['ram_livre']:.1f} GB livre")

        # status
        self.pisca = not self.pisca
        if self.g.ativo:
            t = (self.g.ao_vivo or {}).get("t", 0)
            st, cst = f"●  REC {int(t // 60)}:{int(t % 60):02d}", self.ROSA if self.pisca else "#9f3a4b"
        elif d["foco"] == 0:
            st, cst = "‖  FORA DE FOCO", self.AMBAR
        elif d["jogo"]:
            st, cst = "●  AO VIVO", self.VERDE
        else:
            st, cst = "◌  aguardando jogo…", self.APAGADO
        cv.create_text(16, H - 12, text=st, anchor="w", fill=cst, font=("Bahnschrift SemiBold", 9))
        if d["jogo"]:
            nome = d["jogo"].removesuffix(".exe")[:18]
            cv.create_text(W - 16, H - 12, text=nome, anchor="e", fill=self.APAGADO, font=("Bahnschrift", 9))

    def fechar(self):
        self.visivel = False
        self.aovivo.desligar()


# Processos que a aba Consumo não deixa fechar (derrubariam o Windows ou o próprio monitor).
PROTEGIDOS = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe", "wininit.exe", "services.exe", "lsass.exe",
    "lsaiso.exe", "winlogon.exe", "dwm.exe", "svchost.exe", "memory compression", "secure system", "fontdrvhost.exe",
    "sihost.exe", "ctfmon.exe", "msmpeng.exe", "nissrv.exe", "securityhealthservice.exe", "spoolsv.exe", "audiodg.exe",
    "wudfhost.exe", "explorer.exe", "nvidia-smi.exe", "conhost.exe", "taskhostw.exe", "searchindexer.exe",
}


class Consumo:
    """Coleta o consumo por programa (processos com o mesmo nome somados)."""

    def __init__(self):
        self.procs = {}  # pid -> psutil.Process (para o cpu_percent ter referência)
        self.io_antes = {}  # pid -> (leitura, gravação, horário)
        self.ncpu = psutil.cpu_count() or 1
        self.meu_pid = os.getpid()

    def coletar(self):
        agora = time.time()
        grupos = {}
        vivos = set()
        for p in psutil.process_iter(["pid", "name"]):
            pid = p.info["pid"]
            vivos.add(pid)
            if pid not in self.procs:
                self.procs[pid] = p
                try:
                    p.cpu_percent(None)
                except psutil.Error:
                    pass
            proc = self.procs[pid]
            nome = p.info["name"] or "?"
            try:
                with proc.oneshot():
                    cpu = proc.cpu_percent(None) / self.ncpu
                    mem = proc.memory_info()
                    ws, priv = mem.rss, getattr(mem, "private", mem.vms)
                    try:
                        io = proc.io_counters()
                        ler, grav = io.read_bytes, io.write_bytes
                    except psutil.Error:
                        ler = grav = None
            except psutil.Error:
                continue
            ler_s = grav_s = 0.0
            if ler is not None and pid in self.io_antes:
                l0, g0, t0 = self.io_antes[pid]
                dt_s = max(0.1, agora - t0)
                ler_s, grav_s = max(0, ler - l0) / 2**20 / dt_s, max(0, grav - g0) / 2**20 / dt_s
            if ler is not None:
                self.io_antes[pid] = (ler, grav, agora)
            g = grupos.setdefault(nome.lower(), {"nome": nome, "qtd": 0, "cpu": 0.0, "ram": 0, "priv": 0,
                                                 "ler": 0.0, "grav": 0.0, "pids": []})
            g["qtd"] += 1
            g["cpu"] += cpu
            g["ram"] += ws
            g["priv"] += priv
            g["ler"] += ler_s
            g["grav"] += grav_s
            g["pids"].append(pid)
        for pid in list(self.procs):
            if pid not in vivos:
                self.procs.pop(pid, None)
                self.io_antes.pop(pid, None)
        mem = psutil.virtual_memory()
        try:
            pag = psutil.swap_memory().used / 2**30
        except Exception:
            pag = None
        total = {"cpu": psutil.cpu_percent(None), "ram_pct": mem.percent, "ram_gb": mem.used / 2**30,
                 "ram_total": mem.total / 2**30, "pag_gb": pag}
        return list(grupos.values()), total

    def protegido(self, grupo):
        return grupo["nome"].lower() in PROTEGIDOS or self.meu_pid in grupo["pids"] or \
            grupo["nome"].lower().startswith("presentmon")


def montar_aba_consumo(aba, raiz, messagebox):
    import tkinter as tk
    from tkinter import ttk

    c = Consumo()
    resumo_txt = tk.StringVar(value="Coletando…")
    ttk.Label(aba, textvariable=resumo_txt, font=("Consolas", 10)).pack(anchor="w", pady=(0, 6))

    colunas = [("nome", "Programa", 170, "w"), ("cpu", "CPU %", 60, "e"), ("ram", "RAM em uso", 90, "e"),
               ("priv", "Reservada", 90, "e"), ("disco", "Disco L/G MB/s", 100, "e"), ("qtd", "Proc.", 45, "e")]
    caixa = ttk.Frame(aba)
    caixa.pack(fill="both", expand=True)
    tv = ttk.Treeview(caixa, columns=[k for k, *_ in colunas], show="headings", selectmode="browse")
    for k, titulo, larg, anc in colunas:
        tv.heading(k, text=titulo, command=lambda k=k: ordenar(k))
        tv.column(k, width=larg, anchor=anc, stretch=(k == "nome"))
    rol = ttk.Scrollbar(caixa, orient="vertical", command=tv.yview)
    tv.config(yscrollcommand=rol.set)
    tv.pack(side="left", fill="both", expand=True)
    rol.pack(side="right", fill="y")
    tv.tag_configure("protegido", foreground=TEMA["fraco"])

    barra = ttk.Frame(aba)
    barra.pack(fill="x", pady=(6, 0))
    status = tk.StringVar(value="Selecione um programa para fechar. Cinza = do Windows (protegido).")
    pausado = tk.BooleanVar(value=False)
    ttk.Button(barra, text="✖ Fechar programa", command=lambda: fechar_selecionado()).pack(side="left")
    ttk.Checkbutton(barra, text="Pausar atualização", variable=pausado).pack(side="left", padx=(10, 0))
    ttk.Label(aba, textvariable=status, style="Apagado.TLabel", wraplength=520).pack(anchor="w", pady=(6, 0))

    estado = {"ordem": "ram", "dados": [], "ocupado": False}

    def ordenar(k):
        estado["ordem"] = k
        desenhar()

    def chave(g):
        k = estado["ordem"]
        if k == "nome":
            return g["nome"].lower()
        if k == "disco":
            return -(g["ler"] + g["grav"])
        return -g[k]

    def desenhar():
        sel = tv.selection()
        selecionado = tv.item(sel[0], "values")[0] if sel else None
        tv.delete(*tv.get_children())
        for g in sorted(estado["dados"], key=chave)[:80]:
            vals = (g["nome"], f"{g['cpu']:.1f}", f"{g['ram'] / 2**20:,.0f} MB".replace(",", "."),
                    f"{g['priv'] / 2**20:,.0f} MB".replace(",", "."), f"{g['ler']:.1f} / {g['grav']:.1f}", g["qtd"])
            item = tv.insert("", "end", values=vals, tags=("protegido",) if c.protegido(g) else ())
            if vals[0] == selecionado:
                tv.selection_set(item)

    def coletar_em_thread():
        try:
            dados, total = c.coletar()
        except Exception as e:
            dados, total = None, {"erro": str(e)}
        raiz.after(0, receber, dados, total)

    def receber(dados, total):
        estado["ocupado"] = False
        if dados is None:
            resumo_txt.set(f"Erro ao coletar: {total.get('erro')}")
            return
        estado["dados"] = dados
        pag = f"   Paginação {total['pag_gb']:.1f} GB" if total.get("pag_gb") is not None else ""
        alerta = "   ⚠️ RAM cheia" if total["ram_pct"] >= 95 else ""
        resumo_txt.set(f"CPU {total['cpu']:.0f}%   RAM {total['ram_gb']:.1f}/{total['ram_total']:.1f} GB "
                       f"({total['ram_pct']:.0f}%){pag}{alerta}")
        desenhar()

    def ciclo():
        if not pausado.get() and not estado["ocupado"] and aba.winfo_ismapped():
            estado["ocupado"] = True
            threading.Thread(target=coletar_em_thread, daemon=True).start()
        raiz.after(2000, ciclo)

    def fechar_selecionado():
        sel = tv.selection()
        if not sel:
            status.set("Selecione um programa na lista primeiro.")
            return
        nome = tv.item(sel[0], "values")[0]
        g = next((x for x in estado["dados"] if x["nome"] == nome), None)
        if not g:
            return
        if c.protegido(g):
            messagebox.showinfo("Monitor de Jogos", f"{nome} é do Windows (ou do próprio monitor) e não pode ser fechado por aqui.")
            return
        extra = ""
        if nome.lower() in ("claude.exe", "antigravity ide.exe", "antigravity.exe", "code.exe"):
            extra = "\n\nAtenção: isso também fecha o Claude Code (a conversa fica salva e dá para retomar depois)."
        if not messagebox.askyesno("Fechar programa",
                                   f"Fechar {nome} ({g['qtd']} processo(s), {g['priv'] / 2**30:.1f} GB reservados)?\n\n"
                                   "Trabalho não salvo nesse programa pode ser perdido." + extra):
            return
        procs = []
        for pid in g["pids"]:
            try:
                p = psutil.Process(pid)
                p.terminate()
                procs.append(p)
            except psutil.Error:
                pass
        _, vivos = psutil.wait_procs(procs, timeout=4)
        if vivos and messagebox.askyesno("Fechar programa",
                                         f"{nome} não fechou sozinho ({len(vivos)} processo(s) ainda abertos).\nForçar o fechamento?"):
            for p in vivos:
                try:
                    p.kill()
                except psutil.Error:
                    pass
            _, vivos = psutil.wait_procs(vivos, timeout=3)
        status.set(f"{nome}: fechado." if not vivos else f"{nome}: {len(vivos)} processo(s) ainda abertos.")
        estado["ocupado"] = True
        threading.Thread(target=coletar_em_thread, daemon=True).start()

    ciclo()


# --------------------------------------------------------------------------- tema (mesma paleta do overlay)

TEMA = {"fundo": "#0b1220", "painel": "#111a2e", "campo": "#0d1526", "borda": "#22304d", "trilho": "#1b2742",
        "texto": "#e2e8f0", "apagado": "#7c8aa5", "fraco": "#5d6b86", "selecao": "#164e63",
        "ciano": "#22d3ee", "violeta": "#a78bfa", "verde": "#4ade80", "ambar": "#fbbf24", "rosa": "#fb7185"}


def aplicar_tema(raiz):
    from tkinter import ttk
    T = TEMA
    raiz.configure(bg=T["fundo"])
    st = ttk.Style(raiz)
    st.theme_use("clam")
    st.configure(".", background=T["painel"], foreground=T["texto"], fieldbackground=T["campo"], bordercolor=T["borda"],
                 lightcolor=T["borda"], darkcolor=T["borda"], troughcolor=T["painel"], focuscolor=T["painel"],
                 selectbackground=T["selecao"], selectforeground=T["texto"], insertcolor=T["texto"],
                 arrowcolor=T["apagado"], font=("Bahnschrift", 10))
    st.configure("TLabel", background=T["painel"], foreground=T["texto"])
    st.configure("Apagado.TLabel", foreground=T["apagado"])
    st.configure("Grande.TLabel", font=("Bahnschrift SemiBold", 34), foreground=T["apagado"])
    st.configure("Mono.TLabel", font=("Consolas", 10), foreground=T["texto"])

    st.configure("TNotebook", background=T["fundo"], borderwidth=0, tabmargins=(12, 0, 12, 0))
    st.configure("TNotebook.Tab", background=T["fundo"], foreground=T["apagado"], padding=(18, 8), borderwidth=0,
                 font=("Bahnschrift SemiBold", 10), lightcolor=T["fundo"], bordercolor=T["fundo"])
    st.map("TNotebook.Tab", background=[("selected", T["painel"]), ("active", "#141f36")],
           foreground=[("selected", T["ciano"]), ("active", T["texto"])],
           lightcolor=[("selected", T["painel"])], bordercolor=[("selected", T["painel"])])

    st.configure("TButton", background=T["trilho"], foreground=T["texto"], bordercolor=T["borda"],
                 lightcolor=T["trilho"], darkcolor=T["trilho"], padding=(12, 6), font=("Bahnschrift", 10))
    st.map("TButton", background=[("disabled", T["campo"]), ("pressed", T["borda"]), ("active", "#24345a")],
           foreground=[("disabled", T["fraco"])], lightcolor=[("active", "#24345a")], darkcolor=[("active", "#24345a")])
    for nome, cor, escuro, claro, txt in (("Destaque", T["ciano"], "#0ea5c4", "#67e8f9", "#05232b"),
                                          ("Gravando", T["rosa"], "#e11d48", "#fda4af", "#2a0710")):
        st.configure(f"{nome}.TButton", background=cor, foreground=txt, bordercolor=cor, lightcolor=cor, darkcolor=cor,
                     padding=(12, 10), font=("Bahnschrift SemiBold", 12))
        st.map(f"{nome}.TButton", background=[("disabled", T["trilho"]), ("pressed", escuro), ("active", claro)],
               foreground=[("disabled", T["apagado"])], lightcolor=[("active", claro), ("disabled", T["trilho"])],
               darkcolor=[("active", claro), ("disabled", T["trilho"])], bordercolor=[("disabled", T["trilho"])])

    st.configure("TCheckbutton", background=T["painel"], foreground=T["texto"], indicatorbackground=T["campo"],
                 indicatorforeground=T["fundo"], upperbordercolor=T["borda"], lowerbordercolor=T["borda"], padding=(0, 3))
    st.map("TCheckbutton", background=[("active", T["painel"])], foreground=[("active", T["ciano"])],
           indicatorbackground=[("selected", T["ciano"]), ("active", T["trilho"])])

    st.configure("TEntry", fieldbackground=T["campo"], foreground=T["texto"], padding=7, bordercolor=T["borda"])
    st.map("TEntry", bordercolor=[("focus", T["ciano"])], lightcolor=[("focus", T["ciano"])])
    st.configure("TCombobox", fieldbackground=T["campo"], background=T["trilho"], foreground=T["texto"],
                 arrowcolor=T["ciano"], padding=4)
    st.map("TCombobox", fieldbackground=[("readonly", T["campo"])], foreground=[("readonly", T["texto"])],
           selectbackground=[("readonly", T["campo"])], selectforeground=[("readonly", T["texto"])],
           background=[("active", T["borda"])])
    raiz.option_add("*TCombobox*Listbox.background", T["campo"])
    raiz.option_add("*TCombobox*Listbox.foreground", T["texto"])
    raiz.option_add("*TCombobox*Listbox.selectBackground", T["selecao"])
    raiz.option_add("*TCombobox*Listbox.selectForeground", T["ciano"])
    raiz.option_add("*TCombobox*Listbox.font", ("Bahnschrift", 10))

    st.configure("Treeview", background=T["campo"], fieldbackground=T["campo"], foreground=T["texto"], rowheight=24,
                 bordercolor=T["borda"], font=("Bahnschrift", 10))
    st.map("Treeview", background=[("selected", T["selecao"])], foreground=[("selected", T["ciano"])])
    st.configure("Treeview.Heading", background=T["fundo"], foreground=T["apagado"], bordercolor=T["borda"],
                 lightcolor=T["fundo"], darkcolor=T["fundo"], font=("Bahnschrift SemiBold", 9), padding=(6, 5))
    st.map("Treeview.Heading", background=[("active", T["trilho"])], foreground=[("active", T["ciano"])])

    st.configure("Vertical.TScrollbar", background=T["trilho"], troughcolor=T["campo"], bordercolor=T["campo"],
                 lightcolor=T["trilho"], darkcolor=T["trilho"], arrowcolor=T["apagado"], gripcount=0)
    st.map("Vertical.TScrollbar", background=[("active", T["borda"])])


def barra_de_titulo_escura(janela):
    """Barra de título escura no Windows 11 (sem efeito em versões que não suportam)."""
    try:
        janela.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(janela.winfo_id())
        dwm = ctypes.windll.dwmapi
        for atributo, valor in ((20, 1), (35, 0x0020120B), (34, 0x004D3022)):  # modo escuro, cor da barra, cor da borda
            v = ctypes.c_int(valor)
            dwm.DwmSetWindowAttribute(hwnd, atributo, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        pass


def abrir_janela():
    import tkinter as tk
    from tkinter import ttk, messagebox

    try:  # ícone próprio na barra de tarefas (senão o Windows mostra o do Python)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Kevin.MonitorDeJogos")
    except Exception:
        pass
    g = Gravador()
    raiz = tk.Tk()
    raiz.title("Monitor de Jogos")
    raiz.geometry("600x780")
    raiz.minsize(560, 640)
    raiz.attributes("-topmost", False)
    if (PASTA / "icone.ico").exists():
        try:
            raiz.iconbitmap(default=str(PASTA / "icone.ico"))
        except Exception:
            pass
    aplicar_tema(raiz)
    barra_de_titulo_escura(raiz)

    # cabeçalho: ícone + título + filete em degradê (igual ao overlay)
    cab = tk.Canvas(raiz, height=64, bg=TEMA["fundo"], highlightthickness=0)
    cab.pack(fill="x")
    try:
        from PIL import Image, ImageTk
        raiz._icone_cab = ImageTk.PhotoImage(Image.open(PASTA / "icone.png").resize((36, 36), Image.LANCZOS))
        cab.create_image(16, 32, image=raiz._icone_cab, anchor="w")
    except Exception:
        pass
    cab.create_text(62, 26, text="Monitor de Jogos", anchor="w", fill=TEMA["texto"], font=("Bahnschrift SemiBold", 16))
    cab.create_text(62, 46, text="FPS · frametime · recursos", anchor="w", fill=TEMA["fraco"], font=("Bahnschrift", 9))
    for i in range(30):
        x = 62 + 150 * i / 30
        cab.create_line(x, 58, x + 6, 58, width=2, fill=Overlay._mistura(TEMA["ciano"], TEMA["violeta"], i / 29))

    abas = ttk.Notebook(raiz)
    abas.pack(fill="both", expand=True)
    quadro = ttk.Frame(abas, padding=16)
    aba_consumo = ttk.Frame(abas, padding=16)
    abas.add(quadro, text="  Gravação  ")
    abas.add(aba_consumo, text="  Consumo  ")
    montar_aba_consumo(aba_consumo, raiz, messagebox)

    ttk.Label(quadro, text="NOME DO TESTE  (ex.: Tekken 8 - FORESEE)", style="Apagado.TLabel",
              font=("Bahnschrift SemiBold", 9)).pack(anchor="w")
    nome = tk.StringVar(value="")
    ttk.Entry(quadro, textvariable=nome, font=("Bahnschrift", 11)).pack(fill="x", pady=(4, 12))

    fps_txt = tk.StringVar(value="— FPS")
    fps_lbl = ttk.Label(quadro, textvariable=fps_txt, style="Grande.TLabel")
    fps_lbl.pack(anchor="w")
    info_txt = tk.StringVar(value="Parado. Ctrl+Alt+F10 inicia/para de dentro do jogo.")
    ttk.Label(quadro, textvariable=info_txt, justify="left", style="Mono.TLabel").pack(anchor="w", pady=(2, 12))

    bt = ttk.Button(quadro, text="▶  Iniciar gravação", style="Destaque.TButton", cursor="hand2")
    bt.pack(fill="x")
    abrir_sozinho = tk.BooleanVar(value=False)
    ttk.Checkbutton(quadro, text="Abrir o relatório sozinho ao parar (rouba o foco do jogo)",
                    variable=abrir_sozinho).pack(anchor="w", pady=(8, 0))
    topo = tk.BooleanVar(value=False)
    ttk.Checkbutton(quadro, text="Janela sempre por cima", variable=topo,
                    command=lambda: raiz.attributes("-topmost", topo.get())).pack(anchor="w")

    # overlay por cima do jogo
    linha_ov = ttk.Frame(quadro)
    linha_ov.pack(fill="x", pady=(2, 0))
    canto = tk.StringVar(value="Superior esquerdo")
    tamanho = tk.StringVar(value="Completo")
    overlay = Overlay(raiz, AoVivo(g.gpu), g, canto, tamanho)
    ov_ligado = tk.BooleanVar(value=False)

    def alternar_overlay(_via_atalho=False):
        if _via_atalho:
            ov_ligado.set(not ov_ligado.get())
        if ov_ligado.get() != overlay.visivel:
            overlay.alternar()

    ttk.Checkbutton(linha_ov, text="Overlay no jogo", variable=ov_ligado,
                    command=alternar_overlay).pack(side="left")
    ttk.Combobox(linha_ov, textvariable=canto, state="readonly", width=16,
                 values=["Superior esquerdo", "Superior direito", "Inferior esquerdo", "Inferior direito"]).pack(side="left", padx=(8, 4))
    ttk.Combobox(linha_ov, textvariable=tamanho, state="readonly", width=10,
                 values=["Compacto", "Completo"]).pack(side="left", padx=(0, 6))
    ov_atalho_txt = tk.StringVar(value="")
    ttk.Label(linha_ov, textvariable=ov_atalho_txt, style="Apagado.TLabel").pack(side="left")

    # lista das sessões: clique duplo abre o relatório
    ttk.Label(quadro, text="SESSÕES GRAVADAS  (clique duplo para abrir o relatório)", style="Apagado.TLabel",
              font=("Bahnschrift SemiBold", 9)).pack(anchor="w", pady=(14, 4))
    caixa = ttk.Frame(quadro)
    caixa.pack(fill="both", expand=True)
    lista = tk.Listbox(caixa, height=9, font=("Consolas", 10), activestyle="none", bg=TEMA["campo"], fg=TEMA["texto"],
                       selectbackground=TEMA["selecao"], selectforeground=TEMA["ciano"], highlightthickness=1,
                       highlightbackground=TEMA["borda"], highlightcolor=TEMA["borda"], bd=0, relief="flat")
    rolagem = ttk.Scrollbar(caixa, orient="vertical", command=lista.yview)
    lista.config(yscrollcommand=rolagem.set)
    lista.pack(side="left", fill="both", expand=True)
    rolagem.pack(side="right", fill="y")
    pastas_lista = []

    acoes = ttk.Frame(quadro)
    acoes.pack(fill="x", pady=(6, 0))

    def _abrir(caminho):
        if caminho and caminho.exists():
            webbrowser.open(caminho.as_uri())
        else:
            messagebox.showinfo("Monitor de Jogos", "Relatório não encontrado.")

    def atualizar_lista():
        lista.delete(0, "end")
        pastas_lista.clear()
        for p in sorted(SESSOES.glob("*/resumo.json"), reverse=True):
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            quando = r.get("inicio", "")[5:16].replace("T", " ").replace("-", "/")
            fps = f"{r['fps_medio']:.0f} FPS" if r.get("fps_medio") else "sem FPS"
            lista.insert("end", f"{quando}  {r.get('nome', '')[:28]:<28} {fps:>8}")
            pastas_lista.append(p.parent)

    def abrir_selecionada(_evento=None):
        sel = lista.curselection()
        if sel:
            _abrir(pastas_lista[sel[0]] / "relatorio.html")

    lista.bind("<Double-Button-1>", abrir_selecionada)
    lista.bind("<Return>", abrir_selecionada)
    ttk.Button(acoes, text="Abrir selecionada", command=abrir_selecionada).pack(side="left", fill="x", expand=True)
    ttk.Button(acoes, text="Comparativo", command=lambda: _abrir(SESSOES / "comparativo.html")).pack(side="left", padx=(6, 0))
    ttk.Button(acoes, text="Pasta", command=lambda: os.startfile(SESSOES)).pack(side="left", padx=(6, 0))

    def terminou(rel, erro):
        bt.config(text="▶  Iniciar gravação", state="normal", style="Destaque.TButton")
        fps_txt.set("— FPS")
        fps_lbl.configure(foreground=TEMA["apagado"])
        atualizar_lista()
        if erro:
            info_txt.set(f"Erro ao gerar o relatório:\n{erro}\nOs dados brutos estão na pasta da sessão.")
            return
        info_txt.set(f"✔ Relatório pronto: {rel.parent.name}\nClique duas vezes na sessão abaixo para abrir.")
        if lista.size():
            lista.selection_clear(0, "end")
            lista.selection_set(0)
        if abrir_sozinho.get():
            _abrir(rel)

    def parar_em_segundo_plano():
        """Gera o relatório numa thread, com o processo em prioridade baixa, para não atrapalhar o jogo."""
        eu = psutil.Process()
        antes = eu.nice()
        try:
            eu.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        except Exception:
            pass
        rel, erro = None, None
        try:
            rel = g.parar()
        except Exception as e:
            erro = e
        finally:
            try:
                eu.nice(antes)
            except Exception:
                pass
        raiz.after(0, terminou, rel, erro)

    def bipe(tipo):
        """Aviso sonoro para quem está dentro do jogo: 1 agudo = começou, 2 graves = parou."""
        try:
            import winsound
            if tipo == "inicio":
                winsound.Beep(1200, 150)
            else:
                winsound.Beep(500, 150)
                winsound.Beep(500, 150)
        except Exception:
            pass

    def alternar():
        if str(bt["state"]) == "disabled":
            return  # ainda gerando o relatório anterior
        if not g.ativo:
            g.iniciar(nome.get())
            bt.config(text="■  Parar e gerar relatório", style="Gravando.TButton")
            bipe("inicio")
        else:
            bt.config(text="…  gerando relatório (pode continuar jogando)", state="disabled", style="Destaque.TButton")
            bipe("fim")
            threading.Thread(target=parar_em_segundo_plano, daemon=True).start()

    bt.config(command=alternar)
    atualizar_lista()

    # o atalho roda em outra thread: ele só deixa recados nesta fila, e a janela os pega no tick()
    import queue
    recados = queue.Queue()
    atalho_txt = tk.StringVar(value="Atalho: registrando…")
    ttk.Label(quadro, textvariable=atalho_txt, style="Apagado.TLabel").pack(anchor="w", pady=(8, 0))

    def tick():
        while True:
            try:
                tipo, valor = recados.get_nowait()
            except queue.Empty:
                break
            if tipo == "apertou":
                alternar()
            elif tipo == "registrado":
                atalho_txt.set(f"Atalho para iniciar/parar de dentro do jogo: {valor}  (bipes confirmam)")
                if not g.ativo and info_txt.get().startswith("Parado"):
                    info_txt.set(f"Parado. {valor} inicia/para de dentro do jogo.")
            elif tipo == "falhou":
                atalho_txt.set("⚠️ Nenhum atalho disponível (outro programa está usando). Use o botão.")
            elif tipo == "overlay":
                alternar_overlay(_via_atalho=True)
            elif tipo == "overlay_registrado":
                ov_atalho_txt.set(f"({valor})")
        v = g.ao_vivo if g.ativo else {}
        if g.ativo and v:
            gp = v.get("gpu", {})
            fora = v.get("foco") == 0
            fps_txt.set(f"{v['fps'] if v.get('fps') is not None else '—'} FPS" + ("  ‖ fora de foco" if fora else ""))
            fps_lbl.configure(foreground=TEMA["ambar"] if fora else overlay._cor_fps(v.get("fps")))
            linhas = [f"● GRAVANDO  {int(v['t']//60)}:{int(v['t']%60):02d}   jogo: {v.get('jogo') or 'detectando…'}",
                      ("⏸ jogo fora de foco: este trecho não entra nas estatísticas" if fora else "▶ jogo em foco"),
                      f"CPU {v['cpu']:>5.0f}%   RAM {v['ram']:>3.0f}%",
                      f"GPU {gp.get('gpu_uso') or 0:>5.0f}%   {gp.get('gpu_temp') or 0:.0f} °C   {gp.get('gpu_watts') or 0:.0f} W"]
            for d, (ler, grav, ativo) in v.get("discos", {}).items():
                linhas.append(f"{g.discos.get(d, d)[:18]:<18} L {ler:>6.0f} G {grav:>6.0f} MB/s")
            info_txt.set("\n".join(linhas))
        raiz.after(200, tick)

    # atalho global: tenta Ctrl+Alt+F10; se outro programa já usa, tenta os seguintes
    def escutar_atalho():
        user32 = ctypes.windll.user32
        ALT, CTRL, SHIFT, NOREPEAT = 0x0001, 0x0002, 0x0004, 0x4000
        opcoes = [(CTRL | ALT, 0x79, "Ctrl+Alt+F10"), (CTRL | SHIFT, 0x79, "Ctrl+Shift+F10"),
                  (CTRL | ALT, 0x78, "Ctrl+Alt+F9"), (CTRL | SHIFT, 0x78, "Ctrl+Shift+F9")]
        for mods, vk, nome_atalho in opcoes:
            if user32.RegisterHotKey(None, 1, mods | NOREPEAT, vk):
                recados.put(("registrado", nome_atalho))
                break
        else:
            recados.put(("falhou", None))
        # atalho 2: mostra/esconde o overlay
        for mods, vk, nome_atalho in [(CTRL | ALT, 0x7A, "Ctrl+Alt+F11"), (CTRL | SHIFT, 0x7A, "Ctrl+Shift+F11")]:
            if user32.RegisterHotKey(None, 2, mods | NOREPEAT, vk):
                recados.put(("overlay_registrado", nome_atalho))
                break
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                recados.put(("overlay" if msg.wParam == 2 else "apertou", None))

    threading.Thread(target=escutar_atalho, daemon=True).start()

    def fechar():
        if g.ativo and not messagebox.askyesno("Monitor de Jogos", "Está gravando. Parar e gerar o relatório antes de sair?"):
            return
        overlay.fechar()
        g.fechar()
        raiz.destroy()

    raiz.protocol("WM_DELETE_WINDOW", fechar)
    tick()
    raiz.mainloop()


def main():
    SESSOES.mkdir(exist_ok=True)
    if not PRESENTMON:
        ctypes.windll.user32.MessageBoxW(
            None, "PresentMon não encontrado.\n\nBaixe o PresentMon-2.x.x-x64.exe em\n"
            "github.com/GameTechDev/PresentMon/releases\ne salve na pasta:\n" + str(PASTA / "bin"),
            "Monitor de Jogos", 0x10)
        return
    if not eh_admin():
        exe = sys.executable.replace("python.exe", "pythonw.exe")
        params = " ".join(f'"{a}"' for a in [str(Path(__file__).resolve())] + sys.argv[1:])
        ctypes.windll.shell32.ShellExecuteW(None, "runas", exe if "--teste" not in sys.argv else sys.executable, params, str(PASTA), 1)
        return
    if "--teste" in sys.argv:
        seg = int(sys.argv[sys.argv.index("--teste") + 1])
        nome = sys.argv[sys.argv.index("--nome") + 1] if "--nome" in sys.argv else "Teste rapido"
        g = Gravador()
        g.iniciar(nome)
        time.sleep(seg)
        rel = g.parar()
        g.fechar()
        print(rel)
        return
    abrir_janela()


if __name__ == "__main__":
    main()
