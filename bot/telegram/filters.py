"""Telegram filters re-exporter from WZGram / Pyrogram."""

try:
    from wzgram.filters import *
except ImportError:
    from pyrogram.filters import *
