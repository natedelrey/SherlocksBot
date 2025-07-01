import discord
from discord.ext import commands
import os
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import re
import json
import openai

# Load environment variables
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

openai.api_key = OPENAI_API_KEY

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=".", intents=intents)

DATA_FILE = "sherlocksbot_data.json"

def load_data():
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            return data.get("watchlists", {}), data.get("letterboxd", {})
    except FileNotFoundError:
        return {}, {}

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump({
            "watchlists": user_watchlists,
            "letterboxd": letterboxd_profiles
        }, f, indent=2)

user_watchlists, letterboxd_profiles = load_data()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")

@bot.command()
async def commands(ctx):
    description = (
        "📖 **Available Commands**\n"
        "`.movie [prompt]` – Ask GPT for recommendations (2021+ awareness).\n"
        "`.log [movie name]` – Add a movie to your watchlist (with confirmation + suggestions + reaction scrolling).\n"
        "`.unlog [movie name]` – Remove a movie from your watchlist.\n"
        "`.watchlist [@user]` – View your or someone else's logged movies.\n"
        "`.syncletterboxd [link]` – Link your Letterboxd profile.\n"
        "`.importletterboxd` – Import all movies from your Letterboxd watchlist.\n"
        "`.compare @user1 @user2` – Compare two users' watchlists and show match %."
    )
    await ctx.send(description)

@bot.command()
async def movie(ctx, *, prompt):
    await ctx.send("🧠 Using AI to find recommendations...\n⚠️ Keep in mind: GPT knowledge cutoff is September 2021.")
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a helpful movie expert."},
            {"role": "user", "content": f"Give me 5 movie recommendations based on this prompt: {prompt}. Include year."}
        ]
    )
    reply = response.choices[0].message.content
    await ctx.send(reply)

@bot.command()
async def unlog(ctx, *, movie_name):
    user_id = str(ctx.author.id)
    watchlist = user_watchlists.get(user_id, [])
    matches = [m for m in watchlist if movie_name.lower() in m.lower()]

    if not matches:
        await ctx.send("❌ No matching movies found in your watchlist.")
    elif len(matches) == 1:
        watchlist.remove(matches[0])
        save_data()
        await ctx.send(f"🗑️ Removed **{matches[0]}** from your watchlist.")
    else:
        options = "\n".join([f"{i+1}. {m}" for i, m in enumerate(matches)])
        await ctx.send(f"Multiple matches found:\n{options}\nReply with the number to remove.")

        def check(msg):
            return msg.author == ctx.author and msg.content.isdigit() and 1 <= int(msg.content) <= len(matches)

        try:
            reply = await bot.wait_for("message", timeout=30.0, check=check)
            selected = matches[int(reply.content)-1]
            watchlist.remove(selected)
            save_data()
            await ctx.send(f"🗑️ Removed **{selected}** from your watchlist.")
        except:
            await ctx.send("⌛ Timed out or invalid response.")

@bot.command()
async def watchlist(ctx, member: discord.Member = None):
    member = member or ctx.author
    user_id = str(member.id)
    movies = user_watchlists.get(user_id, [])
    if not movies:
        await ctx.send("📭 No movies logged yet.")
    else:
        movie_list = "\n".join(movies)
        await ctx.send(f"🎞️ **{member.display_name}'s Watchlist:**\n{movie_list}")

@bot.command()
async def syncletterboxd(ctx, link):
    user_id = str(ctx.author.id)
    letterboxd_profiles[user_id] = link
    save_data()
    await ctx.send(f"🔗 Linked your Letterboxd: {link}")

@bot.command()
async def importletterboxd(ctx):
    user_id = str(ctx.author.id)
    link = letterboxd_profiles.get(user_id)
    if not link:
        await ctx.send("❌ You haven’t linked your Letterboxd profile. Use `.syncletterboxd`.")
        return

    try:
        username = re.findall(r"letterboxd\.com/([\w-]+)/?", link)[0]
        url = f"https://letterboxd.com/{username}/films/"
        response = requests.get(url)
        soup = BeautifulSoup(response.text, "html.parser")
        titles = [a["alt"] for a in soup.select("li.poster img")]

        user_watchlists.setdefault(user_id, [])
        for title in titles:
            if title not in user_watchlists[user_id]:
                user_watchlists[user_id].append(title)
        save_data()
        await ctx.send(f"📥 Imported {len(titles)} movies from Letterboxd.")
    except:
        await ctx.send("❌ Failed to import from Letterboxd.")

@bot.command()
async def compare(ctx, member1: discord.Member, member2: discord.Member):
    id1 = str(member1.id)
    id2 = str(member2.id)
    list1 = set(user_watchlists.get(id1, []))
    list2 = set(user_watchlists.get(id2, []))

    if not list1 or not list2:
        await ctx.send("❌ One or both users have empty watchlists.")
        return

    shared = list1 & list2
    total = len(list1 | list2)
    percent = (len(shared) / total) * 100 if total else 0

    await ctx.send(f"🎭 **{member1.display_name}** and **{member2.display_name}** have {len(shared)} movies in common.\nMatch: **{percent:.1f}%**\n\n🎞️ Shared Movies:\n" + "\n".join(shared))

@bot.command()
async def log(ctx, *, movie_name):
    tmdb_url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={movie_name}"
    response = requests.get(tmdb_url)
    data = response.json()

    if not data['results']:
        await ctx.send("❌ Movie not found.")
        return

    current_page = 0

    async def show_options(page):
        start = page * 4
        results = data['results'][start:start + 4]
        if not results:
            await ctx.send("❌ No more results.")
            return

        embed = discord.Embed(title="🎥 Select a Movie to Log", description="React with 1️⃣ 2️⃣ 3️⃣ 4️⃣ to log. ⏪ ⏩ to scroll.")

        for i, movie in enumerate(results):
            title = movie['title']
            year = movie.get('release_date', 'N/A')[:4] if movie.get('release_date') else 'N/A'
            embed.add_field(name=f"{i+1})", value=f"{title} ({year})", inline=False)

        msg = await ctx.send(embed=embed)

        emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"][:len(results)] + ["⏪", "⏩"]
        for emoji in emojis:
            await msg.add_reaction(emoji)

        def reaction_check(reaction, user):
            return user == ctx.author and reaction.message.id == msg.id and str(reaction.emoji) in emojis

        try:
            reaction, user = await bot.wait_for("reaction_add", timeout=30.0, check=reaction_check)
            emoji = str(reaction.emoji)
            await msg.delete()
            if emoji == "⏩":
                await show_options(page + 1)
            elif emoji == "⏪" and page > 0:
                await show_options(page - 1)
            else:
                index = emojis.index(emoji)
                movie = results[index]
                title = movie['title']
                year = movie.get('release_date', 'N/A')[:4] if movie.get('release_date') else 'N/A'
                user_id = str(ctx.author.id)
                user_watchlists.setdefault(user_id, []).append(f"{title} ({year})")
                save_data()
                poster_url = movie.get('poster_path')
                poster = f"https://image.tmdb.org/t/p/w500{poster_url}" if poster_url else None
                await ctx.send(f"✅ Logged **{title} ({year})** to your watchlist!" + (f"\n{poster}" if poster else ""))

        except:
            await ctx.send("⌛ Timed out or invalid reaction.")

    first_movie = data['results'][0]
    title = first_movie['title']
    year = first_movie.get('release_date', 'N/A')[:4] if first_movie.get('release_date') else 'N/A'
    msg = await ctx.send(f"🎥 Did you mean **{title} ({year})**? React with ✅ to confirm or ❌ to see more options.")

    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    def check(reaction, user):
        return user == ctx.author and reaction.message.id == msg.id and str(reaction.emoji) in ["✅", "❌"]

    try:
        reaction, user = await bot.wait_for("reaction_add", timeout=30.0, check=check)
        if str(reaction.emoji) == "✅":
            user_id = str(ctx.author.id)
            user_watchlists.setdefault(user_id, []).append(f"{title} ({year})")
            save_data()
            poster_url = first_movie.get('poster_path')
            poster = f"https://image.tmdb.org/t/p/w500{poster_url}" if poster_url else None
            await ctx.send(f"✅ Logged **{title} ({year})** to your watchlist!" + (f"\n{poster}" if poster else ""))

        else:
            await msg.delete()
            await show_options(current_page)
    except:
        await ctx.send("⌛ Timed out waiting for reaction.")

bot.run(DISCORD_TOKEN)
