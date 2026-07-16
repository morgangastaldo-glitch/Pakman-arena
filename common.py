"""
Costanti condivise, mappa e helper di protocollo per Pac-Man Arena 1vAll.
"""
import json
import string

DEFAULT_PORT = 8765

TICK_HZ = 20
TICK_DT = 1.0 / TICK_HZ

COUNTDOWN_SECONDS = 15
ROUND_SECONDS = 120
MAX_PLAYERS = 5
MIN_PLAYERS = 2

NORMAL_SPEED = 4.5          # celle al secondo
KILLER_SPEED_MULT = 1.25    # come richiesto: killer 1.25x rispetto a 1.0 dei giocatori

# Nome colore (mostrato all'utente, in italiano) -> id colore interno
COLORS = ["azzurro", "giallo", "verde", "bianco", "rosa"]

# Mappa in stile Pac-Man: muri simmetrici, dimensione compatta per terminale.
# Generata proceduralmente e verificata per connettivita' totale (nessuna zona isolata).
MAZE = [
    "###################",
    "#.................#",
    "#.#.###.#.#.###.#.#",
    "#.................#",
    "#.#.###.###.###.#.#",
    "#.................#",
    "#.#.#####.#####.#.#",
    "#.#.............#.#",
    "#.#.#.##...##.#.#.#",
    "#.#.............#.#",
    "#.###.##...##.###.#",
    "#.................#",
    "#.#.#.###.###.#.#.#",
    "#.....#.....#.....#",
    "#.#.###.#.#.###.#.#",
    "#.......#.#.......#",
    "#.#.#####.#####.#.#",
    "#.................#",
    "###################",
]
MAZE_W = len(MAZE[0])
MAZE_H = len(MAZE)

# 5 punti di spawn ben distanziati, uno per possibile giocatore
SPAWN_POINTS = [(1, 1), (17, 1), (1, 17), (17, 17), (9, 9)]

DIRECTIONS = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}

ROOM_CODE_CHARS = "".join(c for c in string.ascii_uppercase + string.digits if c not in "0O1I")


def is_wall(x, y):
    if x < 0 or y < 0 or y >= MAZE_H or x >= MAZE_W:
        return True
    return MAZE[y][x] == "#"


def encode(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
