"""Симулятор источника Steam: детерминированные демо-данные.

Назначение:
* режим симуляции/офлайн (интернет не нужен);
* автотесты и DRY RUN демонстрации;
* первый запуск приложения «из коробки».

Данные помечаются источником ``simulation`` и при переходе на реальный
аккаунт заменяются синхронизацией через официальный Steam Web API.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.domain.value_objects import OwnedGame
from app.integrations.steam.base import ProfileInfo, SteamLibrarySource


@dataclass(frozen=True)
class SimMeta:
    name: str
    is_free: bool = False
    price: float = 0.0
    genres: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    release_year: int = 2020
    review_score: int = 85
    review_count: int = 10_000
    owners: int = 500_000
    third_party_launcher: bool = False
    third_party_account: bool = False
    anticheat: str = ""
    multiplayer: bool = False
    coop: bool = False


# Реалистичный набор (цены/метрики — ориентировочные, для демо).
SIM_GAMES: dict[int, SimMeta] = {
    108600: SimMeta("Project Zomboid", False, 799, ("Indie", "Simulation", "Survival"), ("Single-player", "Co-op"), 2013, 94, 180_000, 4_000_000, multiplayer=True, coop=True),
    251570: SimMeta("7 Days to Die", False, 915, ("Survival", "Open World", "Zombies"), ("Single-player", "Co-op", "Multi-player"), 2013, 88, 350_000, 8_000_000, multiplayer=True, coop=True),
    242760: SimMeta("The Forest", False, 399, ("Survival", "Horror", "Open World"), ("Single-player", "Co-op"), 2018, 92, 280_000, 9_000_000, multiplayer=True, coop=True),
    648800: SimMeta("Raft", False, 515, ("Survival", "Adventure", "Co-op"), ("Single-player", "Co-op", "Multi-player"), 2022, 93, 210_000, 7_000_000, multiplayer=True, coop=True),
    264710: SimMeta("Subnautica", False, 719, ("Survival", "Exploration", "Underwater"), ("Single-player"), 2018, 95, 240_000, 8_000_000),
    739630: SimMeta("Phasmophobia", False, 435, ("Horror", "Co-op", "Investigation"), ("Single-player", "Co-op", "Multi-player"), 2020, 91, 320_000, 12_000_000, multiplayer=True, coop=True),
    2096610: SimMeta("R.E.P.O.", False, 315, ("Horror", "Co-op", "Physics"), ("Co-op", "Multi-player"), 2025, 93, 90_000, 5_000_000, multiplayer=True, coop=True),
    2881650: SimMeta("Content Warning", False, 315, ("Horror", "Comedy", "Co-op"), ("Co-op", "Multi-player"), 2024, 90, 60_000, 6_000_000, multiplayer=True, coop=True),
    1097840: SimMeta("The Outlast Trials", False, 1099, ("Horror", "Co-op", "Stealth"), ("Single-player", "Co-op", "Multi-player"), 2024, 86, 40_000, 2_000_000, multiplayer=True, coop=True),
    602960: SimMeta("Barotrauma", False, 699, ("Submarine", "Survival", "Co-op"), ("Single-player", "Co-op", "Multi-player"), 2023, 90, 95_000, 2_500_000, multiplayer=True, coop=True),
    105600: SimMeta("Terraria", False, 259, ("Sandbox", "Survival", "2D"), ("Single-player", "Co-op", "Multi-player"), 2011, 97, 1_100_000, 30_000_000, multiplayer=True, coop=True),
    4000: SimMeta("Garry's Mod", False, 259, ("Sandbox", "Physics"), ("Single-player", "Multi-player"), 2006, 96, 950_000, 25_000_000, multiplayer=True, coop=True),
    1118200: SimMeta("People Playground", False, 259, ("Sandbox", "Simulation", "Gore"), ("Single-player"), 2019, 96, 140_000, 5_000_000),
    244850: SimMeta("Space Engineers", False, 515, ("Sandbox", "Space", "Building"), ("Single-player", "Co-op", "Multi-player"), 2019, 88, 190_000, 6_000_000, multiplayer=True, coop=True),
    892970: SimMeta("Valheim", False, 599, ("Survival", "Vikings", "Open World"), ("Single-player", "Co-op", "Multi-player"), 2021, 94, 260_000, 12_000_000, multiplayer=True, coop=True),
    252490: SimMeta("Rust", False, 1499, ("Survival", "PvP", "Open World"), ("Multi-player"), 2018, 87, 800_000, 15_000_000, multiplayer=True, coop=True, anticheat="EAC"),
    1966720: SimMeta("Lethal Company", False, 315, ("Horror", "Co-op", "Roguelike"), ("Co-op", "Multi-player"), 2023, 96, 300_000, 11_000_000, multiplayer=True, coop=True),
    413150: SimMeta("Stardew Valley", False, 299, ("Farming", "Relaxing", "Simulation"), ("Single-player", "Co-op", "Multi-player"), 2016, 98, 700_000, 20_000_000, multiplayer=True, coop=True),
    427520: SimMeta("Factorio", False, 999, ("Automation", "Strategy", "Base Building"), ("Single-player", "Co-op", "Multi-player"), 2020, 98, 190_000, 4_000_000, multiplayer=True, coop=True),
    526870: SimMeta("Satisfactory", False, 1099, ("Automation", "Open World", "Building"), ("Single-player", "Co-op", "Multi-player"), 2024, 96, 150_000, 5_500_000, multiplayer=True, coop=True),
    548430: SimMeta("Deep Rock Galactic", False, 515, ("Co-op", "FPS", "Dwarves"), ("Single-player", "Co-op", "Multi-player"), 2020, 96, 220_000, 7_000_000, multiplayer=True, coop=True),
    1086940: SimMeta("Baldur's Gate 3", False, 1999, ("RPG", "Turn-Based", "D&D"), ("Single-player", "Co-op", "Multi-player"), 2023, 96, 560_000, 15_000_000, multiplayer=True, coop=True),
    1245620: SimMeta("Elden Ring", False, 2599, ("Souls-like", "Open World", "RPG"), ("Single-player", "Multi-player"), 2022, 92, 700_000, 20_000_000, multiplayer=True),
    1091500: SimMeta("Cyberpunk 2077", False, 1999, ("RPG", "Open World", "Sci-Fi"), ("Single-player"), 2020, 88, 640_000, 18_000_000),
    553850: SimMeta("HELLDIVERS™ 2", False, 1299, ("Co-op", "Shooter", "Sci-Fi"), ("Co-op", "Multi-player"), 2024, 82, 500_000, 14_000_000, multiplayer=True, coop=True, anticheat="nProtect"),
    1623730: SimMeta("Palworld", False, 899, ("Survival", "Creature Collector", "Open World"), ("Single-player", "Co-op", "Multi-player"), 2024, 89, 300_000, 19_000_000, multiplayer=True, coop=True),
    271590: SimMeta("Grand Theft Auto V", False, 1299, ("Open World", "Action"), ("Single-player", "Multi-player"), 2015, 85, 1_700_000, 25_000_000, third_party_launcher=True, multiplayer=True),
    1174180: SimMeta("Red Dead Redemption 2", False, 2499, ("Open World", "Western"), ("Single-player", "Multi-player"), 2019, 90, 480_000, 12_000_000, third_party_launcher=True, multiplayer=True),
    306130: SimMeta("The Elder Scrolls Online", False, 999, ("MMO", "RPG"), ("Multi-player", "MMO"), 2014, 82, 130_000, 5_000_000, third_party_account=True, multiplayer=True),
    1151340: SimMeta("Fallout 76", False, 1299, ("Open World", "Survival", "Multiplayer"), ("Multi-player"), 2020, 78, 110_000, 5_000_000, third_party_account=True, multiplayer=True),
    945360: SimMeta("Among Us", False, 149, ("Social Deduction", "Party"), ("Single-player", "Co-op", "Multi-player"), 2018, 90, 550_000, 25_000_000, multiplayer=True, coop=True),
    632360: SimMeta("Risk of Rain 2", False, 419, ("Roguelike", "Co-op", "Shooter"), ("Single-player", "Co-op", "Multi-player"), 2020, 95, 210_000, 8_000_000, multiplayer=True, coop=True),
    1604030: SimMeta("V Rising", False, 999, ("Survival", "Vampires", "Open World"), ("Single-player", "Co-op", "Multi-player"), 2024, 90, 90_000, 5_000_000, multiplayer=True, coop=True),
    275850: SimMeta("No Man's Sky", False, 1299, ("Space", "Exploration", "Survival"), ("Single-player", "Co-op", "Multi-player"), 2016, 89, 250_000, 10_000_000, multiplayer=True, coop=True),
    215: SimMeta("Day of Defeat: Source", False, 219, ("FPS", "WWII"), ("Multi-player"), 2005, 92, 20_000, 4_000_000, multiplayer=True),
    1238810: SimMeta("Battlefield V", False, 1999, ("FPS", "WWII"), ("Multi-player"), 2018, 60, 70_000, 3_000_000, third_party_launcher=True, multiplayer=True),
    # --- F2P: не являются коммерческим преимуществом ---
    730: SimMeta("Counter-Strike 2", True, 0, ("FPS", "Competitive"), ("Multi-player"), 2023, 86, 8_000_000, 60_000_000, multiplayer=True, anticheat="VAC"),
    570: SimMeta("Dota 2", True, 0, ("MOBA", "Strategy"), ("Multi-player"), 2013, 81, 2_000_000, 80_000_000, multiplayer=True, anticheat="VAC"),
    1172470: SimMeta("Apex Legends", True, 0, ("Battle Royale", "FPS"), ("Multi-player"), 2020, 84, 700_000, 50_000_000, third_party_account=True, multiplayer=True, anticheat="EAC"),
    236390: SimMeta("War Thunder", True, 0, ("Military", "Vehicles"), ("Multi-player"), 2013, 78, 500_000, 30_000_000, multiplayer=True, anticheat="EAC"),
}

# Аккаунты демо-семьи: 4 аккаунта, дубликаты копий осознанные.
SIM_ACCOUNTS: dict[str, dict] = {
    "76561198000000001": {
        "name": "Family-Host (Main)",
        "games": [108600, 251570, 242760, 648800, 264710, 739630, 2096610, 105600, 892970,
                  1966720, 413150, 427520, 1086940, 1245620, 1091500, 271590, 1174180,
                  730, 570, 1172470, 215, 945360],
    },
    "76561198000000002": {
        "name": "Backup-Account",
        "games": [108600, 739630, 2881650, 1097840, 602960, 252490, 526870, 548430,
                  553850, 1623730, 632360, 236390, 4000],
    },
    "76561198000000003": {
        "name": "Coop-Account",
        "games": [108600, 242760, 648800, 105600, 4000, 1118200, 244850, 275850,
                  1604030, 945360, 730, 570],
    },
    "76561198000000004": {
        "name": "Cheap-Keys Account",
        "games": [306130, 1151340, 1238810, 215, 275850, 413150, 632360, 236390],
    },
}


class SimulationSteamSource(SteamLibrarySource):
    name = "simulation"

    def fetch_profile(self, steam_id64: str) -> ProfileInfo:
        info = SIM_ACCOUNTS.get(str(steam_id64))
        name = info["name"] if info else f"Simulated {steam_id64[-4:]}"
        return ProfileInfo(
            steam_id64=str(steam_id64),
            persona_name=name,
            avatar_url="",
            profile_url=f"https://steamcommunity.com/profiles/{steam_id64}",
            community_visibility=3,
            loc_country="RU",
        )

    def fetch_owned_games(self, steam_id64: str) -> list[OwnedGame]:
        info = SIM_ACCOUNTS.get(str(steam_id64))
        if info is None:
            return []
        games: list[OwnedGame] = []
        for app_id in info["games"]:
            meta = SIM_GAMES[app_id]
            games.append(
                OwnedGame(
                    app_id=app_id,
                    name=meta.name,
                    is_free=meta.is_free,
                    playtime_minutes=(app_id * 37) % 9000,
                    family_shared=False,
                )
            )
        return games

    def check_available(self) -> tuple[bool, str]:
        return True, "Режим симуляции: используются локальные демо-данные."
