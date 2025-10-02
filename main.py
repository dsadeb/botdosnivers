from keep_alive import keep_alive
from dotenv import load_dotenv
import os
import discord
from discord.ext import tasks, commands
from datetime import datetime, date
import pytz
import json
import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError

# ========= Config e Credenciais =========
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")  # token do botdosnivers
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
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

def _env_ok():
    faltando = []
    if not BOT_TOKEN: faltando.append("BOT_TOKEN")
    if not DISCORD_CHANNEL_ID: faltando.append("DISCORD_CHANNEL_ID")
    if not GOOGLE_SHEET_ID: faltando.append("GOOGLE_SHEET_ID")
    if not GOOGLE_SERVICE_ACCOUNT_JSON: faltando.append("GOOGLE_SERVICE_ACCOUNT_JSON")
    return faltando

def _sa_email():
    """Retorna o e-mail da Service Account (suporta B64 ou JSON) para mensagens de diagnóstico."""
    try:
        b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64")
        if b64:
            import base64, json as _json
            raw = base64.b64decode(b64)
            d = _json.loads(raw)
        else:
            d = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON or "{}")
        return d.get("client_email") or "(sem client_email no JSON)"
    except Exception:
        return "(falha ao ler credenciais)"

gc = None  # inicializado no on_ready

def build_gspread_client():
    """Monta o cliente do Google Sheets.
    Dá preferência à credencial em base64 (GOOGLE_SERVICE_ACCOUNT_B64) para evitar problema de \n."""
    b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64")
    if b64:
        import base64, json as _json
        raw = base64.b64decode(b64)
        creds_dict = _json.loads(raw)
    else:
        # fallback: JSON em uma linha na env GOOGLE_SERVICE_ACCOUNT_JSON
        creds_dict = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


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
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
    except APIError as e:
        # PERMISSION_DENIED ou NOT_FOUND, etc.
        motivo = ""
        try:
            motivo = e.response.json().get("error", {}).get("message", "")
        except Exception:
            pass
        raise RuntimeError(
            f"🚨 Erro ao abrir a planilha {GOOGLE_SHEET_ID}. "
            f"Verifique se ela existe e se está **compartilhada como Leitor** com: `{_sa_email()}`. "
            f"Detalhe da API: {motivo or e}"
        )
    except Exception as e:
        raise RuntimeError(f"🚨 Falha inesperada ao abrir a planilha: {e}")

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
        raise RuntimeError(f"🚨 Erro ao ler a aba '{GOOGLE_SHEET_TAB}': {e}")

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
    global gc
    try:
        gc = build_gspread_client()
    except Exception as e:
        print(f"🚨 Falha ao construir cliente Google (JSON inválido?): {e}")
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
        linhas.append(f"{bolo}{confete} **Hoje tem niver!** Parabéns {', '.join(nomes_nao)}! {confete}{bolo}")

    try:
        await channel.send("\n".join(linhas))
        _last_announce_date = hoje_date_key
        print(f"Anúncio de aniversários enviado para o canal {DISCORD_CHANNEL_ID}")
    except Exception as e:
        print(f"[ERRO] Falha ao enviar mensagem no canal {DISCORD_CHANNEL_ID}: {e}")

# ======== Comandos ========

@bot.command(name="testniver")
async def testniver(ctx):
    """Mostra o último e o próximo aniversário com base na planilha."""
    hoje = datetime.now(TZ).date()
    try:
        rows = fetch_birthdays_rows()
    except Exception as e:
        await ctx.reply(str(e))
        return

    last_date, last_names, next_date, next_names = _last_and_next_birthdays(rows, hoje)

    if not last_date and not next_date:
        await ctx.reply("Não encontrei aniversários válidos na planilha.")
        return

    def fmt(dt: date): return dt.strftime("%d/%m/%Y")

    linhas = ["🎂 **Aniversários (teste)**"]
    if last_date:
        dias = (hoje - last_date).days
        quando = "hoje" if dias == 0 else (f"há {dias} dia" + ("s" if dias != 1 else ""))
        linhas.append(f"• **Último:** {fmt(last_date)} — {', '.join(last_names)} ({quando})")
    if next_date:
        dias = (next_date - hoje).days
        quando = "hoje" if dias == 0 else (f"em {dias} dia" + ("s" if dias != 1 else ""))
        linhas.append(f"• **Próximo:** {fmt(next_date)} — {', '.join(next_names)} ({quando})")

    await ctx.reply("\n".join(linhas))

@bot.command(name="proximos")
async def proximos(ctx, dias: int = 30):
    """Lista próximos aniversários em N dias (padrão 30)."""
    hoje = datetime.now(TZ).date()
    try:
        rows = fetch_birthdays_rows()
    except Exception as e:
        await ctx.reply(str(e))
        return

    futuros = []
    for r in rows:
        dm = parse_day_month(r["data"])
        if not dm: continue
        d, m = dm
        ano_ref = hoje.year
        data_ref = _safe_date(ano_ref, m, d)
        if data_ref is None or data_ref < hoje:
            data_ref = _safe_date(ano_ref + 1, m, d)
        if data_ref is None: continue
        delta = (data_ref - hoje).days
        if 0 <= delta <= dias:
            futuros.append((delta, r["nome"], data_ref.strftime("%d/%m/%Y")))
    futuros.sort(key=lambda x: x[0])

    if not futuros:
        await ctx.reply(f"Ninguém faz aniversário nos próximos {dias} dias.")
        return

    linhas = [f"🎈 **Próximos aniversários (≤ {dias} dias):**"]
    for delta, nome, data_fmt in futuros:
        quando = "hoje" if delta == 0 else (f"em {delta} dias")
        linhas.append(f"• {data_fmt} — {nome} ({quando})")
    await ctx.reply("\n".join(linhas))

@bot.command(name="checknivers")
async def checknivers(ctx):
    """Mostra diagnóstico de configuração e acesso ao Sheets."""
    faltando = _env_ok()
    status_env = "✅" if not faltando else f"🚨 faltando: {', '.join(faltando)}"
    sa = _sa_email()

    sheets_ok = "❔"
    try:
        rows = fetch_birthdays_rows()
        sheets_ok = f"✅ acesso OK (linhas: {len(rows)}, aba: {GOOGLE_SHEET_TAB})"
    except Exception as e:
        sheets_ok = f"🚨 {e}"

    canal = bot.get_channel(DISCORD_CHANNEL_ID)
    canal_ok = "✅" if canal else "🚨 canal não encontrado"

    msg = [
        "🔎 **Diagnóstico botdosnivers**",
        f"• Env vars: {status_env}",
        f"• Service Account: `{sa}`",
        f"• Google Sheet ID: `{GOOGLE_SHEET_ID or '(vazio)'}` | Aba: `{GOOGLE_SHEET_TAB}`",
        f"• Sheets: {sheets_ok}",
        f"• Canal (ID {DISCORD_CHANNEL_ID}): {canal_ok}",
        "→ Se for PERMISSION_DENIED, compartilhe a planilha como **Leitor** com o e-mail da Service Account acima."
    ]
    await ctx.reply("\n".join(msg))

# ========= Bootstrap =========
def main():
    keep_alive()
    bot.run(BOT_TOKEN)

if __name__ == "__main__":
    main()


