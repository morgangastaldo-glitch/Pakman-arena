"""
Costanti condivise, mappe e helper di protocollo per Pac-Man Arena 1vAll.
"""
import json
import random
import string
from collections import deque

DEFAULT_PORT = 8765

# 60Hz invece di 30Hz: raddoppia la frequenza con cui il server calcola
# fisica/collisioni e manda correzioni di stato ai client. Le velocita' sono
# espresse in celle/secondo quindi il bilanciamento del gioco NON cambia
# (a 60Hz ogni tick avanza semplicemente la meta' di spazio rispetto a
# prima); a beneficiarne sono la precisione delle collisioni tra giocatori
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
ROUND_SECONDS = 300  # durata di un round: 5 minuti
MAX_PLAYERS = 5
MIN_PLAYERS = 2

NORMAL_SPEED = 4.5          # celle al secondo
ASSASSIN_SPEED_MULT = 1.1   # il super assassino (bonus 300 punti) e' 1.1x rispetto a 1.0 dei giocatori normali

# ---- sistema punti e bonus a traguardi ----
# Ogni pallino normale vale 1 punto. In 10 punti (angoli/estremita') della
# mappa si trovano pallini piu' grossi e arancioni che valgono 10 punti.
# Ogni pallino mangiato ricompare da solo dopo PELLET_RESPAWN_SECONDS.
# Al raggiungimento di ogni soglia (una sola volta per round) scatta il
# bonus corrispondente.
BONUS_THRESHOLDS = [
    (50,  "extra_life"),    # +1 vita: se vieni eliminato, respawni invece di uscire
    (100, "extra_life"),    # +1 seconda vita extra (stesso effetto, soglia diversa)
    (150, "laser"),         # sblocca il laser (un colpo/secondo), ma dura solo LASER_DURATION_SECONDS
    (200, "mines"),         # sblocca 3 mine sganciabili sulla mappa (si attivano col tasto "1")
    (400, "missile"),       # sblocca 1 missile guidato (si spara col tasto "3")
]
PELLET_POINTS = 1                  # valore di un pallino normale
POWER_PELLET_POINTS = 10           # valore di un pallino grosso/arancione
POWER_PELLET_COUNT = 10            # quanti pallini grossi su ciascuna mappa
PELLET_RESPAWN_SECONDS = 20.0      # tempo prima che un pallino mangiato ricompaia
SUPER_ASSASSIN_THRESHOLD = 300     # punti oltre i quali si diventa "super assassino"
SUPER_ASSASSIN_DURATION_SECONDS = 30.0  # il super assassino dura solo 30 secondi, poi si disattiva
LASER_DURATION_SECONDS = 60.0      # bonus 150 punti: il laser resta attivo solo 1 minuto
GHOST_SECONDS = 10.0            # (bonus rimosso dal gioco, costante tenuta per compatibilita')
SPAWN_PROTECT_SECONDS = 3.0    # invulnerabilita' temporanea dopo un respawn
LASER_INTERVAL_SECONDS = 1.0   # ogni quanto il laser spara un colpo, una volta sbloccato (1 al secondo)
LASER_FIRST_DELAY_SECONDS = 1.0  # attesa del primo colpo dopo lo sblocco
LASER_PROJECTILE_SPEED = 20.0  # celle al secondo percorse dal proiettile laser (raddoppiata: e' un proiettile vero, deve sentirsi veloce)
LASER_BOUNCE_DISTANCE = 12     # celle percorribili dopo il primo rimbalzo su una parete (bonus 150 punti)
MINES_COUNT = 2                # numero di mine disponibili una volta sbloccato il bonus 200 punti (ridotto da 3 a 2)
MINE_DOUBLE_TAP_MS = 350       # finestra (ms) del doppio tocco freccia destra/D che sgancia una mina (uso lato client)
PORTAL_COOLDOWN_SECONDS = 1.2  # anti ping-pong: dopo un teletrasporto i portali si ignorano per un attimo

# ---- ciclo acceso/spento dei portali di teletrasporto ----
# I portali non sono piu' sempre attivi: si accendono per PORTAL_ON_SECONDS,
# poi si spengono per PORTAL_OFF_SECONDS, e cosi' via per tutto il round.
# Da spenti, entrarci non ha alcun effetto (vedi try_portal in main.py).
PORTAL_ON_SECONDS = 30.0
PORTAL_OFF_SECONDS = 30.0

# ---- bonus 400 punti: missile guidato (tasto "3") ----
MISSILE_SPEED_MULT = 1.1        # velocita' del missile = NORMAL_SPEED * 1.1 (di poco piu' veloce di un giocatore normale)
MISSILES_COUNT = 1              # missili disponibili una volta sbloccato il bonus 400 punti (solo 1)
MISSILE_RETARGET_SECONDS = 0.15  # ogni quanto il missile ricalcola il percorso verso il bersaglio (che si muove)

# ---- bonus 500 punti: trappola (tasto "4") ----
# Allo sblocco NON scatta nulla in automatico: premendo il tasto "4" il
# giocatore intrappola il nemico piu' vicino (bloccato sul posto) per
# TRAP_DURATION_SECONDS. Se ci si avvicina entro TRAP_RANGE celle e si
# preme di nuovo "4" in tempo, l'avversario viene distrutto da una piccola
# esplosione (perde una vita). Se scade il tempo, la trappola si disinnesca
# da sola e l'avversario torna libero.
TRAP_THRESHOLD = 500
TRAP_DURATION_SECONDS = 3.0    # la trappola immobilizza il bersaglio solo 3 secondi (ridotta da 15)
TRAP_RANGE = 1  # distanza massima (in celle, stile scacchi/Chebyshev) per far detonare la trappola
TRAP_MAX_USES = 3              # la trappola si puo' innescare al massimo 3 volte per giocatore, per round

# ---- bonus 600 punti: torretta automatica piazzabile (tasto "5") ----
# Allo sblocco NON scatta nulla in automatico: premendo il tasto "5" UNA
# SOLA VOLTA il giocatore piazza una torretta nella cella in cui si trova
# in quel momento. La torretta e' permanente (resta sulla mappa per tutto
# il resto del round, anche se il proprietario muore) e spara da sola verso
# il nemico vivo piu' vicino con la STESSA cadenza di fuoco del laser
# (un colpo ogni LASER_INTERVAL_SECONDS), riusando la stessa meccanica dei
# proiettili laser (stessa velocita', si ferma sul primo muro).
TURRET_THRESHOLD = 600
TURRET_FIRE_INTERVAL_SECONDS = LASER_INTERVAL_SECONDS  # stessa cadenza di fuoco del laser
# Raggio d'azione della torretta: traccia e spara SOLO ai nemici entro
# questa distanza (in caselle, distanza Manhattan). Fuori raggio la
# torretta resta in attesa e riprende a sparare appena qualcuno rientra.
TURRET_RANGE_CELLS = 10
# Percentuale di punti che chi uccide ruba alla vittima (50%): la vittima
# NON perde piu' tutto, conserva l'altra meta' delle sue risorse.
KILL_STEAL_FRACTION = 0.5

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


def bfs_path(maze, w, h, start, goal):
    """Percorso piu' breve (in celle, esclusa quella di partenza) da start a
    goal dentro il labirinto, via breadth-first search: e' cio' che rende il
    missile del bonus 400 punti "guidato" (segue i corridoi, non attraversa
    mai un muro) invece che un proiettile a linea retta come il laser.
    Ritorna None se il bersaglio non e' raggiungibile (non dovrebbe mai
    succedere: tutte le mappe sono garantite completamente connesse)."""
    if start == goal:
        return []
    frontier = deque([start])
    came_from = {start: None}
    while frontier:
        cur = frontier.popleft()
        if cur == goal:
            break
        cx, cy = cur
        for ddx, ddy in DIRECTIONS.values():
            nxt = (cx + ddx, cy + ddy)
            if nxt in came_from:
                continue
            if is_wall(maze, w, h, nxt[0], nxt[1]):
                continue
            came_from[nxt] = cur
            frontier.append(nxt)
    if goal not in came_from:
        return None
    path = []
    cur = goal
    while cur != start:
        path.append(cur)
        cur = came_from[cur]
    path.reverse()
    return path


def choose_power_pellet_cells(maze, w, h, count=POWER_PELLET_COUNT):
    """Sceglie 'count' celle libere ben distribuite tra loro (algoritmo
    "farthest point sampling"): si parte dalla cella libera piu' vicina
    all'angolo in alto a sinistra, poi ad ogni passo si aggiunge la cella
    libera piu' lontana (in distanza minima) da quelle gia' scelte. Il
    risultato tende naturalmente a "sparpagliarsi" verso gli estremi/angoli
    della mappa, esattamente come richiesto."""
    floor_cells = [(x, y) for y in range(h) for x in range(w) if maze[y][x] == "."]
    if not floor_cells:
        return []
    count = min(count, len(floor_cells))
    start = min(floor_cells, key=lambda c: c[0] + c[1])
    chosen = [start]
    remaining = set(floor_cells)
    remaining.discard(start)
    while len(chosen) < count and remaining:
        best_cell, best_dist = None, -1
        for c in remaining:
            d = min((c[0] - s[0]) ** 2 + (c[1] - s[1]) ** 2 for s in chosen)
            if d > best_dist:
                best_dist, best_cell = d, c
        chosen.append(best_cell)
        remaining.discard(best_cell)
    return chosen


def encode(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
