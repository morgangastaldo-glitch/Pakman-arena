"""
Costanti condivise, mappe e helper di protocollo per Pac-Man Arena 1vAll.
"""
import json
import random
import string

DEFAULT_PORT = 8765

# 60Hz invece di 30Hz: raddoppia la frequenza con cui il server calcola
# fisica/collisioni e manda correzioni di stato ai client. Le velocita' sono
# espresse in celle/secondo quindi il bilanciamento del gioco NON cambia
# (a 60Hz ogni tick avanza semplicemente la meta' di spazio rispetto a
# prima); a beneficiarne sono la precisione delle collisioni col killer
# (la cella "attraversata" viene controllata il doppio delle volte, quindi
# si notano meno gli "attraversamenti fantasma" ad alta velocita') e la
# riconciliazione client-side, che deve correggere scarti piu' piccoli e
# piu' spesso invece di scarti piu' grandi e piu' radi: e' proprio questo
# che si traduce in un movimento remoto percepito come piu' fluido, oltre
# ad avvicinare il tickrate del server al refresh rate tipico di un monitor
# desktop (60/120/144Hz), a cui il client renderizza gia' via
# requestAnimationFrame.
TICK_HZ = 60
TICK_DT = 1.0 / TICK_HZ

COUNTDOWN_SECONDS = 15
ROUND_SECONDS = 120
KILLER_INTERVAL_SECONDS = 15  # ogni quanto il killer cambia casualmente durante il round
MAX_PLAYERS = 5
MIN_PLAYERS = 2

NORMAL_SPEED = 4.5          # celle al secondo
KILLER_SPEED_MULT = 1.1     # come richiesto: killer 1.1x rispetto a 1.0 dei giocatori

# ---- sistema punti e bonus a traguardi ----
# Ogni pallino della mappa vale 1 punto. Al raggiungimento di ogni soglia
# (una sola volta per round) scatta il bonus corrispondente.
BONUS_THRESHOLDS = [
    (50,  "extra_life"),    # +1 vita: se il killer ti prende, respawni invece di uscire
    (100, "laser"),         # sblocca il laser: un colpo singolo (proiettile) ogni secondo
    (150, "laser_bounce"),  # i colpi laser rimbalzano sui muri invece di sparire
    (200, "mines"),         # sblocca 3 mine sganciabili sulla mappa
]
BOOST_MULT = 2.0               # (bonus rimosso dal gioco, costante tenuta per compatibilita')
BOOST_SECONDS = 15.0           # (bonus rimosso dal gioco, costante tenuta per compatibilita')
GHOST_SECONDS = 10.0           # (bonus rimosso dal gioco, costante tenuta per compatibilita')
SPAWN_PROTECT_SECONDS = 3.0    # invulnerabilita' (solo dal killer) dopo un respawn
LASER_INTERVAL_SECONDS = 1.0   # ogni quanto il laser spara un colpo, una volta sbloccato (1 al secondo)
LASER_FIRST_DELAY_SECONDS = 1.0  # attesa del primo colpo dopo lo sblocco
LASER_PROJECTILE_SPEED = 20.0  # celle al secondo percorse dal proiettile laser (raddoppiata: e' un proiettile vero, deve sentirsi veloce)
LASER_BOUNCE_DISTANCE = 12     # celle percorribili dopo il primo rimbalzo su una parete (bonus 150 punti)
MINES_COUNT = 3                # numero di mine disponibili una volta sbloccato il bonus 200 punti
MINE_DOUBLE_TAP_MS = 350       # finestra (ms) del doppio tocco freccia destra/D che sgancia una mina (uso lato client)
PORTAL_COOLDOWN_SECONDS = 1.2  # anti ping-pong: dopo un teletrasporto i portali si ignorano per un attimo

# Nome colore (mostrato all'utente, in italiano) -> id colore interno.
# Elenco esteso: ogni giocatore puo' scegliere fino a 2 colori (primario +
# dettaglio/contorno), vedi Player.colors in main.py e COLOR_HEX nel
# client (index.html) per i valori esadecimali corrispondenti.
COLORS = [
    "azzurro", "giallo", "verde", "bianco", "rosa",
    "arancione", "rosso", "viola", "lime", "oro",
    "ciano", "magenta", "grigio", "marrone", "blu_notte", "corallo",
]

# Personaggi selezionabili in lobby. La forma/dettagli di ciascuno sono
# disegnati lato client (index.html); qui serve solo l'elenco degli id
# validi per la validazione server-side.
CHARACTERS = ["classic", "shark", "hex", "cyclops", "angry"]

DIRECTIONS = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}

ROOM_CODE_CHARS = "".join(c for c in string.ascii_uppercase + string.digits if c not in "0O1I")

# 10 mappe distinte, generate proceduralmente con un labirinto perfetto
# (recursive backtracker) poi "braided" per aggiungere anelli percorribili
# e specchiate in orizzontale per la classica simmetria da arcade.
# Ognuna e' stata verificata via flood-fill: tutte le celle libere sono
# raggiungibili tra loro (nessuna zona isolata) => sempre giocabile.
MAZES = [
    {
        "name": 'Neon Blu',
        "maze": [
            '###############################',
            '#.........#...........#.....#.#',
            '#.#.#.#.###.#.###.###.###.#.#.#',
            '#...#.#.....#...#...#...#.#...#',
            '#.#######.###.#.#.#.###.#.#####',
            '#.....#.....#.#.#.#.....#.#...#',
            '###.#.#.###.#.#.#.#####.#.#.#.#',
            '#.....#.#...#.#.#.#.......#.#.#',
            '#.#####.#.#.#.#.#.#.#####.#.#.#',
            '#.#.......#.....#.#.#.......#.#',
            '#.###.###.#######.#.#########.#',
            '#...#...#.....#...#.....#.....#',
            '###.#####.#.#.#.#.#####.#.###.#',
            '#.........#.#.............#...#',
            '###############################',
        ],
        "spawn_points": [[1, 1], [29, 1], [1, 13], [29, 13], [15, 7]],
        "theme": {'wall': '#0a1440', 'edge': '#2b4bd6', 'glow': '#4d7bff', 'pellet': '#ffe9a8', 'bg': '#000000', 'fx': 'neon'},
    },
    {
        "name": 'Lava Cremisi',
        "maze": [
            '#################################',
            '#.#...#.......#.......#.......#.#',
            '#.#.#.#.#.###.#.#.#.#.#.#####.#.#',
            '#.#.#...#...#.#.#...#.#.........#',
            '#.#.#######.#.###.###.#####.#.#.#',
            '#.#...#.....#.#.............#...#',
            '#.#.#.#.###.#.#.###.#############',
            '#.....#.#...#.#...#.............#',
            '#.#####.#####.#.#.#.#.#########.#',
            '#.....#.....#.#.#.............#.#',
            '#.###.#.###.#.###.#######.#####.#',
            '#.#...#...#.#...#.#...#...#.....#',
            '#.#.#####.#.#.#.#.#.#.#####.###.#',
            '#...........#...#...#.#.......#.#',
            '#.###.#####.#.###.###.#.#####.#.#',
            '#.........#.......#.....#.......#',
            '#################################',
        ],
        "spawn_points": [[1, 1], [31, 1], [1, 15], [31, 15], [16, 7]],
        "theme": {'wall': '#3a0505', 'edge': '#ff3b3b', 'glow': '#ff7a5c', 'pellet': '#ffd166', 'bg': '#0a0000', 'fx': 'embers'},
    },
    {
        "name": 'Giungla Smeraldo',
        "maze": [
            '#############################',
            '#.........#...........#.....#',
            '###.#####.#.#####.#.#.#.###.#',
            '#.......#.#.#...#.#.#.#.#...#',
            '#.###.#.#.#.#.#.#.#.#.#.#.#.#',
            '#.....#.#.#...#.#.#.#.#...#.#',
            '#.#######.#####.#.#.#.###.#.#',
            '#...#.....#.#...#.#.#.....#.#',
            '#.#.#.#####.#.###.#.#######.#',
            '#.#...#.......#...#...#.....#',
            '#.#######.#####.###.###.#####',
            '#.......#...#...#.#...#.#...#',
            '#####.#.#####.###.#.#.#.#.#.#',
            '#.............#.....#.......#',
            '#############################',
        ],
        "spawn_points": [[1, 1], [27, 1], [1, 13], [27, 13], [14, 7]],
        "theme": {'wall': '#03301c', 'edge': '#12c96f', 'glow': '#5dffb0', 'pellet': '#e8ff8f', 'bg': '#000e08', 'fx': 'leaves'},
    },
    {
        "name": 'Violetto Regale',
        "maze": [
            '#####################################',
            '#...#.....#...............#.........#',
            '###.#.###.###.#.#########.#.###.###.#',
            '#.#...#.#.....#.#.....#...#.....#...#',
            '#.#####.###.###.#.#####.###.#.###.#.#',
            '#.........#.....#.......#.......#...#',
            '#.#######.###########.#.###.###.###.#',
            '#.#.....#...#.........#...#...#.....#',
            '#.#.###.###.###.###.###.#.###.#####.#',
            '#.#...#.#.......#...........#.#.....#',
            '#.#.#.#.#######.#.#########.#.#.###.#',
            '#.#...#.....#...#...#.....#.....#...#',
            '#.###.###.#.#######.###.#.#.#.#.#.#.#',
            '#.#.......#.....#...#...#.#...#...#.#',
            '#.#.#.#.#.#.###.#.###.###.#####.###.#',
            '#.#...#.#...#...#.#...#.#.#.....#...#',
            '#.###.###.#.#.#.#.#.###.#.#.#.#.#.###',
            '#.........#.........#.......#...#...#',
            '#####################################',
        ],
        "spawn_points": [[1, 1], [35, 1], [1, 17], [35, 17], [18, 9]],
        "theme": {'wall': '#210a3a', 'edge': '#9b3bff', 'glow': '#c68cff', 'pellet': '#ffe2f7', 'bg': '#08000f', 'fx': 'sparkle'},
    },
    {
        "name": 'Sabbia Ambra',
        "maze": [
            '###########################',
            '#.......#...#...#.........#',
            '###.#.#.#.#.#.#.#.###.#.#.#',
            '#...#.....#...#.....#...#.#',
            '#.#.#######.#######.###.#.#',
            '#.#...........#.....#...#.#',
            '#.#.#####.###.###.#.#.#.###',
            '#.#.......#.......#...#...#',
            '#.###.#.#####.#######.###.#',
            '#.#...#.....#.#...#.....#.#',
            '#.#.###.###.###.#.#######.#',
            '#.#.#.....#.......#.......#',
            '#.#.#.#.###.#.###.#.#####.#',
            '#...#.#.......#.....#.....#',
            '###########################',
        ],
        "spawn_points": [[1, 1], [25, 1], [1, 13], [25, 13], [13, 7]],
        "theme": {'wall': '#402706', 'edge': '#ff9d1f', 'glow': '#ffc266', 'pellet': '#fff3c4', 'bg': '#0d0700', 'fx': 'sand'},
    },
    {
        "name": 'Ghiaccio Ciano',
        "maze": [
            '###################################',
            '#...#.....#.........#...........#.#',
            '###.###.#.#.###.###.#.#####.###.#.#',
            '#.#.#...#.......#...#.....#.....#.#',
            '#.#.#.###.#.#.###.#######.#.#####.#',
            '#...#.#...#...#.#.#.......#.#.....#',
            '#.###.#.#.###.#.#.#.###.###.###.#.#',
            '#.#...#.....#...#.#.#.....#...#...#',
            '#.#.###.###.#.#.#.#.#.#######.###.#',
            '#.....#...#...#.#...#.#.....#.....#',
            '#.###.#.#.###.#######.#.###.###.#.#',
            '#.#...#.#.#...#...#...#.......#...#',
            '#.#.###.#.#.#.#.#.#.#.#.#.###.###.#',
            '#...#.#.......#.#...#.#.#.....#...#',
            '#####.#.#.#####.#####.#.#.#####.###',
            '#.......#...........#...#.........#',
            '###################################',
        ],
        "spawn_points": [[1, 1], [33, 1], [1, 15], [33, 15], [17, 8]],
        "theme": {'wall': '#052a33', 'edge': '#22e6ff', 'glow': '#9df6ff', 'pellet': '#ffffff', 'bg': '#000a0d', 'fx': 'snow'},
    },
    {
        "name": 'Rosa Arcade',
        "maze": [
            '###############################',
            '#...#.....#...............#...#',
            '###.#.###.#####.#.#########.#.#',
            '#.#...#...#.....#...#.......#.#',
            '#.#.#.#.###.#####.#.#.#######.#',
            '#.#.#.#.#.#.......#.....#.#...#',
            '#.#.#.#.#.#.###########.#.#.###',
            '#.#...#.#.......#...#...#.....#',
            '#.#.#.#.###.###.#.#.#.###.###.#',
            '#...#.#...........#.#.......#.#',
            '#.#.#.###.#.###.#.#.#.#####.#.#',
            '#.#.......#.#.....#.....#.....#',
            '#.###.###.#.#.#.#.#####.#.###.#',
            '#.......#...#.#.#.#...#.#...#.#',
            '###.###.#.#.#.#.#.#.###.###.#.#',
            '#.......#...#.....#.........#.#',
            '###############################',
        ],
        "spawn_points": [[1, 1], [29, 1], [1, 15], [29, 15], [15, 8]],
        "theme": {'wall': '#3a0524', 'edge': '#ff2b9e', 'glow': '#ff8fce', 'pellet': '#fff0f8', 'bg': '#0d0009', 'fx': 'hearts'},
    },
    {
        "name": 'Foresta Notte',
        "maze": [
            '#################################',
            '#.#...............#.............#',
            '#.#.#.###########.#######.###.#.#',
            '#.#...#.....#...#.......#...#.#.#',
            '#.#.#.###.#.#.#.#######.#.#.#.#.#',
            '#.#.#...#.....#.......#.#...#.#.#',
            '#.#.#.#.#.#####.###.#.#.#####.#.#',
            '#...#.#.#.......#.....#.........#',
            '###.#.#.#####.#####.###.#.###.#.#',
            '#.#...#.....#.#...#...#.#...#...#',
            '#.###.#.###.#.#.#.#####.###.#.###',
            '#.#...#.....#...#.....#.....#...#',
            '#.#.#####.###.#######.###.###.#.#',
            '#.........#.........#...........#',
            '#################################',
        ],
        "spawn_points": [[1, 1], [31, 1], [1, 13], [31, 13], [15, 7]],
        "theme": {'wall': '#0c2410', 'edge': '#3ddc4a', 'glow': '#9dffa5', 'pellet': '#f4ffb8', 'bg': '#020a03', 'fx': 'fireflies'},
    },
    {
        "name": 'Corallo Tramonto',
        "maze": [
            '#######################################',
            '#...#.......#...............#.........#',
            '###.#.#.###.#.#####.#######.#.#######.#',
            '#.#.#.#...#...#.........#.#.#...#...#.#',
            '#.#.#####.#####.#.###.#.#.#.###.###.#.#',
            '#.#.......#.#...#.....#.#.#.....#...#.#',
            '#.#########.#.#######.#.#.#######.#.#.#',
            '#...................#...#...#.....#...#',
            '#.#####.#########.###.#.###.#.###.###.#',
            '#.#.....#.........#...#.....#...#.....#',
            '#.#.#.#.#.#.#####.#.#.#####.#.#.#.#####',
            '#.#.#.#...#.#.....#.#.....#.#...#.#...#',
            '#.#.#.###.#.#.#.###.#.###.#.#.###.#.#.#',
            '#...#.#...#...#.....#.#.#.#.........#.#',
            '#.#.#.#.###.#####.###.#.#.#####.#.###.#',
            '#.#.#.#.#.#.#...#.#...#.#.....#.#.#...#',
            '#.#.#.#.#.#.#.#.###.###.#.#.#.#.###.#.#',
            '#.........#...#.........#...#.......#.#',
            '#######################################',
        ],
        "spawn_points": [[1, 1], [37, 1], [1, 17], [37, 17], [19, 9]],
        "theme": {'wall': '#3a1005', 'edge': '#ff5a36', 'glow': '#ffb08a', 'pellet': '#ffe3c2', 'bg': '#0d0300', 'fx': 'bubbles'},
    },
    {
        "name": 'Indaco Profondo',
        "maze": [
            '###################################',
            '#.....#.#.......#.#...............#',
            '#####.#.#.#.###.#.#.#########.###.#',
            '#.#...#...#.#.#.#...#.........#...#',
            '#.#.#####.#.#.#.#.###.###.#.###.#.#',
            '#.#.....#.#.#.#.#.#...#.#...#.#...#',
            '#.#####.#.#.#.#.###.###.#####.###.#',
            '#.....#...#.#.......#.......#.....#',
            '#.###.#.###.#.#######.#####.#.###.#',
            '#...#.......#.....#...#.#...#.#...#',
            '###.#.#########.#.#.###.#.###.###.#',
            '#...#...........#.#...#.#.....#...#',
            '#.###.###########.###.#.#######.#.#',
            '#.#...#.....#...#...#.......#.....#',
            '#.#####.#.#.#.#.#.#########.#.#.#.#',
            '#...#...#.#...#...#.............#.#',
            '#.#.#.###.#########.###########.#.#',
            '#.#.....#.......................#.#',
            '###################################',
        ],
        "spawn_points": [[1, 1], [33, 1], [1, 17], [33, 17], [17, 9]],
        "theme": {'wall': '#0a0a3a', 'edge': '#5b6bff', 'glow': '#a6b0ff', 'pellet': '#e6e9ff', 'bg': '#020214', 'fx': 'stars'},
    },
]

def pick_random_maze():
    """Sceglie casualmente una delle 10 mappe. Ritorna un dict con
    maze/w/h/spawn_points/theme/name pronto da assegnare a una Room."""
    m = random.choice(MAZES)
    rows = m["maze"]
    return {
        "name": m["name"],
        "maze": rows,
        "w": len(rows[0]),
        "h": len(rows),
        "spawn_points": m["spawn_points"],
        "theme": m["theme"],
    }


def is_wall(maze, w, h, x, y):
    if x < 0 or y < 0 or y >= h or x >= w:
        return True
    return maze[y][x] == "#"


def encode(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
