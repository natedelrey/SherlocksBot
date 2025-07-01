# main.py

import discord
from discord.ext import commands
import os
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import re
import openai
import psycopg2

# Load environment variables
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=".", intents=intents)

openai.api_key = OPENAI_API_KEY

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def ensure_tables():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS watchlists (
            user_id TEXT,
            movie TEXT,
            PRIMARY KEY (user_id, movie)
        );
        CREATE TABLE IF NOT EXISTS letterboxd_profiles (
            user_id TEXT PRIMARY KEY,
            link TEXT
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

ensure_tables()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")

@bot.command()
async def commands(ctx):
    await ctx.send(
        "📖 **Available Commands**\n"
        "`.movie [prompt]` – Ask GPT for recommendations (2021+ awareness).\n"
        "`.log [movie name]` – Add a movie to your watchlist (reaction menu).\n"
        "`.unlog [movie name]` – Remove a movie from your watchlist.\n"
        "`.watchlist [@user]` – View yours or another user’s watchlist.\n"
        "`.syncletterboxd [link]` – Link your Letterboxd profile.\n"
        "`.importletterboxd` – Import watched films from Letterboxd.\n"
        "`.compare @user1 @user2` – Compare two users’ watchlists."
    )

@bot.command()
async def movie(ctx, *, prompt):
    await ctx.send("🧠 Finding movie recommendations... (GPT knowledge cutoff: 2021)")
    chat_response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a helpful movie expert."},
            {"role": "user", "content": f"Give me 5 movie recommendations based on: {prompt}. Include year."}
        ]
    )
    await ctx.send(chat_response.choices[0].message.content)

@bot.command()
async def log(ctx, *, movie_name):
    tmdb_url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={movie_name}"
    response = requests.get(tmdb_url)
    data = response.json()

    if not data['results']:
        await ctx.send("❌ No results found.")
        return

    current_page = 0
    movies = data['results']

    async def show_page(page):
        start = page * 4
        chunk = movies[start:start+4]
        if not chunk:
            await ctx.send("❌ No more results.")
            return

        embed = discord.Embed(title="🎥 Choose a Movie to Log", description="React to log. ⏪ ⏩ to scroll.")
        for i, movie in enumerate(chunk):
            title = movie['title']
            year = movie.get('release_date', 'N/A')[:4] if movie.get('release_date') else "N/A"
            embed.add_field(name=f"{i+1})", value=f"{title} ({year})", inline=False)

        msg = await ctx.send(embed=embed)
        emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"][:len(chunk)] + ["⏪", "⏩"]
        for emoji in emojis:
            await msg.add_reaction(emoji)

        def check(reaction, user):
            return user == ctx.author and reaction.message.id == msg.id and str(reaction.emoji) in emojis

        try:
            reaction, _ = await bot.wait_for("reaction_add", timeout=30.0, check=check)
            await msg.delete()

            if reaction.emoji == "⏩":
                await show_page(page + 1)
            elif reaction.emoji == "⏪" and page > 0:
                await show_page(page - 1)
            else:
                index = emojis.index(str(reaction.emoji))
                movie = chunk[index]
                title = movie['title']
                year = movie.get('release_date', 'N/A')[:4] if movie.get('release_date') else "N/A"
                poster = movie.get('poster_path')
                poster_url = f"https://image.tmdb.org/t/p/w500{poster}" if poster else None

                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("INSERT INTO watchlists (user_id, movie) VALUES (%s, %s) ON CONFLICT DO NOTHING", (str(ctx.author.id), f"{title} ({year})"))
                conn.commit()
                cur.close()
                conn.close()

                await ctx.send(f"✅ Logged **{title} ({year})** to your watchlist!" + (f"\n{poster_url}" if poster_url else ""))

        except:
            await ctx.send("⌛ Timed out or invalid reaction.")

    first = movies[0]
    title = first['title']
    year = first.get('release_date', 'N/A')[:4] if first.get('release_date') else "N/A"
    msg = await ctx.send(f"🎥 Did you mean **{title} ({year})**? React with ✅ to confirm, ❌ to browse options.")
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    def confirm_check(r, u): return u == ctx.author and r.message.id == msg.id and str(r.emoji) in ["✅", "❌"]

    try:
        reaction, _ = await bot.wait_for("reaction_add", timeout=30.0, check=confirm_check)
        if str(reaction.emoji) == "✅":
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("INSERT INTO watchlists (user_id, movie) VALUES (%s, %s) ON CONFLICT DO NOTHING", (str(ctx.author.id), f"{title} ({year})"))
            conn.commit()
            cur.close()
            conn.close()

            poster = first.get('poster_path')
            poster_url = f"https://image.tmdb.org/t/p/w500{poster}" if poster else None
            await ctx.send(f"✅ Logged **{title} ({year})** to your watchlist!" + (f"\n{poster_url}" if poster_url else ""))
        else:
            await msg.delete()
            await show_page(current_page)
    except:
        await ctx.send("⌛ Timed out.")

@bot.command()
async def unlog(ctx, *, movie_name):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT movie FROM watchlists WHERE user_id = %s", (str(ctx.author.id),))
    movies = [row[0] for row in cur.fetchall()]
    matches = [m for m in movies if movie_name.lower() in m.lower()]

    if not matches:
        await ctx.send("❌ No matching movies found in your watchlist.")
    elif len(matches) == 1:
        cur.execute("DELETE FROM watchlists WHERE user_id = %s AND movie = %s", (str(ctx.author.id), matches[0]))
        conn.commit()
        await ctx.send(f"🗑️ Removed **{matches[0]}** from your watchlist.")
    else:
        options = "\n".join([f"{i+1}. {m}" for i, m in enumerate(matches)])
        await ctx.send(f"Multiple matches found:\n{options}\nReply with the number to remove.")

        def check(msg):
            return msg.author == ctx.author and msg.content.isdigit() and 1 <= int(msg.content) <= len(matches)

        try:
            reply = await bot.wait_for("message", timeout=30.0, check=check)
            selected = matches[int(reply.content)-1]
            cur.execute("DELETE FROM watchlists WHERE user_id = %s AND movie = %s", (str(ctx.author.id), selected))
            conn.commit()
            await ctx.send(f"🗑️ Removed **{selected}** from your watchlist.")
        except:
            await ctx.send("⌛ Timed out or invalid response.")

    cur.close()
    conn.close()

@bot.command()
async def watchlist(ctx, member: discord.Member = None):
    member = member or ctx.author
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT movie FROM watchlists WHERE user_id = %s", (str(member.id),))
    movies = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()

    if not movies:
        await ctx.send("📭 No movies logged.")
    else:
        await ctx.send(f"🎞️ **{member.display_name}'s Watchlist:**\n" + "\n".join(movies))

@bot.command()
async def syncletterboxd(ctx, link):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO letterboxd_profiles (user_id, link) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET link = EXCLUDED.link",
        (str(ctx.author.id), link)
    )
    conn.commit()
    cur.close()
    conn.close()
    await ctx.send(f"🔗 Linked Letterboxd profile: {link}")

@bot.command()
async def importletterboxd(ctx):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT link FROM letterboxd_profiles WHERE user_id = %s", (str(ctx.author.id),))
    row = cur.fetchone()

    if not row:
        await ctx.send("❌ You haven’t linked your Letterboxd profile. Use `.syncletterboxd`.")
        cur.close()
        conn.close()
        return

    try:
        link = row[0]
        username = re.findall(r"letterboxd\.com/([\w-]+)/?", link)[0]
        url = f"https://letterboxd.com/{username}/films/by/added/"
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(response.text, "html.parser")
        posters = soup.select("li.poster-container img") or soup.select("li.poster img")
        titles = [img["alt"] for img in posters if img.get("alt")]

        for title in titles:
            cur.execute("INSERT INTO watchlists (user_id, movie) VALUES (%s, %s) ON CONFLICT DO NOTHING", (str(ctx.author.id), title))
        conn.commit()

        await ctx.send(f"📥 Imported {len(titles)} movies from Letterboxd." if titles else "⚠️ Couldn't find movies.")

    except Exception as e:
        await ctx.send(f"❌ Failed to import. Error: {e}")

    cur.close()
    conn.close()

@bot.command()
async def compare(ctx, member1: discord.Member, member2: discord.Member):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT movie FROM watchlists WHERE user_id = %s", (str(member1.id),))
    list1 = set(row[0] for row in cur.fetchall())
    cur.execute("SELECT movie FROM watchlists WHERE user_id = %s", (str(member2.id),))
    list2 = set(row[0] for row in cur.fetchall())
    cur.close()
    conn.close()

    if not list1 or not list2:
        await ctx.send("❌ One or both users have empty watchlists.")
        return

    shared = list1 & list2
    total = len(list1 | list2)
    percent = (len(shared) / total) * 100 if total else 0

    await ctx.send(f"🎭 **{member1.display_name}** and **{member2.display_name}** share {len(shared)} movies.\nMatch: **{percent:.1f}%**\n\n🎬 Shared:\n" + "\n".join(shared))

bot.run(DISCORD_TOKEN)
