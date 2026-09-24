"""Telegram errors re-exporter from WZGram / Pyrogram."""

try:
    from wzgram.errors import *
except ImportError:
    from pyrogram.errors import *
