import time
import aiohttp
import requests , json , discord , os , re , datetime, asyncio, sqlite3
from discord import app_commands
from discord.ext import commands, tasks
from fake_useragent import UserAgent
from collections import defaultdict


from embed import newEmbed
class RateLimiter:
    def __init__(self, calls_per_minute):
        self.calls_per_minute = calls_per_minute
        self.call_times = []

    async def wait(self):
        now = time.time()
        self.call_times = [t for t in self.call_times if now - t < 60]
        if len(self.call_times) >= self.calls_per_minute:
            await asyncio.sleep(60 - (now - self.call_times[0]))
        self.call_times.append(time.time())

rate_limiter = RateLimiter(30)

class steamSearch(commands.Cog):
    
    def __init__(self, bot):
        self.bot = bot
        self.steamKey = os.getenv("STEAM_API_KEY")
        self.steamToken = os.getenv("STEAM_TOKEN")
        self.conn = sqlite3.connect('monitored_users.db')
        self.cursor = self.conn.cursor()
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS monitored_users
                            (discord_id TEXT, steam_id TEXT, last_state INTEGER)''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS monitored_fivem_players
                    (discord_id TEXT, server_id TEXT, player_identifier TEXT, total_playtime INTEGER, last_seen INTEGER, state INTEGER)''')
        self.conn.commit()
        self.monitor_users.start()
        self.monitor_fivem_players.start()
    '''
    Creates an embed of the steam profile of the user with the given steam identifier (steam 64, hex, or vanity id)
    @param interaction : discord.Interaction the interaction object of the user who called the command
    @param steamid : str the steam identifier of the user
    '''
    @app_commands.command(name = "steam", description = "Command to search for a steam profile")
    @app_commands.describe(steamid = "The steam 64, hex or vanity id of the user you want to search for")
    async def steamlookup(self, interaction : discord.Interaction, steamid : str):
        id = await self.getID(steamid.lower()) #Gets the steam 64 ID
        if id == None: #If the ID is invalid, return an error message
            await interaction.response.send_message(f"Invalid or Unknown Steam Id ({steamid})", ephemeral=True)
            return
        
        #requests the userdata and bans infomation from steam
        userdata = await self.get_user_data(id)
        bansRequest = requests.get(f"http://api.steampowered.com/ISteamUser/GetPlayerBans/v1/?key={self.steamKey}&access_token={self.steamToken}&steamids={id}")
        profileURL = ""
        personState, lastSeen = self.get_state(userdata)
        
        #loads the json data from the requests
        bansdata = json.loads(bansRequest.text)['players'][0]

        if userdata["communityvisibilitystate"] > 1:
            userdata["timecreated"] = datetime.datetime.fromtimestamp(int(userdata["timecreated"])).strftime('%d-%m-%Y')
        else:
            userdata["timecreated"] = "Private Profile"

        if userdata["profileurl"] == "":
            profileURL = "Private Profile"
        else:
            profileURL = "[Click Here]({})".format(userdata['profileurl'])


        #creates a discord embed containing the infomation from the requests using the newEmbed method from embed.py
        fm = newEmbed(title=f"Steam Profile Look Up",  #embed title 
        fields={  #embed fields
            "Steam Name": userdata['personaname'],
            "Steam Hex":"Steam:"+(hex(int(id))[2:]),
            "Steam ID":userdata['steamid'],
            "realname": userdata.get('realname', 'Unknown'),
            "Country": userdata.get('loccountrycode', 'Unknown'),
            "VAC Bans":bansdata['NumberOfVACBans'],
            "Game Bans":bansdata['NumberOfGameBans'],
            "Days Since Last Ban" : bansdata['DaysSinceLastBan'],
            "Account Creation Date": userdata["timecreated"] , #time of account create or Unknown if they have a private profile profile
            "Status": personState,
            "Last Seen": lastSeen,  #last time the user was seen or Unknown if they have a private profile profile
            "Profile URL": profileURL,
        },
        embedUrl=userdata['avatarfull'] #the profile picture of the user
        )

        await interaction.response.send_message(embed=fm, ephemeral=True) # sends the embed that was created to the user who requested the search

    @app_commands.command(name="smonitor", description="Monitor a Steam user's status")
    @app_commands.describe(steamid="The steam 64, hex or vanity id of the user you want to monitor")
    async def monitor_user(self, interaction: discord.Interaction, steamid: str):
        steam_id = await self.getID(steamid.lower())
        if steam_id is None:
            await interaction.response.send_message(f"Invalid or Unknown Steam Id ({steamid})", ephemeral=True)
            return

        self.cursor.execute("SELECT * FROM monitored_users WHERE discord_id=? AND steam_id=?", 
                            (str(interaction.user.id), str(steam_id)))
        if self.cursor.fetchone():
            await interaction.response.send_message("You're already monitoring this user.", ephemeral=True)
            return

        userdata = await self.get_user_data(steam_id)
        if userdata:
            self.cursor.execute("INSERT INTO monitored_users VALUES (?, ?, ?)", 
                                (str(interaction.user.id), str(steam_id), userdata["personastate"]))
            self.conn.commit()
            personState, lastSeen = self.get_state(userdata)
            em = newEmbed(title="Started Monitoring",  #embed title 
            fields={
                "Steam Name": userdata['personaname'],
                "Steam ID": steamid,
                "Status": personState,
                "Last Seen": lastSeen,
            },
            embedUrl=userdata['avatarfull'] 
            )

            await interaction.response.send_message(embed=em, ephemeral=True)
        else:
            await interaction.response.send_message("Failed to fetch user data. Please try again later.", ephemeral=True)
        

    '''
    Method to return the 64 bit steam id of the user or None if it is invalid
    @param steamid : str the steam identifier of the user
    @return steamid : int steam 64 id of the user or None if the steam id is invalid
    '''
    async def getID(self, steamid):
        if re.match(r'^\d{17}$', steamid):
            return int(steamid)
        elif re.match(r'^steam:[A-Fa-f0-9]{15}$', steamid):
            id = (steamid[6:])
            return int(id,16)
        elif re.match(r'^[A-Za-z0-9_]+$', steamid):
            id = await self.getVanityURl(steamid)
            if id != None: return id
            id = await self.getVanityURl(steamid[30:])
            return id
        return None

    @app_commands.command(name="sstop", description="Stop monitoring a Steam user's status")
    @app_commands.describe(steamid="The steam 64, hex or vanity id of the user you want to stop monitoring")
    async def stop_monitor(self, interaction: discord.Interaction, steamid: str):
        steam_id = await self.getID(steamid.lower())
        if steam_id is None:
            await interaction.response.send_message(f"Invalid or Unknown Steam Id ({steamid})", ephemeral=True)
            return
        self.cursor.execute("DELETE FROM monitored_users WHERE discord_id=? AND steam_id=?", 
                            (str(interaction.user.id), str(steam_id)))
        self.conn.commit()
        userdata = await self.get_user_data(steam_id)
        personState, lastSeen = self.get_state(userdata)
        em = newEmbed(title="Stopped Monitoring",  #embed title 
        fields={
            "Steam Name": userdata['personaname'],
            "Steam ID": steamid,
            "Status": personState,
            "Last Seen": lastSeen,
        },
        embedUrl=userdata['avatarfull'] 
        )

        await interaction.response.send_message(embed=em, ephemeral=True)
    @app_commands.command(name="slist", description="List all Steam users you're monitoring")
    async def steam_list_monitored(self, interaction: discord.Interaction):
        self.cursor.execute("SELECT steam_id, last_state FROM monitored_users WHERE discord_id = ?",
                            (str(interaction.user.id),))
        monitored_users = self.cursor.fetchall()

        if not monitored_users:
            await interaction.response.send_message("You are not monitoring any Steam users.", ephemeral=True)
            return

        fields = {}
        for steam_id, last_state in monitored_users:
            userdata = await self.get_user_data(steam_id)
            if userdata:
                personState, _ = self.get_state(userdata)
                fields[f"Steam ID: {steam_id}"] = f"Name: {userdata['personaname']}\nStatus: {personState}"
            else:
                fields[f"Steam ID: {steam_id}"] = "Unable to fetch user data"

        em = newEmbed(title="Monitored Steam Users", fields=fields)
        await interaction.response.send_message(embed=em, ephemeral=True)
    
    '''
    Method to check if the vanity id is valid and get the steam 64 id of the user
    @param steamid : str the vanity id of the user
    @return steamid : int steam 64 id of the user or None if the vanity id is invalid
    '''
    async def getVanityURl(self, steamid):
        res = requests.get(f"http://api.steampowered.com/ISteamUser/ResolveVanityURL/v0001/?key={self.steamKey}&access_token={self.steamToken}&vanityurl={steamid}")
        vanitydata = json.loads(res.text)["response"]
        if vanitydata["success"] == 1:
            return vanitydata["steamid"]
        else:
            return None
    
    async def get_user_data(self, steam_id):
        userRequest = requests.get(f"http://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/?key={self.steamKey}&access_token={self.steamToken}&steamids={steam_id}")
        if userRequest.status_code == 200:
            return json.loads(userRequest.text)["response"]['players'][0]
        return None
    
    @tasks.loop(minutes=1)
    async def monitor_users(self):
        self.cursor.execute("SELECT * FROM monitored_users")
        monitored_users = self.cursor.fetchall()
        for discord_id, steam_id, last_state in monitored_users:
            userdata = await self.get_user_data(steam_id)
            if userdata and userdata["personastate"] != last_state:
                user = self.bot.get_user(int(discord_id))
                if user:
                    personState, lastSeen = self.get_state(userdata)
                    em = newEmbed(title="Status Changed",  #embed title 
                    fields={
                        "Steam Name": userdata['personaname'],
                        "Steam ID": steam_id,
                        "Status": f"{self.get_state_name(last_state)} -> {personState}",
                    }, embedUrl=userdata['avatarfull'] )
                    await user.send(embed=em)
                self.cursor.execute("UPDATE monitored_users SET last_state=? WHERE discord_id=? AND steam_id=?", 
                                    (userdata["personastate"], discord_id, steam_id))
        self.conn.commit()

    @monitor_users.before_loop
    async def before_monitor_users(self):
        await self.bot.wait_until_ready()

    def get_state_name(self, state):
        states = {0: "Offline", 1: "Online", 2: "Busy", 3: "Away", 4: "Snooze", 5: "Looking to Trade", 6: "Looking to Play", 7: "In Game"}
        return states.get(state, "Unknown")
    
    def get_state(self, userdata):
        personState = "Unknown"
        lastSeen = "Unknown"

        state_mapping = {0: "Offline", 1: "Online", 2: "Busy", 3: "Away", 4: "Snooze", 5: "Looking to Trade", 6: "Looking to Play", 7: "In Game"}

        personState = state_mapping.get(userdata["personastate"], "Unknown")

        if "lastlogoff" in userdata:
            lastSeen = datetime.datetime.fromtimestamp(int(userdata["lastlogoff"])).strftime('%d-%m-%Y %H:%M:%S')
        elif userdata["personastate"] != 0:
            lastSeen = "Unknown // Bukan temen dari yang punya bot"

        return personState, lastSeen
    

    # FIVEM PART =========================================================================================================\
    async def get_fivem_server_data(self, server_id):
        url = f'https://servers-frontend.fivem.net/api/servers/single/{server_id}'
        ua = UserAgent()
        headers = {'User-Agent': ua.random, 'Referer': 'https://servers-frontend.fivem.net/'}

        max_retries = 5
        retry_count = 0
        base_delay = 1

        while retry_count < max_retries:
            try:
                await rate_limiter.wait()  # Wait before making a request
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers, timeout=100) as response:
                        if response.status == 200:
                            data = await response.json()
                            return data.get("Data", {})
                        elif response.status == 403:
                            delay = base_delay * (2 ** retry_count)  # Exponential backoff
                            await asyncio.sleep(delay)
                        else:
                            delay = base_delay * (2 ** retry_count)  # Exponential backoff
                            await asyncio.sleep(delay)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                delay = base_delay * (2 ** retry_count)  # Exponential backoff
                await asyncio.sleep(delay)
            
            retry_count += 1

        return None


    @app_commands.command(name="fmonitor", description="Monitor a player on a FiveM server")
    @app_commands.describe(
        player_identifier="The player's identifier (Discord ID or name)",
        server_id="The FiveM server ID (e.g., 5kmgkz)"
    )
    async def fivem_monitor(self, interaction: discord.Interaction, player_identifier: str, server_id: str):
        await interaction.response.defer(ephemeral=True)
        
        server_data = await self.get_fivem_server_data(server_id)
        if server_data is None:
            await interaction.followup.send("Unable to fetch server data. Please check the server ID and try again.", ephemeral=True)
            return

        player_found = False
        current_time = int(time.time())

        for player in server_data.get('players', []):
            if player_identifier.lower() == player.get('name', '').lower() or f"discord:{player_identifier}" in player.get('identifiers', []):
                player_found = True
                self.cursor.execute(
                    "INSERT OR REPLACE INTO monitored_fivem_players (discord_id, server_id, player_identifier, total_playtime, last_seen, state) VALUES (?, ?, ?, ?, ?, ?)",
                    (str(interaction.user.id), server_id, player_identifier, 0, current_time, 1)
                )
                self.conn.commit()
                
                em = newEmbed(title="Monitor Added & Player Online",
                            fields={
                                "Player Identifier": player_identifier,
                                "Server Name": server_data.get('hostname', 'Unknown'),
                                "Server ID": server_id,
                                "Status": "Online",
                                "Total Playtime": "0 minutes"
                            },
                            embedUrl=server_data.get('icon', ''))
                break

        if not player_found:
            self.cursor.execute(
                    "INSERT OR REPLACE INTO monitored_fivem_players (discord_id, server_id, player_identifier, total_playtime, last_seen, state) VALUES (?, ?, ?, ?, ?, ?)",
                    (str(interaction.user.id), server_id, player_identifier, 0, 0, 0)
                )
            self.conn.commit()
            em = newEmbed(title="Monitor Added",
                fields={
                    "Player Identifier": player_identifier,
                    "Server Name": server_data.get('hostname', 'Unknown'),
                    "Server ID": server_id,
                    "Status": "Offline",
                    "Total Playtime": "0 minutes"
                },
                embedUrl=server_data.get('icon', ''))
        
        await interaction.followup.send(embed=em, ephemeral=True)

    @app_commands.command(name="fstop", description="Stop monitoring a player on a FiveM server")
    @app_commands.describe(
        player_identifier="The player's identifier (Discord ID or name)",
        server_id="The FiveM server ID (e.g., 5kmgkz)"
    )
    async def fivem_stop_monitor(self, interaction: discord.Interaction, player_identifier: str, server_id: str):
        self.cursor.execute("SELECT total_playtime FROM monitored_fivem_players WHERE discord_id = ? AND server_id = ? AND player_identifier = ?",
                            (str(interaction.user.id), server_id, player_identifier))
        result = self.cursor.fetchone()
        
        if result:
            total_playtime = result[0]
            self.cursor.execute("DELETE FROM monitored_fivem_players WHERE discord_id = ? AND server_id = ? AND player_identifier = ?",
                                (str(interaction.user.id), server_id, player_identifier))
            self.conn.commit()
            
            em = newEmbed(title="Stopped Monitoring",
                        fields={
                            "Player Identifier": player_identifier,
                            "Server ID": server_id,
                            "Total Playtime": f"{total_playtime // 60} minutes"
                        })
            await interaction.response.send_message(embed=em, ephemeral=True)
        else:
            await interaction.response.send_message(f"You were not monitoring player {player_identifier} on server {server_id}.", ephemeral=True)

    @app_commands.command(name="flist", description="List all players you're monitoring on FiveM servers")
    async def fivem_list_monitored(self, interaction: discord.Interaction):
        self.cursor.execute("SELECT * FROM monitored_fivem_players WHERE discord_id = ?",
                            (str(interaction.user.id),))
        monitored_players = self.cursor.fetchall()

        if not monitored_players:
            await interaction.response.send_message("You are not monitoring any FiveM players.", ephemeral=True)
            return

        fields = {}
        for index, player in enumerate(monitored_players, start=1):
            discord_id, server_id, player_identifier, total_playtime, last_seen, state = player
            fields[f"Player {index}"] = f"Server: {server_id}\nIdentifier: {player_identifier}\nTotal Playtime: {total_playtime // 60} minutes\nLast Seen: {datetime.datetime.fromtimestamp(last_seen).strftime('%Y-%m-%d %H:%M:%S')}\nState: {'Online' if state == 1 else 'Offline'}"

        em = newEmbed(title="Monitored FiveM Players", fields=fields)
        await interaction.response.send_message(embed=em, ephemeral=True)

    @tasks.loop(minutes=2)
    async def monitor_fivem_players(self):
        self.cursor.execute("SELECT * FROM monitored_fivem_players")
        monitored_players = self.cursor.fetchall()
        current_time = int(time.time())

        # Group players by server to reduce API calls
        server_players = defaultdict(list)
        for player in monitored_players:
            server_players[player[1]].append(player)

        for server_id, players in server_players.items():
            try:
                await rate_limiter.wait()
                server_data = await self.get_fivem_server_data(server_id)
                if server_data is None:
                    continue

                for discord_id, _, player_identifier, total_playtime, last_seen, state in players:
                    player_found = False
                    for player in server_data.get('players', []):
                        if player_identifier.lower() == player.get('name', '').lower() or f"discord:{player_identifier}" in player.get('identifiers', []):
                            player_found = True
                            new_total_playtime = total_playtime + (current_time - last_seen)
                            
                            if state == 0: 
                                self.cursor.execute(
                                    "UPDATE monitored_fivem_players SET total_playtime = ?, last_seen = ?, state = ? WHERE discord_id = ? AND server_id = ? AND player_identifier = ?",
                                    (new_total_playtime, current_time, 1, discord_id, server_id, player_identifier)
                                )
                                self.conn.commit()

                                user = self.bot.get_user(int(discord_id))
                                if user:
                                    em = newEmbed(title="FiveM Player Update",
                                                fields={
                                                    "Player Identifier": player_identifier,
                                                    "Server Name": server_data.get('hostname', 'Unknown'),
                                                    "Server ID": server_id,
                                                    "Status": "Online",
                                                    "Total Playtime": f"{new_total_playtime // 60} minutes"
                                                },
                                                embedUrl=server_data.get('icon', ''))
                                    await user.send(embed=em)
                            else:
                                # Update playtime without sending a notification
                                self.cursor.execute(
                                    "UPDATE monitored_fivem_players SET total_playtime = ?, last_seen = ? WHERE discord_id = ? AND server_id = ? AND player_identifier = ?",
                                    (new_total_playtime, current_time, discord_id, server_id, player_identifier)
                                )
                                self.conn.commit()
                            break

                    if not player_found and state == 1:  # Player was online but now offline
                        self.cursor.execute(
                            "UPDATE monitored_fivem_players SET state = ? WHERE discord_id = ? AND server_id = ? AND player_identifier = ?",
                            (0, discord_id, server_id, player_identifier)
                        )
                        self.conn.commit()

                        user = self.bot.get_user(int(discord_id))
                        if user:
                            em = newEmbed(title="FiveM Player Update",
                                        fields={
                                            "Player Identifier": player_identifier,
                                            "Server Name": server_data.get('hostname', 'Unknown'),
                                            "Server ID": server_id,
                                            "Status": "Offline",
                                            "Total Playtime": f"{total_playtime // 60} minutes"
                                        },
                                        embedUrl=server_data.get('icon', ''))
                            await user.send(embed=em)

            except Exception as e:
                print(f"Error processing server {server_id}: {str(e)}")
                await asyncio.sleep(5)  # Wait a bit before moving to the next server

        await asyncio.sleep(1)  # Small delay between iterations

    @monitor_fivem_players.before_loop
    async def before_monitor_fivem_players(self):
        await self.bot.wait_until_ready()

 #ABOUT AND UTILITY FUNCTIONS =========================================================================================
    @app_commands.command(name="clearbot", description="Delete recent bot messages in this channel")
    async def clearbot(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        deleted = 0
        async for message in interaction.channel.history(limit=100):
            if message.author == self.bot.user:
                await message.delete()
                deleted += 1
                await asyncio.sleep(0.5)  # To avoid rate limiting

        await interaction.followup.send(f"Deleted {deleted} bot messages.", ephemeral=True)

    @app_commands.command(name="about", description="Show information about the bot")
    async def about(self, interaction: discord.Interaction):
        em = newEmbed(title="About This Bot",
                    fields={
                        "Description": "This bot allows you to search for Steam player data and monitor Steam and FiveM players.",
                        "Commands": "/steam, /smonitor, /sstop, /fmonitor, /fstop, /flist, /clearbot, /clear, /about, /slist",
                        "Creator": "Z",
                        "Version": "0.1",
                    })
        await interaction.response.send_message(embed=em, ephemeral=True)
    @app_commands.command(name="clear", description="Delete all messages in this channel")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clear(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        if not interaction.channel.permissions_for(interaction.guild.me).manage_messages:
            await interaction.followup.send("I don't have the necessary permissions to delete messages in this channel.", ephemeral=True)
            return

        deleted = 0
        async for message in interaction.channel.history(limit=None):
            try:
                await message.delete()
                deleted += 1
                if deleted % 10 == 0:  
                    await interaction.followup.send(f"Deleted {deleted} messages so far...", ephemeral=True)
                await asyncio.sleep(0.5)  # To avoid rate limiting
            except discord.errors.NotFound:
                pass  # Message was already deleted
            except discord.errors.Forbidden:
                await interaction.followup.send("I don't have permission to delete some messages.", ephemeral=True)
                break

        await interaction.followup.send(f"Finished. Deleted a total of {deleted} messages.", ephemeral=True)

    @clear.error
    async def clearchannel_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.errors.MissingPermissions):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)

            
async def setup(bot : commands.Bot):
    await bot.add_cog(steamSearch(bot))