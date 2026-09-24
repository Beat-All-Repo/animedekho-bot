# AnimeDekho Telegram Bot 🎌

Telegram bot to search, browse, and download anime from [AnimeDekho](https://animedekho.app/) — Hindi, Tamil & Telugu dubbed.

Built with **WZGram (High-Performance MTProto Fork with WarpCrypto)** for **2GB upload/download support**, multi-server fallbacks, child worker bots load balancing, userbot per-anime channel creation, autonomous AI agent, and real-time health monitoring.

---

> [!NOTE]
> **Graceful Fallback / Zero-Config Architecture**:
> All newly added advanced capabilities (**Userbot**, **Child Worker Bots**, **Per-Anime Dedicated Channels**, and **Autonomous AI Agent**) are **completely optional**.
> - If you do not log in to a userbot, the bot will upload directly to user chats and your main library channel as before.
> - If you do not register child bots, all downloads and deep links route through your main bot.
> - If no AI key is configured, standard search and keyboard navigation operate normally.
> 
> Everything works out-of-the-box in standard standalone mode if you only provide the core credentials!

---

## Features

### 🔍 Core Search & Browsing
- **Instant Search** — Send any anime name in chat to search.
- **Seasons & Episodes** — Complete pagination with interactive inline buttons.
- **Multi-Server Streaming** — Extracts real direct CDN video player streams from multiple providers.
- **Dual Formats** — Supports anime series (episodes grouped by season) and standalone movies.
- **Quality Selection** — 360p, 480p, 720p, 1080p, and enhanced 4K tiers.
- **Batch Downloads** — Download entire seasons sequentially with automatic server fallbacks.
- **Duplicate Prevention** — Downloaded files are indexed in MongoDB by `file_unique_id`; identical files are delivered in milliseconds from cache without re-downloading.

### 🌐 Multi-Source & Enhanced 4K Resolution
- **Multi-Source Fallback Cascade**:
  1. Primary: **AnimeDekho** (multi-server CDN streams)
  2. Secondary: **AnimeDrive** (automatic fallback for missing episodes)
  3. Tertiary: **ToonFlix** (tertiary fallback)
- **4K Tier Scoring** — Automatically identifies and downloads the highest available quality above 1080p (e.g. 1080p HQ x265, 1080p 10-bit, 2160p).

### 🎨 AniList Official HD Poster & Key Visuals
- **Authoritative AniList Integration** — Queries the **AniList GraphQL API** to retrieve official, high-resolution anime key visuals and cover art (`coverImage.extraLarge`).
- **Smart Title Normalization** — Automatically cleans dub tags (e.g. `(Hindi Dubbed)`, `[Multi Audio]`, `Dual Audio`), quality labels (`1080p`, `720p`), and season tags into progressive query candidates.
- **Junk & Banner Filtering** — Automatically detects and filters out generic website banners (e.g. `banner-112.webp`), header logos, and placeholders.
- **Graceful Fallback** — If AniList has no match or is unreachable, seamlessly falls back to valid scraped artwork without blocking downloads.
- **Channel Avatars & Album Cards** — The official AniList visual is automatically applied to dedicated per-anime channels, main channel album cards, and video upload thumbnails.

### 👷 Child Worker Bot Network (Load Balancing)
- **Multi-Bot Worker Fleet** — Connect unlimited child worker bots (`/addbot`) to distribute user downloads and avoid single-bot Telegram rate limits.
- **Quality-Tier Assignment** — Assign dedicated child bots to specific resolutions (e.g., Worker 1 for `1080p`, Worker 2 for `720p`, Worker 3 for `480p`).
- **Dynamic Deep Links** — Channel posters automatically route user download clicks to the assigned worker bots via round-robin balancing.
- **Instant Batch Refresh** — Use `/refreshalbums` to retroactively update all existing channel album posts with new worker bot links.

### 👤 Userbot & Dedicated Per-Anime Channels
- **MTProto Userbot Login**:
  - Interactive wizard (`/login`) with step-by-step phone number, OTP code, and 2FA cloud password handling.
  - Or direct login (`/login <string_session>`) using an existing WZGram/Pyrogram string session.
- **Per-Anime Dedicated Channels**:
  - Automatically creates a dedicated private Telegram channel per anime series.
  - Sets the anime poster as the channel's profile photo.
  - Promotes the Main Bot and all active Child Bots as administrators with full posting privileges.
  - Generates permanent invite links.
- **Channel Mapping & Routing**:
  - Automatically routes episode uploads into the dedicated anime channel.
  - Direct delivery to users in private chat via cached `file_id` (0 bandwidth overhead).
- **Poster Album Display Modes (`/albummode`)**:
  - `channel` *(default)*: Main channel poster album displays a prominent `[📢 Watch / Episodes Channel]` button linking directly to the series channel.
  - `both`: Displays the channel button at the top, followed by individual quality buttons.
  - `direct`: Traditional direct download buttons only.

### 🤖 Autonomous AI Agent
- **Natural Language Assistant** (`/ai <prompt>`): Ask the AI to find anime, inspect streams, check episodes, or trigger downloads autonomously.
- **Configurable Models (`/setai`)**: Supports OpenAI (`gpt-4o`, `gpt-4o-mini`), Google Gemini (`gemini-2.0-flash`), OpenRouter, and custom endpoints.
- **Long-Term Memory**: Autonomous memory tools (`remember_fact`, `recall_facts`) persist user preferences across sessions.
- **Channel Tools**: AI can check channel mappings and create dedicated anime channels.

### 🩺 System Health & Diagnostics
- **Network Health Dashboard (`/health`)**:
  - Tests connectivity and measures round-trip ping latency for the **Main Bot**, **Userbot**, and all **Child Worker Bots**.
  - Displays hardware metrics: CPU load %, RAM usage, Disk space, and MongoDB ping latency.
  - Reports process uptime (e.g. `2d 4h 12m`).
- **Download Error Tracker (`/errors`)**:
  - Automatically logs failed downloads to MongoDB with timestamps, series title, quality, source attempted, and error messages.
  - 24-hour error counters and recent failure inspection.
- **Environment Log Buffer (`/logs`)**:
  - In-memory ring buffer keeps the latest 150 log events.
  - Filter by error logs or export full logs as a `.txt` document file (`/logs export`).

---

## Bot Commands

### 👥 User Commands
| Command | Description |
| :--- | :--- |
| Any text | Search for anime series or movies |
| `/start` | Open the main menu |
| `/search <query>` | Search anime by title |
| `/help` | Show user help message |

### 👑 Owner & Admin Commands

#### System Health & Diagnostics
| Command | Description |
| :--- | :--- |
| `/health` (or `/status`) | Interactive health dashboard with bot pings, uptime, RAM/CPU/Disk metrics, and error summary |
| `/health errors` (or `/errors`) | View recent download failures with exact error reasons |
| `/health logs` (or `/logs`) | View recent environment log events in Telegram |
| `/logs export` | Export the latest 150 log records as a `.txt` file document |
| `/logs errors` | View only error-level environment logs |
| `/clearerrors` | Clear all logged download error history from the database |

#### Child Worker Bots (Load Balancing)
| Command | Description |
| :--- | :--- |
| `/addbot <token> [quality]` | Add a child worker bot with optional quality tier (`1080p`, `720p`, `480p`, `all`) |
| `/delbot <username or id>` | Remove a child worker bot |
| `/bots` | List all child worker bots, assigned qualities, and files served |
| `/setbotquality <bot> <quality>` | Update a child bot's assigned quality tier |
| `/refreshalbums` | Update all main channel album posts with new child bot links |

#### Userbot & Per-Anime Channels
| Command | Description |
| :--- | :--- |
| `/login` | Start interactive phone login wizard or `/login <string_session>` |
| `/logout` | Disconnect userbot and remove session from database |
| `/userbot` | View userbot session status and channel mapping settings |
| `/cancel` | Cancel an ongoing interactive login wizard |
| `/autochannel <on\|off>` | Toggle automatic channel creation during series downloads |
| `/albummode <channel\|both\|direct>` | Set poster album display mode in main channel |
| `/createchannel <slug or title>` | Manually create and map a dedicated channel for an anime |
| `/mapchannel <slug> <channel_id> [link]` | Manually map an existing Telegram channel to an anime slug |
| `/unmapchannel <slug>` | Remove channel mapping for an anime slug |
| `/channels` | List all mapped anime series channels with invite links |

#### Autonomous AI Agent
| Command | Description |
| :--- | :--- |
| `/ai <prompt>` | Query the autonomous AI agent |
| `/setai` | View or configure AI provider, API key, model, and system persona |

#### Access & User Management
| Command | Description |
| :--- | :--- |
| `/adduser <id>` | Approve a user to access the bot |
| `/removeuser <id>` | Revoke user access |
| `/users` | List approved user IDs |
| `/setchannellink <url>` | Set force-subscribe invite link |
| `/delete` | Interactive menu to delete files or entire series from library |

---

## Environment Variables

| Variable | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `BOT_TOKEN` | ✅ | — | Telegram Bot API token from [@BotFather](https://t.me/BotFather) |
| `API_ID` | ✅ | — | Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | ✅ | — | Telegram API Hash from [my.telegram.org](https://my.telegram.org) |
| `OWNER_ID` | ✅ | — | Your numeric Telegram user ID |
| `MONGO_URI` | ✅ | — | MongoDB connection string (local or MongoDB Atlas) |
| `MAIN_CHANNEL` | ❌ | `0` | Telegram Channel ID for album library posts (e.g. `-1001234567890`) |
| `LOG_CHANNEL` | ❌ | `0` | Telegram Channel ID for live event and audit logs |
| `LOG_LEVEL` | ❌ | `INFO` | Console logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `AI_API_KEY` | ❌ | `""` | Optional API key for OpenAI, Gemini, or OpenRouter |
| `AI_MODEL` | ❌ | `gpt-4o-mini` | Optional default AI model |

---

## Project Structure

```
├── main.py                     # Application entrypoint
├── setup.sh                    # Automated VPS/Docker installer
├── config/settings.py          # Pydantic environment configuration
├── api/
│   ├── models.py               # Anime, Series, Episode & Server data models
│   ├── parser.py               # HTML parsers for AnimeDekho
│   └── client.py               # Async API client with session management
├── extractors/
│   ├── resolver.py             # CDN player extractors & m3u8 parser
│   ├── animedrive.py           # AnimeDrive multi-server stream extractor
│   ├── toonflix.py             # ToonFlix stream extractor
│   └── shortener.py            # Link shortener bypass (gplinks, vshort, cuty)
├── bot/
│   ├── app.py                  # WZGram app factory & lifecycle hooks
│   ├── telegram.py             # Unified Telegram MTProto client provider (WZGram / Pyrogram)
│   ├── auth.py                 # User authorization & owner guard
│   ├── child_bots.py           # Child worker bots manager & load balancer
│   ├── userbot.py              # MTProto userbot session & channel creator
│   ├── health.py               # System diagnostics, uptime & log buffer
│   ├── database.py             # MongoDB async driver (motor)
│   ├── downloader.py           # Video download engine & MTProto uploader
│   ├── forcesub.py             # Force-subscribe verification
│   ├── keyboards.py            # Inline keyboard builders
│   ├── library.py              # Main channel poster album manager
│   ├── logger.py               # Telegram log channel dispatcher
│   ├── ai/                     # Autonomous AI Agent engine & function tools
│   │   ├── agent.py            # ReAct autonomous loop
│   │   ├── config.py           # AI model & persona configuration
│   │   └── tools.py            # Function calling tools
│   └── handlers/               # Command, callback, and message handlers
│       ├── admin.py            # Owner commands & health callbacks
│       ├── admin_ai.py         # AI administration commands
│       ├── callbacks.py        # Inline button router & download handlers
│       ├── commands.py         # Basic user commands (/start, /help)
│       └── messages.py         # Search query & login wizard input handler
└── utils/
    ├── cache.py                # In-memory TTL cache
    ├── http.py                 # Async HTTP client with connection pooling
    └── helpers.py              # Formatting & slug helpers
```

---

## Deployment Guides

### Option 1: Docker / VPS (Recommended)

Run on any Ubuntu/Debian VPS (AWS EC2, DigitalOcean, Hetzner, etc.):

```bash
# Clone the repository
git clone https://github.com/jrodr254/animedekho-bot.git
cd animedekho-bot

# Run the automated setup script
bash setup.sh
```

The script will:
1. Install Docker Engine and Docker Compose.
2. Prompt for environment variables (`BOT_TOKEN`, `API_ID`, `API_HASH`, `OWNER_ID`, etc.) and write `.env`.
3. Build the container with Python 3.11, ffmpeg, and MongoDB.
4. Launch the bot daemon.

**Manage containers:**
```bash
docker compose logs -f          # Live logs
docker compose restart bot      # Restart bot
docker compose down             # Stop containers
docker compose up -d --build    # Rebuild & start
```

---

### Option 2: Railway Deployment

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template)

1. Fork this repository.
2. Create a project on [Railway](https://railway.app) and select your fork.
3. Add a **MongoDB** service (one-click in Railway).
4. Set required environment variables (`BOT_TOKEN`, `API_ID`, `API_HASH`, `OWNER_ID`, `MONGO_URI`).
5. Deploy!

---

### Option 3: Local Development

```bash
# 1. Clone repository
git clone https://github.com/Tgbotworld/animedekho-bot.git
cd animedekho-bot

# 2. Install dependencies
pip install -r requirements.txt

# 3. Install ffmpeg
# Ubuntu/Debian:
sudo apt install ffmpeg
# macOS:
brew install ffmpeg

# 4. Set environment variables
export BOT_TOKEN="your_bot_token"
export API_ID="123456"
export API_HASH="your_api_hash"
export OWNER_ID="your_telegram_id"
export MONGO_URI="mongodb://localhost:27017"

# 5. Run the bot
python main.py
```

---

## License

Distributed under the MIT License. See `LICENSE` for more information.
