from keep_alive import keep_alive
from dotenv import load_dotenv
import os
import discord
from discord.ext import tasks, commands
from datetime import datetime
import pytz
import json
import gspread
from google.oauth2.service_account import Credentials

# ========= Config e Credenciais =========
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")  # token do botdosnivers
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_SHEET_TAB = os.getenv("GOOGLE_SHEET_TAB", "Aniversários")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

if not (BOT_TOKEN and DISCORD_CHANNEL_ID and GOOGLE_SHEET_ID and GOOGLE_SERVICE_ACCOUNT_JSON):
    raise RuntimeError("Faltam variáveis de ambiente obrigatórias: "
                       "BOT_TOKEN, DISCORD_CHANNEL_ID, GOOGLE_SHEET_ID, GOOGLE_SERVICE_ACCOUNT_JSON")

# ========= Fuso horário =========
TZ = pytz.timezone("America/Sao_Paulo")

# ========= Discord Intents / Bot =========
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ========= Google Sheets Client =========
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

def build_gspread_client():
    creds_dict = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)

gc = build_gspread_client()

def fetch_birthdays_rows():
    """Lê linhas da aba configurada e retorna uma lista de dicts com chaves normalizadas."""
    sh = gc.open_by_key(GOOGLE_SHEET_ID)
    ws = sh.worksheet(GOOGLE_SHEET_TAB)

    rows = ws.get_all_records()  # primeira linha como header
    normalized = []
    for r in rows:
        # normaliza possíveis nomes de coluna
        nome = r.get("Nome") or r.get("DiscordName") or r.get("Pessoa") or ""
        data = r.get("Data") or r.get("Aniversário") or r.get("Aniversario") or r.get("Nascimento") or ""
        if nome and data:
            normalized.append({"nome": str(nome).strip(), "data": str(data).strip()})
    return normalized

def parse_day_month(date_str: str):
    """Aceita 'DD/MM' ou 'DD/MM/AAAA' e retorna (dia, mes) como ints. Ignora ano se houver."""
    date_str = date_str.strip()
    parts = date_str.split("/")
    if len(parts) < 2:
        return None
    try:
        dia = int(parts[0])
        mes = int(parts[1])
        if not (1 <= dia <= 31 and 1 <= mes <= 12):
            return None
        return (dia, mes)
    except:
        return None

def find_today_birthdays():
    """Retorna lista de nomes que fazem aniversário hoje (dia e mês)"""
    hoje = datetime.now(TZ)
    d, m = hoje.day, hoje.month
    aniversariantes = []
    for row in fetch_birthdays_rows():
        dm = parse_day_month(row["data"])
        if dm and dm[0] == d and dm[1] == m:
            aniversariantes.append(row["nome"])
    return aniversariantes

async def match_member_by_name(guild: discord.Guild, alvo_nome: str):
    """
    Faz uma correspondência 'flexível' entre o nome da planilha e o display_name do membro.
    Regras:
      - casefold
      - remove espaços
      - aceita substring em qualquer direção
    """
    alvo_norm = alvo_nome.casefold().replace(" ", "")
    for m in guild.members:
        name_norm = m.display_name.casefold().replace(" ", "")
        if alvo_norm in name_norm or name_norm in alvo_norm:
            return m
    return None

# Evita postagens duplicadas se o bot reiniciar no mesmo minuto
_last_announce_date = None

@bot.event
async def on_ready():
    print(f"botdosnivers conectado como {bot.user}")
    anunciar_aniversarios.start()

@tasks.loop(minutes=1)
async def anunciar_aniversarios():
    """Checa 1x/min e anuncia às 09:00 America/Sao_Paulo."""
    global _last_announce_date

    agora = datetime.now(TZ)
    # Dispara apenas no minuto certo (09:00)
    if not (agora.hour == 9 and agora.minute == 0):
        return

    hoje_date_key = agora.strftime("%Y-%m-%d")
    if _last_announce_date == hoje_date_key:
        # Já anunciamos hoje
        return

    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if channel is None:
        print(f"[ERRO] Canal {DISCORD_CHANNEL_ID} não encontrado. Verifique o ID e as permissões do bot.")
        return

    aniversariantes = find_today_birthdays()

    if not aniversariantes:
        print("Nenhum aniversário hoje. (ok)")
        _last_announce_date = hoje_date_key
        return

    # Monta mensagem com tentativas de mention
    # Tenta resolver mention por membro para cada guild que o bot está
    mentions = []
    nomes_nao_encontrados = []

    # Considera todas as guilds onde o bot está (caso o canal seja compartilhado, já estamos com o ID exato)
    guilds = bot.guilds

    for nome in aniversariantes:
        mencionado = False
        for g in guilds:
            m = await match_member_by_name(g, nome)
            if m:
                mentions.append(m.mention)
                mencionado = True
                break
        if not mencionado:
            nomes_nao_encontrados.append(nome)

    # Mensagem principal
    bolo = "🎂"
    confete = "🎉"
    texto_mencoes = ", ".join(mentions) if mentions else ""
    texto_nao_encontrados = ", ".join(nomes_nao_encontrados) if nomes_nao_encontrados else ""

    linhas = []
    if texto_mencoes:
        linhas.append(f"{bolo}{confete} **Hoje tem niver!** Parabéns {texto_mencoes}! {confete}{bolo}")
    if texto_nao_encontrados:
        # fallback com nomes “crus” caso não tenha encontrado o membro
        linhas.append(f"{bolo}{confete} **Hoje tem niver!** Parabéns {texto_nao_encontrados}! {confete}{bolo}")

    if not linhas:
        # segurança: se por algum motivo deu vazio, ainda assim registra e sai
        print("Nada para anunciar (nenhuma correspondência).")
        _last_announce_date = hoje_date_key
        return

    try:
        await channel.send("\n".join(linhas))
        _last_announce_date = hoje_date_key
        print(f"Anúncio de aniversários enviado para o canal {DISCORD_CHANNEL_ID}")
    except Exception as e:
        print(f"[ERRO] Falha ao enviar mensagem no canal {DISCORD_CHANNEL_ID}: {e}")

# ======== Comandos úteis ========

@bot.command(name="testbday")
async def test_birthday(ctx, *, nome: str = None):
    """
    Envia uma mensagem de teste de aniversário para o canal configurado.
    Uso: !testbday Maria Fernanda
    Se não informar nome, faz um dry-run de quem faz hoje pela planilha.
    """
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if channel is None:
        await ctx.reply("Canal inválido ou não encontrado. Verifique DISCORD_CHANNEL_ID.")
        return

    if nome:
        # tenta mencionar
        m = None
        for g in bot.guilds:
            m = await match_member_by_name(g, nome)
            if m:
                break
        if m:
            await channel.send(f"🧪 {m.mention} **faz aniversário (TESTE)**! 🎉🎂")
            await ctx.reply(f"Ok! Teste enviado mencionando {m.display_name}.")
        else:
            await channel.send(f"🧪 **{nome}** faz aniversário (TESTE)! 🎉🎂 (não encontrei o usuário para mencionar)")
            await ctx.reply(f"Ok! Teste enviado sem @ (não encontrei o membro).")
    else:
        aniversariantes = find_today_birthdays()
        if aniversariantes:
            await channel.send("🧪 **TESTE** – Aniversariantes de hoje pela planilha: " + ", ".join(aniversariantes))
            await ctx.reply("Ok! Teste de hoje enviado.")
        else:
            await ctx.reply("Hoje não há aniversariantes na planilha.")

@bot.command(name="proximos")
async def proximos(ctx, dias: int = 30):
    """
    Lista próximos aniversários em N dias (padrão 30) – não envia no canal, só responde no chat.
    Usa o ano corrente para calcular o próximo aniversário.
    """
    hoje = datetime.now(TZ).date()
    rows = fetch_birthdays_rows()
    futuros = []

    for r in rows:
        dm = parse_day_month(r["data"])
        if not dm:
            continue
        d, m = dm
        ano_ref = hoje.year
        try:
            data_ref = datetime(ano_ref, m, d).date()
        except ValueError:
            # datas inválidas tipo 29/02 em ano não bissexto — joga pro próximo ano bissexto/normal
            try:
                data_ref = datetime(ano_ref + 1, m, d).date()
            except:
                continue

        if data_ref < hoje:
            try:
                data_ref = datetime(ano_ref + 1, m, d).date()
            except:
                continue

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

# ========= Bootstrap =========
def main():
    keep_alive()
    bot.run(BOT_TOKEN)

if __name__ == "__main__":
    main()

