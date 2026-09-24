"""Telegram enums re-exporter from WZGram / Pyrogram."""

try:
    from wzgram.enums import *
except ImportError:
    from pyrogram.enums import *
