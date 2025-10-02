from keep_alive import keep_alive
from dotenv import load_dotenv
import os
import discord
from discord.ext import tasks, commands
from datetime import datetime, date
import pytz
import json
import base64
import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError
from google.auth.transport.requests import Request

# ========= Config e Credenciais =========
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")  # token do botdosnivers
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
GOOGLE_SHEET_ID = (os.getenv("GOOGLE_SHEET_ID") or "").strip()
GOOGLE_SHEET_TAB = os.getenv("GOOGLE_SHEET_TAB", "Aniversários")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

# ========= Fuso horário =========
TZ = pytz.timezone("America/Sao_Paulo")

# ========= Discord Intents / Bot =========
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ========= Helpers de credencial/Sheets =========
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

# cliente gspread global
gc = None

def load_sa_creds():
    """
    Carrega a credencial da Service Account a partir de GOOGLE_SERVICE_ACCOUNT_B64 (preferido)
    ou GOOGLE_SERVICE_ACCOUNT_JSON (fallback). Retorna (src, creds_dict, email, key_id).
    """
    b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64")
    if b64:
        raw = base64.b64decode(b64)
        data = json.loads(raw)
        src = "B64"
    else:
        if not GOOGLE_SERVICE_ACCOUNT_JSON:
            raise RuntimeError("Nenhuma credencial encontrada (B64 ou JSON).")
        data = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        src = "JSON"

    email = data.get("client_email")
    key_id = data.get("private_key_id")
    if not email or not data.get("private_key"):
        raise RuntimeError("Credencial inválida: falta client_email ou private_key.")
    return src, data, email, key_id


def _env_ok():
    faltando = []
    if not BOT_TOKEN: faltando.append("BOT_TOKEN")
    if not DISCORD_CHANNEL_ID: faltando.append("DISCORD_CHANNEL_ID")
    if not GOOGLE_SHEET_ID: faltando.append("GOOGLE_SHEET_ID")
    if not (os.getenv("GOOGLE_SERVICE_ACCOUNT_B64") or GOOGLE_SERVICE_ACCOUNT_JSON):
        faltando.append("GOOGLE_SERVICE_ACCOUNT_B64 ou GOOGLE_SERVICE_ACCOUNT_JSON")
    return faltando


def _sa_email():
    try:
        _, _, email, _ = load_sa_creds()
        return email or "(sem client_email)"
    except Exception:
        return "(falha ao ler credenciais)"


def build_gspread_client():
    src, data, email, kid = load_sa_creds()
    # Log útil no console na subida:
    print(f"[Credencial] origem={src} | SA={email} | key_id={kid}")
    creds = Credentials.from_service_account_info(data, scopes=SCOPES)
    return gspread.authorize(creds)


def _ensure_gc():
    """Garante que o cliente gspread esteja construído."""
    global gc
    if gc is None:
        gc = build_gspread_client()
    return gc


def _safe_date(y: int, m: int, d: int):
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_day_month(date_str: str):
    date_str = str(date_str or "").strip()
    parts = date_str.split("/")
    if len(parts) < 2:
        return None
    try:
        dia = int(parts[0]); mes = int(parts[1])
        if not (1 <= dia <= 31 and 1 <= mes <= 12):
            return None
        return (dia, mes)
    except:
        return None


def fetch_birthdays_rows():
    """Lê linhas da aba e retorna [{'nome':..., 'data':...}, ...] com tratamento de erros claro."""
    try:
        client = _ensure_gc()
        sh = client.open_by_key(GOOGLE_SHEET_ID.strip())
    except APIError as e:
        # PERMISSION_DENIED, NOT_FOUND, API disabled, etc.
        detail = ""
        try:
            detail = e.response.json().get("error", {}).get("message", "")
        except Exception:
            detail = repr(e)
        raise RuntimeError(
            f"🚨 Erro ao abrir a planilha `{GOOGLE_SHEET_ID}`. "
            f"Confira o ID e se está **compartilhada (Leitor)** com `{_sa_email()}`. "
            f"Detalhe da API: {detail}"
        )
    except AttributeError:
        raise RuntimeError("🚨 Cliente do Google Sheets não inicializado (gc=None).")
    except Exception as e:
        raise RuntimeError(f"🚨 Falha inesperada ao abrir a planilha: {repr(e)}")

    try:
        ws = sh.worksheet(GOOGLE_SHEET_TAB)
    except gspread.exceptions.WorksheetNotFound:
        raise RuntimeError(
            f"🚨 Aba '{GOOGLE_SHEET_TAB}' não encontrada. "
            "Confira o nome exato da guia/aba no Google Sheets ou ajuste a env GOOGLE_SHEET_TAB."
        )

    try:
        rows = ws.get_all_records()  # primeira linha como header
    except APIError as e:
        detail = ""
        try:
            detail = e.response.json().get("error", {}).get("message", "")
        except Exception:
            detail = repr(e)
        raise RuntimeError(f"🚨 Erro ao ler a aba '{GOOGLE_SHEET_TAB}': {detail}")

    normalized = []
    for r in rows:
        nome = r.get("Nome") or r.get("DiscordName") or r.get("Pessoa") or ""
        data = r.get("Data") or r.get("Aniversário") or r.get("Aniversario") or r.get("Nascimento") or ""
        if nome and data:
            normalized.append({"nome": str(nome).strip(), "data": str(data).strip()})
    return normalized


def find_today_birthdays():
    hoje = datetime.now(TZ)
    d, m = hoje.day, hoje.month
    aniversariantes = []
    for row in fetch_birthdays_rows():
        dm = parse_day_month(row["data"])
        if dm and dm[0] == d and dm[1] == m:
            aniversariantes.append(row["nome"])
    return aniversariantes


async def match_member_by_name(guild: discord.Guild, alvo_nome: str):
    alvo_norm = alvo_nome.casefold().replace(" ", "")
    for m in guild.members:
        name_norm = m.display_name.casefold().replace(" ", "")
        if alvo_norm in name_norm or name_norm in alvo_norm:
            return m
    return None


def _last_and_next_birthdays(rows, today: date):
    past = []
    future = []
    for r in rows:
        dm = parse_day_month(r["data"])
        if not dm:
            continue
        d, m = dm
        this_year = _safe_date(today.year, m, d)
        if this_year is None:
            # busca próximo e anterior válidos (ex.: 29/02)
            ny = today.year + 1
            next_occ = None
            for k in range(0, 4):
                next_occ = _safe_date(ny + k, m, d)
                if next_occ: break
            py = today.year - 1
            prev_occ = None
            for k in range(0, 4):
                prev_occ = _safe_date(py - k, m, d)
                if prev_occ: break
        else:
            if this_year >= today:
                next_occ = this_year
                prev_occ = _safe_date(today.year - 1, m, d)
            else:
                next_occ = _safe_date(today.year + 1, m, d)
                prev_occ = this_year

        if prev_occ: past.append((prev_occ, r["nome"]))
        if next_occ: future.append((next_occ, r["nome"]))

    def group_by_date(pairs):
        by = {}
        for dt, nome in pairs:
            by.setdefault(dt, []).append(nome)
        return by

    past_by = group_by_date(past)
    future_by = group_by_date(future)

    last_date = max(past_by.keys()) if past_by else None
    next_date = min(future_by.keys()) if future_by else None

    last_names = past_by.get(last_date, []) if last_date else []
    next_names = future_by.get(next_date, []) if next_date else []

    return last_date, last_names, next_date, next_names


# Evita postagens duplicadas se o bot reiniciar no mesmo minuto
_last_announce_date = None


async def _warmup_and_diagnose():
    """Roda checagens e imprime diagnósticos no console."""
    faltando = _env_ok()
    if faltando:
        print("🚨 Variáveis de ambiente faltando:", ", ".join(faltando))
        return False

    # constrói gspread e testa acesso
    try:
        _ensure_gc()
    except Exception as e:
        print(f"🚨 Falha ao construir cliente Google (JSON/B64 inválido?): {e}")
        return False

    try:
        rows = fetch_birthdays_rows()
        print(f"✅ Sheets OK. Linhas lidas: {len(rows)} | Aba: {GOOGLE_SHEET_TAB}")
        return True
    except Exception as e:
        print(str(e))
        return False


@bot.event
async def on_ready():
    print(f"botdosnivers conectado como {bot.user}")
    ok = await _warmup_and_diagnose()
    if not ok:
        print("⚠️ O bot iniciou, mas há problemas de configuração. Use !checknivers para ver detalhes no Discord.")
    anunciar_aniversarios.start()


@tasks.loop(minutes=1)
async def anunciar_aniversarios():
    global _last_announce_date
    agora = datetime.now(TZ)
    if not (agora.hour == 9 and agora.minute == 0):
        return

    hoje_date_key = agora.strftime("%Y-%m-%d")
    if _last_announce_date == hoje_date_key:
        return

    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if channel is None:
        print(f"[ERRO] Canal {DISCORD_CHANNEL_ID} não encontrado. Verifique o ID e as permissões do bot.")
        return

    try:
        aniversariantes = find_today_birthdays()
    except Exception as e:
        await channel.send(
            f"🚨 Não consegui ler a planilha de aniversários.\n{e}\n"
            f"• Compartilhe o Sheets com `{_sa_email()}` (Leitor)\n"
            f"• Confirme a aba: **{GOOGLE_SHEET_TAB}**"
        )
        _last_announce_date = hoje_date_key
        return

    if not aniversariantes:
        print("Nenhum aniversário hoje. (ok)")
        _last_announce_date = hoje_date_key
        return

    mentions, nomes_nao = [], []
    for nome in aniversariantes:
        mencionado = False
        for g in bot.guilds:
            m = await match_member_by_name(g, nome)
            if m:
                mentions.append(m.mention)
                mencionado = True
                break
        if not mencionado:
            nomes_nao.append(nome)

    bolo, confete = "🎂", "🎉"
    linhas = []
    if mentions:
        linhas.append(f"{bolo}{confete} **Hoje tem niver!** Parabéns {', '.join(mentions)}! {confete}{bolo}")
    if nomes_nao:
        linhas.append(f"{bolo}{


