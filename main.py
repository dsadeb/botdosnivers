from keep_alive import keep_alive
from dotenv import load_dotenv
import os
import discord
from discord.ext import tasks, commands
from datetime import datetime
import pytz

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Lista dos nomes em ordem de rodízio (use os nomes de usuário exatos do Discord, ex: "@Maria Fernanda")
rodizio = [
    "Julia Kliemann", "Kauê Kazuo Kubo", "Lucas Sadoski", "Maria Fernanda","Maria Júlia", "Mateus Silverio", "Matheus Belizário", 
    "Matheus Mello", "Milene Lopes", "Paulo Nogueira", "Pedro Balieiro", "Rodrigo", 
    "Agata Kojiio", "Aline Lima", "Arthur Tormena", "Cindy Grasiely", "Débora Sanches Aroca", 
    "Enzo Vieira", "Érica Doneux", "Fabio", "Hemilly Silva Barbosa", "João Birtche"
]

# Fuso-horário de Brasília
timezone = pytz.timezone('America/Sao_Paulo')

# Iniciação do bot
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Começamos com a primeira pessoa da lista
index_atual = 0

@bot.event
async def on_ready():
    print(f"Bot conectado como {bot.user}")
    enviar_lembrete.start()

@bot.command(name='test')
async def test_lembrete(ctx):
    """Comando de teste para enviar lembrete imediatamente"""
    global index_atual

    nome_mencao = rodizio[index_atual % len(rodizio)]
    nome_limpo = nome_mencao.lower().strip("@").replace(" ", "")

    enviado = False
    for guild in bot.guilds:
        for member in guild.members:
            member_name_clean = member.display_name.lower().replace(" ", "")
            if nome_limpo in member_name_clean or member_name_clean in nome_limpo:
                try:
                    await member.send(f"🧪 TESTE: Oi {member.display_name}! Hoje é sua vez de tirar o lixo no escritório! ✨🚩🗑️")
                    await ctx.send(f"Mensagem de teste enviada para {member.display_name}")
                    enviado = True
                    break
                except Exception as e:
                    await ctx.send(f"Não consegui enviar para {member.display_name}: {e}")
        if enviado:
            break

    if enviado:
        index_atual += 1
        await ctx.send(f"Próxima pessoa será: {rodizio[index_atual % len(rodizio)]}")
    else:
        await ctx.send(f"Membro não encontrado: {nome_mencao}")

@tasks.loop(minutes=1)
async def enviar_lembrete():
    global index_atual
    agora = datetime.now(timezone)

    # Verifica se é segunda-feira e 09:00 - lembrete semanal
    if agora.weekday() == 0 and agora.hour == 9 and agora.minute == 0:
        nome_mencao = rodizio[index_atual % len(rodizio)]
        nome_limpo = nome_mencao.lower().strip("@").replace(" ", "")

        enviado = False
        for guild in bot.guilds:
            for member in guild.members:
                member_name_clean = member.display_name.lower().replace(" ", "")
                if nome_limpo in member_name_clean or member_name_clean in nome_limpo:
                    try:
                        await member.send(f"🗓️ Oi {member.display_name}! Esta semana é sua vez de cuidar da limpeza do escritório. Lembre-se de tirar o lixo na sexta-feira e fique atento durante a semana! ✨🧹")
                        print(f"Lembrete semanal enviado para {member.display_name}")
                        enviado = True
                        break
                    except Exception as e:
                        print(f"Não consegui enviar lembrete semanal para {member.display_name}: {e}")
            if enviado:
                break

        if not enviado:
            print(f"Membro não encontrado para lembrete semanal: {nome_mencao}")

    # Verifica se é sexta-feira e 17:00 - lembrete do dia
    if agora.weekday() == 4 and agora.hour == 17 and agora.minute == 0:
        nome_mencao = rodizio[index_atual % len(rodizio)]
        nome_limpo = nome_mencao.lower().strip("@").replace(" ", "")

        enviado = False
        for guild in bot.guilds:
            for member in guild.members:
                member_name_clean = member.display_name.lower().replace(" ", "")
                if nome_limpo in member_name_clean or member_name_clean in nome_limpo:
                    try:
                        await member.send(f"Oi {member.display_name}! Hoje é sua vez de tirar o lixo no escritório! ✨🚩🗑️")
                        print(f"Mensagem enviada para {member.display_name}")
                        enviado = True
                        break
                    except Exception as e:
                        print(f"Não consegui enviar para {member.display_name}: {e}")
            if enviado:
                break

        if enviado:
            index_atual += 1
        else:
            print(f"Membro não encontrado: {nome_mencao}")

def main():
    keep_alive()
    bot.run(BOT_TOKEN)

if __name__ == "__main__":
    main()
