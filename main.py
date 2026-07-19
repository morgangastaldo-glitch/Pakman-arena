"""
Pac-Man Arena 1vAll - server WebSocket

Questo file contiene la STESSA IDENTICA logica di gioco di server.py
(lobby, countdown, movimento, collisioni, condizioni di vittoria, timer,
codici stanza): nessuna regola e' stata cambiata rispetto a server.py, a
parte la rimozione del killer (vedi sotto).
L'UNICA differenza rispetto a server.py e' il trasporto di rete:
- server.py parla socket TCP grezzi (righe di JSON) pensati per il
  client da terminale (client.py), incompatibili con un browser.
- questo file parla il protocollo WebSocket vero, quello che il browser
  usa con `new WebSocket(...)`, cosi' il client web (index.html) puo'
  collegarsi davvero.

In piu', questo server invia la mappa scelta a caso tra le 10 disponibili
(maze/maze_w/maze_h/maze_name/theme) alla creazione della stanza e di nuovo,
con una nuova mappa casuale, ad ogni inizio round: il client da terminale la
legge in locale da common.py, il browser invece deve riceverla via rete.

Non esiste piu' un "killer" scelto a rotazione tra i giocatori: dopo il
countdown iniziale il round parte direttamente, e i giocatori si eliminano
a vicenda solo tramite i bonus ottenuti raggiungendo soglie di punti
(laser, mine, super assassino a 300 punti).

Avvio locale:      python3 main.py [porta]   (default 8765)
In hosting (Render/Railway/...): la porta arriva dalla variabile
d'ambiente PORT, impostata automaticamente dalla piattaforma.
"""
import asyncio
import json
import os
import pathlib
import random
import socket
import sys
import uuid
from collections import deque

import websockets
from websockets.datastructures import Headers
from websockets.http11 import Response

from common import (
    TICK_DT, COUNTDOWN_SECONDS, ROUND_SECONDS,
    MAX_PLAYERS, MIN_PLAYERS, NORMAL_SPEED, ASSASSIN_SPEED_MULT,
    COLORS, CHARACTERS, DIRECTIONS, is_wall, ROOM_CODE_CHARS,
    pick_random_maze, choose_power_pellet_cells, bfs_path,
    BONUS_THRESHOLDS, GHOST_SECONDS,
    PELLET_POINTS, POWER_PELLET_POINTS, POWER_PELLET_COUNT,
    PELLET_RESPAWN_SECONDS, SUPER_ASSASSIN_THRESHOLD,
    SUPER_ASSASSIN_DURATION_SECONDS, LASER_RANGE_CELLS,
    SPAWN_PROTECT_SECONDS, LASER_INTERVAL_SECONDS, LASER_FIRST_DELAY_SECONDS,
    LASER_PROJECTILE_SPEED, LASER_BOUNCE_DISTANCE, MINES_COUNT,
    PORTAL_COOLDOWN_SECONDS, PORTAL_ON_SECONDS, PORTAL_OFF_SECONDS,
    MISSILE_SPEED_MULT, MISSILES_COUNT, MISSILE_RETARGET_SECONDS,
    TRAP_THRESHOLD, TRAP_DURATION_SECONDS, TRAP_RANGE, TRAP_MAX_USES,
    TURRET_THRESHOLD, TURRET_FIRE_INTERVAL_SECONDS,
    TURRET_RANGE_CELLS, KILL_STEAL_FRACTION,
    ARMOR_THRESHOLD, ARMOR_DURATION_SECONDS,
    LIGHTNING_THRESHOLD,
    PET_THRESHOLD, PET_RANGE_CELLS,
    PET_SPEED_MULT, PET_RETARGET_SECONDS, PET_STAY_RANGE,
    ROBOT_THRESHOLD, ROBOT_FIRE_INTERVAL_SECONDS, ROBOT_SPEED_MULT,
    ROBOT_WANDER_RETARGET_SECONDS, ROBOT_LEVELUP_DISPLAY_SECONDS,
    MORTAR_THRESHOLD, MORTAR_RANGE_CELLS, MORTAR_FIRE_INTERVAL_SECONDS,
    MORTAR_FLIGHT_SECONDS_PER_CELL, MORTAR_BLAST_RADIUS_CELLS,
    POISON_DURATION_SECONDS, POISON_TICK_SECONDS, POISON_RADIUS_CELLS,
    SUPERBOMB_THRESHOLD, SUPERBOMB_FUSE_SECONDS, SUPERBOMB_RADIUS_CELLS,
    BALLOON_THRESHOLD, BALLOON_SPEED, BALLOON_BOMB_INTERVAL_SECONDS,
    BALLOON_BOMB_RADIUS_CELLS, BALLOON_RETARGET_EPSILON,
    RTT_PING_INTERVAL_SECONDS, RTT_DEFAULT_SECONDS,
    REWIND_MAX_SECONDS, REWIND_HISTORY_SECONDS,
)
import math
import time

# Direzione opposta di ciascuna direzione: serve per l'inversione di marcia
# istantanea a meta' cella (stile Pac-Man originale, vedi update_movement).
OPPOSITE_DIR = {"up": "down", "down": "up", "left": "right", "right": "left"}

MAX_PLAYER_COLORS = 2  # colore primario + colore di dettaglio (opzionale)

DEFAULT_PORT = 8765

# Se index.html si trova nella stessa cartella di questo file, il server lo
# serve direttamente: cosi' un solo processo/hosting basta per tutto,
# niente Netlify separato. Pura comodita' di distribuzione, non tocca la
# logica di gioco: il client rimane identico, cambia solo da dove arriva.
CLIENT_HTML_PATH = pathlib.Path(__file__).parent / "index.html"
try:
    CLIENT_HTML = CLIENT_HTML_PATH.read_text(encoding="utf-8")
except FileNotFoundError:
    CLIENT_HTML = None

ROOMS = {}  # code -> Room


def encode_text(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


class Player:
    def __init__(self, pid, name, ws):
        self.id = pid
        self.name = name
        self.ws = ws
        # Fino a 2 colori: colors[0] = colore primario (corpo, univoco tra
        # i giocatori), colors[1] = colore di dettaglio opzionale (contorno,
        # denti, occhio a seconda del personaggio). Lista vuota = nessun
        # colore scelto ancora.
        self.colors = []
        self.character = "classic"
        self.host = False
        self.x = 0
        self.y = 0
        self.direction = None
        # Direzione "richiesta" dal giocatore ma non ancora applicabile (es.
        # muro nella cella successiva): viene tenuta in memoria e applicata
        # in automatico nel primo tick in cui diventa possibile, esattamente
        # come nel Pac-Man originale. Senza questa coda, premere una
        # direzione un istante troppo presto la faceva perdere del tutto,
        # dando la sensazione di comandi "poco precisi".
        self.next_direction = None
        self.move_accum = 0.0
        self.alive = True
        self.connected = True
        # ---- sistema punti / bonus (azzerato ad ogni round) ----
        self.points = 0
        self.lives = 3                 # si parte con 3 vite; a 50 punti diventa 4, ecc.: un'eliminazione non ti fa uscire finche' hai vite, fa respawnare
        self.claimed = set()           # soglie bonus gia' riscattate in questo round
        self.ghost_left = 0.0          # (bonus fantasma rimosso dal gioco: resta sempre a 0)
        # A SUPER_ASSASSIN_THRESHOLD punti (300): sblocca la modalita' ninja
        # (tasto "2"), attivabile a comando finche' il round e' in corso.
        # Una volta attivata: invisibile agli altri, piu' veloce (1.1x
        # rispetto a 1.0 dei giocatori normali) e uccide chiunque al solo
        # contatto. Dura solo SUPER_ASSASSIN_DURATION_SECONDS (45s) poi si
        # disattiva da sola (vedi il countdown in update_movement); si
        # disattiva anche prima se il giocatore viene ucciso (vedi
        # kill_player). E' UTILIZZABILE UNA SOLA VOLTA per round (vedi
        # ninja_used): a differenza di prima non si puo' piu' riattivare
        # dopo che e' scaduta o dopo un'eliminazione.
        self.has_ninja = False         # bonus sbloccato (una volta per round)
        self.is_assassin = False       # True mentre la modalita' ninja e' ATTIVA in questo istante
        self.assassin_left = 0.0       # secondi rimanenti da modalita' ninja attiva
        self.ninja_used = False        # True dopo l'unica attivazione consentita per round
        self.prot_left = 0.0           # invulnerabilita' temporanea dopo un respawn
        self.has_laser = False         # bonus 150 punti: laser frontale (arma principale). Resta sbloccato per TUTTA la partita una volta ottenuto (non scade piu'); spara solo quando un nemico e' entro LASER_RANGE_CELLS caselle (vedi update_lasers)
        self.laser_cd = 0.0            # countdown al prossimo colpo di laser
        self.has_bounce = False        # (non piu' assegnato da alcun bonus, resta sempre False)
        self.has_mines = False         # bonus 200 punti: puo' sganciare mine
        self.mines_left = 0            # mine ancora disponibili in questo round
        self.portal_cd = 0.0           # anti ping-pong dopo un teletrasporto
        # Ultima direzione di marcia nota: e' il "lato frontale" da cui parte
        # il laser anche se in questo istante si e' fermi contro un muro.
        self.facing = "right"
        # ---- compensazione latenza per le svolte (vedi Room._rewind_move) ----
        # Storico (timestamp, x, y, move_accum, direction) registrato ad ogni
        # tick: serve a "tornare indietro" fino al momento reale in cui un
        # tasto direzione e' stato premuto, cosi' la svolta puo' essere
        # applicata li' invece che al momento (in ritardo) in cui il
        # messaggio arriva sul server. Lunghezza limitata (REWIND_HISTORY_SECONDS)
        # per non accumulare memoria round dopo round.
        self.pos_history = deque()
        # Stima del round-trip-time di questo giocatore (aggiornata dai
        # pacchetti rtt_pong, vedi Room.update_rtt_pings): usata per sapere
        # di quanto "tornare indietro" quando arriva un comando di svolta.
        self.rtt = RTT_DEFAULT_SECONDS
        self.rtt_ping_cd = 0.0        # countdown al prossimo ping di misura RTT
        self.rtt_ping_sent_at = None  # timestamp dell'ultimo ping in attesa di risposta
        # ---- bonus 400 punti: missile guidato (tasto "2") ----
        self.has_missile = False
        self.missiles_left = 0
        # ---- bonus 500 punti: trappola (tasto "3") ----
        self.has_trap = False          # bonus sbloccato (una volta per round)
        self.trap_target = None        # id della vittima che QUESTO giocatore ha intrappolato
        self.trapped_left = 0.0        # se > 0, QUESTO giocatore e' intrappolato (immobile)
        self.trapped_by = None         # id di chi lo ha intrappolato (per pulizia alla scadenza/morte)
        self.trap_uses_left = 0        # quante volte puo' ancora INNESCARE la trappola (max TRAP_MAX_USES)
        # ---- bonus 600 punti: torretta automatica permanente (tasto "5") ----
        self.has_turret = False        # bonus sbloccato (una volta per round)
        self.turret_placed = False     # True dopo il piazzamento: il tasto "5" e' utilizzabile una sola volta
        # ---- bonus 700 punti: corazza laser (tasto "6") ----
        # Allo sblocco NON si attiva da sola: si attiva a comando col tasto
        # "6" (vedi try_activate_armor), UNA SOLA VOLTA per round, e dura
        # ARMOR_DURATION_SECONDS. Mentre e' attiva respinge ogni proiettile
        # che la colpisce, distrugge le torrette toccate e uccide chiunque
        # tocchi (a differenza del ninja resta pero' visibile a tutti).
        self.has_armor = False         # bonus sbloccato (una volta per round)
        self.armor_active = False      # True mentre la corazza e' ATTIVA in questo istante
        self.armor_left = 0.0          # secondi rimanenti di corazza attiva
        self.armor_used = False        # True dopo l'unica attivazione consentita per round
        # ---- bonus 800 punti: fulmine (tasto "7") ----
        # Allo sblocco NON scatta nulla in automatico: si attiva a comando
        # col tasto "7" (vedi try_activate_lightning), UNA SOLA VOLTA per
        # round. Colpisce all'istante tutti gli avversari vivi sulla mappa.
        self.has_lightning = False     # bonus sbloccato (una volta per round)
        self.lightning_used = False    # True dopo l'unica attivazione consentita per round
        # ---- bonus 900 punti: pet fedele permanente (tasto "8") ----
        # Allo sblocco NON scatta nulla in automatico: si evoca a comando col
        # tasto "8" (vedi try_summon_pet), UNA SOLA VOLTA per round, come la
        # torretta. Il pet vero e proprio vive in self.pets (lista della
        # Room), non qui: qui si tiene solo lo stato dello sblocco/utilizzo.
        self.has_pet = False           # bonus sbloccato (una volta per round)
        self.pet_summoned = False      # True dopo l'evocazione: il tasto "8" e' utilizzabile una sola volta
        # ---- bonus 1000 punti: evoluzione della torretta in robot (tasto "9") ----
        # Allo sblocco NON scatta nulla in automatico: si evolve a comando col
        # tasto "9" (vedi try_evolve_turret), UNA SOLA VOLTA per round, e solo
        # se la torretta (bonus 600 punti) e' ancora viva sulla mappa. Lo
        # stato vero e proprio dell'evoluzione (evolved/level_up_left/
        # wander_path/...) vive dentro il dict della torretta in
        # self.turrets, non qui: qui si tiene solo lo stato dello
        # sblocco/utilizzo, come per gli altri bonus a comando.
        self.has_robot = False         # bonus sbloccato (una volta per round)
        self.robot_used = False        # True dopo l'evoluzione: il tasto "9" e' utilizzabile una sola volta
        # ---- bonus 1200 punti: mortaio (tasto "0") ----
        # Allo sblocco NON scatta nulla in automatico: si schiera a comando
        # col tasto "0" (vedi try_place_mortar), UNA SOLA VOLTA per round,
        # come la torretta. Il mortaio vero e proprio vive in self.mortars
        # (lista della Room), non qui: qui si tiene solo lo stato dello
        # sblocco/utilizzo, come per gli altri bonus a comando.
        self.has_mortar = False        # bonus sbloccato (una volta per round)
        self.mortar_placed = False     # True dopo lo schieramento: il tasto "0" e' utilizzabile una sola volta
        # ---- bonus 1400 punti: bombolone ad area (tasto "0", DOPO il mortaio) ----
        # Allo sblocco NON scatta nulla in automatico: si piazza a comando
        # riusando il tasto "0" (vedi try_place_superbomb), UNA SOLA VOLTA
        # per round, ma solo DOPO che il mortaio (bonus 1200 punti) e' gia'
        # stato schierato. Il bombolone vero e proprio vive in
        # self.superbombs (lista della Room), non qui: qui si tiene solo lo
        # stato dello sblocco/utilizzo, come per gli altri bonus a comando.
        self.has_superbomb = False     # bonus sbloccato (una volta per round)
        self.superbomb_placed = False  # True dopo il piazzamento: il tasto "0" (dopo il mortaio) e' utilizzabile una sola volta

        # ---- bonus 1600 punti: mongolfiera vagante (tasto "0", DOPO il bombolone) ----
        self.has_balloon = False       # bonus sbloccato (una volta per round)
        self.balloon_launched = False  # True dopo il lancio: il tasto "0" (dopo mortaio+bombolone) e' utilizzabile una sola volta
        # Uccisioni fatte in questo round: ogni 2 kill si guadagna una vita extra.
        self.kills = 0

    def to_public(self):
        # Posizione "continua": la griglia interna (self.x/self.y, interi)
        # resta l'autorita' per collisioni/regole, ma al client mandiamo
        # anche l'avanzamento reale dentro la cella corrente (move_accum),
        # che il server gia' calcola ad ogni tick. Cosi' il client puo'
        # mostrare la posizione VERA in tempo reale invece di scoprire "e'
        # arrivato nella cella successiva" solo a cella completata e dover
        # inscenare un'animazione di recupero: e' questo che rendeva il
        # movimento degli altri giocatori percettibilmente in ritardo.
        dx, dy = DIRECTIONS.get(self.direction, (0, 0)) if self.direction else (0, 0)
        fx = self.x + dx * self.move_accum
        fy = self.y + dy * self.move_accum
        return {
            "id": self.id, "name": self.name, "colors": self.colors,
            "character": self.character,
            "host": self.host, "x": round(fx, 4), "y": round(fy, 4),
            "direction": self.direction,
            "alive": self.alive,
            # ---- nuovi campi per HUD/rendering ----
            "points": self.points, "lives": self.lives,
            "kills": self.kills,
            "ghost": self.ghost_left > 0,
            "assassin": self.is_assassin,
            "ninja": self.has_ninja,
            "ninja_used": self.ninja_used,
            "prot": self.prot_left > 0,
            "laser": self.has_laser,
            "bounce": self.has_bounce,
            "mines": self.has_mines,
            "mines_left": self.mines_left,
            "missile": self.has_missile,
            "missiles_left": self.missiles_left,
            "trap": self.has_trap,
            "trap_uses_left": self.trap_uses_left,
            "trapped": self.trapped_left > 0,
            "trapped_left": round(self.trapped_left, 1) if self.trapped_left > 0 else 0,
            "turret": self.has_turret,
            "turret_placed": self.turret_placed,
            "armor": self.has_armor,
            "armor_on": self.armor_active,
            "armor_used": self.armor_used,
            "lightning": self.has_lightning,
            "lightning_used": self.lightning_used,
            "pet": self.has_pet,
            "pet_summoned": self.pet_summoned,
            "robot": self.has_robot,
            "robot_used": self.robot_used,
            "mortar": self.has_mortar,
            "mortar_placed": self.mortar_placed,
            "superbomb": self.has_superbomb,
            "superbomb_placed": self.superbomb_placed,
            "balloon": self.has_balloon,
            "balloon_launched": self.balloon_launched,
        }


class Room:
    def __init__(self, code):
        self.code = code
        self.players: dict[str, Player] = {}
        self.state = "LOBBY"  # LOBBY, COUNTDOWN, PLAYING, ENDED
        self.countdown_left = 0.0
        self.timer_left = 0.0
        self.loop_task = None
        self.last_result = None
        # La vittoria e' "ultimo giocatore vivo": per decidere il titolo di
        # fine round teniamo traccia di com'e' avvenuta l'ultima eliminazione.
        self.last_kill = None
        # Eventi una-tantum (uccisioni, bonus, laser, teletrasporti, pallini
        # mangiati) accumulati durante il tick e trasmessi subito dopo: sono
        # cio' che permette al client effetti/suoni precisi senza dover
        # "indovinare" confrontando snapshot consecutivi.
        self.events = []
        # Proiettili laser in volo e mine posate sulla mappa: entrambi sono
        # liste di dict semplici (niente classi dedicate, sono pochi campi)
        # azzerate ad ogni nuovo round.
        self.lasers = []
        self.mines = []
        # Torrette automatiche piazzate (bonus 600 punti): permanenti, non
        # vengono mai svuotate durante il round, solo ad ogni nuovo round
        # (vedi assign_spawns/reset_to_lobby).
        self.turrets = []
        # Pet fedeli evocati (bonus 900 punti): permanenti come le torrette,
        # non vengono mai svuotati durante il round, solo ad ogni nuovo
        # round (vedi assign_spawns/reset_to_lobby).
        self.pets = []
        # Bomboloni piazzati (bonus 1400 punti): permanenti fino
        # all'esplosione (SUPERBOMB_FUSE_SECONDS dopo il piazzamento),
        # azzerati ad ogni nuovo round (vedi assign_spawns/reset_to_lobby).
        self.superbombs = []
        # Mortai schierati (bonus 1200 punti): permanenti come le torrette.
        # self.bombs sono le bombe attualmente "in volo" sparate dai mortai,
        # svuotate anch'esse solo ad ogni nuovo round.
        self.mortars = []
        self.bombs = []
        self.poison_zones = []  # nuvole velenose lasciate a terra dagli impatti del mortaio
        # Mongolfiere in volo (bonus 1600 punti): permanenti come mortai e
        # torrette, vagano a caso su tutta la mappa sganciando bombe a
        # intervalli regolari, azzerate ad ogni nuovo round.
        self.balloons = []
        # Mappa corrente della stanza: viene ripescata a caso tra le 10
        # disponibili a OGNI inizio round (vedi run_round), cosi' ogni
        # partita puo' capitare su una mappa diversa per forma/colore/misura.
        self.pick_new_map()

    def pick_new_map(self):
        map_data = pick_random_maze()
        self.maze = map_data["maze"]
        self.maze_w = map_data["w"]
        self.maze_h = map_data["h"]
        self.maze_name = map_data["name"]
        self.spawn_points = map_data["spawn_points"]
        self.theme = map_data["theme"]
        self.compute_portals()
        # 10 celle (una per angolo/estremita' della mappa) con un pallino
        # grosso arancione che vale 10 punti invece di 1.
        self.power_pellets = set(
            choose_power_pellet_cells(self.maze, self.maze_w, self.maze_h, POWER_PELLET_COUNT)
        )
        self.reset_pellets()
        self.reset_pellets()
        # Tutte le celle libere (non muro) della mappa: servono al robot
        # (torretta evoluta, bonus 1000 punti) per scegliere a caso una
        # meta' da raggiungere mentre pattuglia (vedi update_robot_wander).
        # Calcolate una sola volta per mappa, non ad ogni ricalcolo.
        self.free_cells = [
            (x, y)
            for y, row in enumerate(self.maze)
            for x, ch in enumerate(row)
            if ch != "#"
        ]

    def reset_pellets(self):
        """Ricrea l'insieme dei pallini: ogni cella libera della mappa ne
        contiene uno (1 punto, o 10 se e' una delle celle "power" scelte in
        pick_new_map). Il server e' l'autorita' (prima erano solo
        decorativi lato client): cosi' i punti sono uguali per tutti."""
        self.pellets = {
            (x, y)
            for y, row in enumerate(self.maze)
            for x, ch in enumerate(row)
            if ch == "."
        }
        # Cella -> secondi residui prima che un pallino mangiato ricompaia.
        self.pellet_respawns = {}

    def compute_portals(self):
        """Due portali su una coppia di angoli diagonalmente opposti della
        mappa: in alto a sinistra e in basso a destra. Tutte le mappe
        standard (39x19) hanno quei due angoli esatti gia' aperti per
        costruzione (vedi commento sopra MAZES in common.py); per sicurezza,
        se un angolo fosse un muro, si cerca con una BFS la cella libera
        raggiungibile piu' vicina all'angolo. Entrare in un portale
        teletrasporta all'altro (vedi try_portal)."""
        def nearest_open(tx, ty):
            if self.maze[ty][tx] == ".":
                return (tx, ty)
            seen = {(tx, ty)}
            frontier = deque([(tx, ty)])
            while frontier:
                x, y = frontier.popleft()
                for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < self.maze_w and 0 <= ny < self.maze_h and (nx, ny) not in seen:
                        seen.add((nx, ny))
                        if self.maze[ny][nx] == ".":
                            return (nx, ny)
                        frontier.append((nx, ny))
            return (tx, ty)

        top_left = nearest_open(1, 1)
        bottom_right = nearest_open(self.maze_w - 2, self.maze_h - 2)
        if top_left != bottom_right:
            self.portals = [top_left, bottom_right]
        else:
            self.portals = []
        # I portali partono accesi ad ogni nuova mappa/round, poi si
        # alternano acceso/spento ogni PORTAL_ON_SECONDS/PORTAL_OFF_SECONDS
        # (vedi update_portal_cycle, chiamato una volta per tick).
        self.portal_on = True
        self.portal_cycle_left = PORTAL_ON_SECONDS

    def map_payload(self):
        return {
            "maze": self.maze, "maze_w": self.maze_w, "maze_h": self.maze_h,
            "maze_name": self.maze_name, "theme": self.theme,
            "portals": [list(p) for p in self.portals],
            "power_pellets": [list(c) for c in self.power_pellets],
        }

    # ---------- lobby ----------

    def add_player(self, player):
        player.host = (len(self.players) == 0)
        self.players[player.id] = player

    def taken_primary_colors(self):
        # Solo il colore PRIMARIO (corpo) resta univoco tra i giocatori, per
        # non avere due pedine indistinguibili a colpo d'occhio: il colore
        # di dettaglio (contorno/denti/occhio) puo' invece essere condiviso
        # liberamente.
        return {p.colors[0] for p in self.players.values() if p.colors}

    async def broadcast(self, obj):
        dead = []
        text = encode_text(obj)
        for p in list(self.players.values()):
            if not p.connected:
                continue
            try:
                await p.ws.send(text)
            except websockets.exceptions.ConnectionClosed:
                dead.append(p.id)
        for pid in dead:
            p = self.players.get(pid)
            if p:
                p.connected = False

    async def broadcast_lobby(self):
        await self.broadcast({
            "type": "lobby_state",
            "code": self.code,
            "players": [
                {
                    "id": p.id, "name": p.name, "colors": p.colors,
                    "character": p.character, "host": p.host,
                }
                for p in self.players.values()
            ],
            "min_players": MIN_PLAYERS,
            "max_players": MAX_PLAYERS,
        })

    # ---------- round setup ----------

    def assign_spawns(self):
        spots = self.spawn_points[:]
        random.shuffle(spots)
        for p, (x, y) in zip(self.players.values(), spots):
            p.x, p.y = x, y
            p.direction = None
            p.next_direction = None
            p.move_accum = 0.0
            p.alive = True
            # Storico posizioni azzerato: dopo un teletrasporto di spawn
            # non deve restare traccia della posizione precedente, altrimenti
            # un riavvolgimento (vedi _rewind_move) potrebbe usarla per
            # errore e "resuscitare" una posizione ormai non valida.
            p.pos_history.clear()
            # reset del sistema punti/bonus per il nuovo round
            p.points = 0
            p.lives = 3             # si parte con 3 vite ad ogni nuovo round
            p.claimed = set()
            p.has_ninja = False
            p.is_assassin = False
            p.assassin_left = 0.0
            p.ninja_used = False
            p.ghost_left = 0.0
            p.prot_left = 0.0
            p.has_laser = False
            p.laser_cd = 0.0
            p.has_bounce = False
            p.has_mines = False
            p.mines_left = 0
            p.portal_cd = 0.0
            p.facing = "right"
            p.has_missile = False
            p.missiles_left = 0
            p.has_trap = False
            p.trap_target = None
            p.trapped_left = 0.0
            p.trapped_by = None
            p.trap_uses_left = 0
            p.has_turret = False
            p.turret_placed = False
            p.has_armor = False
            p.armor_active = False
            p.armor_left = 0.0
            p.armor_used = False
            p.has_lightning = False
            p.lightning_used = False
            p.has_pet = False
            p.pet_summoned = False
            p.has_robot = False
            p.robot_used = False
            p.has_mortar = False
            p.mortar_placed = False
            p.has_superbomb = False
            p.superbomb_placed = False
            p.has_balloon = False
            p.balloon_launched = False
            p.kills = 0
        self.lasers = []
        self.mines = []
        self.missiles = []
        self.turrets = []
        self.pets = []
        self.mortars = []
        self.superbombs = []
        self.balloons = []
        self.bombs = []
        self.poison_zones = []  # nuvole velenose lasciate a terra dagli impatti del mortaio

    def begin_playing(self):
        """Fine countdown iniziale: il round entra nel vivo. Non c'e' piu'
        alcuna scelta del killer: i giocatori partono tutti sullo stesso
        piano, e si eliminano a vicenda solo tramite i bonus (laser, mine,
        super assassino a 300 punti)."""
        self.state = "PLAYING"
        self.timer_left = ROUND_SECONDS

    # ---------- game tick ----------

    def push_event(self, ev):
        """Accoda un evento una-tantum da trasmettere a fine tick."""
        self.events.append({"type": "event", **ev})

    def update_movement(self):
        prev_positions = {p.id: (p.x, p.y) for p in self.players.values()}
        for p in self.players.values():
            if not p.alive:
                continue

            # Timer personali dei bonus/protezioni: scorrono qui, una volta
            # per tick, cosi' restano sincronizzati con la fisica.
            if p.ghost_left > 0:
                p.ghost_left = max(0.0, p.ghost_left - TICK_DT)
            if p.prot_left > 0:
                p.prot_left = max(0.0, p.prot_left - TICK_DT)
            if p.portal_cd > 0:
                p.portal_cd = max(0.0, p.portal_cd - TICK_DT)
            # Bonus 300 punti (super assassino): dura solo un tempo limitato
            # (30s), poi si disattiva da solo. Il laser (bonus 150) non ha
            # piu' un timer di scadenza: resta sbloccato per tutta la
            # partita una volta ottenuto, e la sua attivazione dipende solo
            # dalla vicinanza di un nemico (vedi update_lasers).
            if p.assassin_left > 0:
                p.assassin_left = max(0.0, p.assassin_left - TICK_DT)
                if p.assassin_left <= 0 and p.is_assassin:
                    p.is_assassin = False
                    self.push_event({"kind": "assassin_off", "player": p.id})
            # Bonus 700 punti (corazza laser): dura solo ARMOR_DURATION_SECONDS,
            # poi si disattiva da sola.
            if p.armor_left > 0:
                p.armor_left = max(0.0, p.armor_left - TICK_DT)
                if p.armor_left <= 0 and p.armor_active:
                    p.armor_active = False
                    self.push_event({"kind": "armor_off", "player": p.id})

            # Bonus 500 punti (trappola): chi e' intrappolato resta bloccato
            # sul punto esatto in cui si trovava, per TRAP_DURATION_SECONDS.
            # Scaduto il tempo senza detonazione, torna libero da solo.
            if p.trapped_left > 0:
                p.trapped_left = max(0.0, p.trapped_left - TICK_DT)
                if p.trapped_left <= 0:
                    trapper = self.players.get(p.trapped_by)
                    if trapper is not None and trapper.trap_target == p.id:
                        trapper.trap_target = None
                    p.trapped_by = None
                    self.push_event({"kind": "trap_expired", "player": p.id})
                # Immobile: niente movimento/pellet/portale in questo tick.
                continue

            # La svolta in coda si applica SUBITO, ad ogni tick, non solo
            # quando si e' esattamente su un incrocio: aspettare l'incrocio
            # sembrava piu' pulito, ma introduceva un problema peggiore,
            # cioe' client e server non attraversano MAI il confine della
            # cella nello stesso identico istante (millisecondo), quindi ad
            # ogni curva le due posizioni previste divergevano abbastanza
            # da far scattare la correzione forte lato client: e' quello il
            # teletrasporto. Girando subito, client e server seguono la
            # STESSA regola semplice ("se non e' muro, gira ora") e restano
            # sincronizzati sulla stessa logica deterministica.
            #
            # Resta pero' il problema originale che l'attesa dell'incrocio
            # doveva risolvere: se si cambia asse (es. da orizzontale a
            # verticale) a meta' cella, non si puo' riusare lo stesso
            # move_accum sul nuovo asse, altrimenti il personaggio "salta"
            # di una frazione di cella nella direzione sbagliata. Per
            # questo, ogni volta che la direzione cambia davvero, si azzera
            # l'avanzamento frazionario: il movimento nella nuova direzione
            # riparte pulito dal centro della cella corrente. E' comunque
            # uno scatto piccolo e deterministico (identico su client e
            # server), non il grosso teletrasporto dovuto al disallineamento
            # di rete.
            # ---- MOVIMENTO "VERO PAC-MAN" (automa a sotto-passi) ----
            # La logica vera e propria vive in _advance_state, condivisa con
            # Room._rewind_move: cosi' un tick normale e un "riavvolgimento"
            # per compensare la latenza (vedi sopra il messaggio "move")
            # usano ESATTAMENTE la stessa fisica, e non possono divergere.
            speed = NORMAL_SPEED
            if p.is_assassin:
                # Il super assassino (300 punti) e' piu' veloce dei
                # giocatori normali (stesso moltiplicatore 1.0 -> 1.1).
                speed *= ASSASSIN_SPEED_MULT
            (p.x, p.y, p.move_accum, p.direction, p.next_direction,
             facing) = self._advance_state(
                p.x, p.y, p.move_accum, p.direction, p.next_direction,
                TICK_DT, speed,
            )
            if facing is not None:
                p.facing = facing

            # Registra lo stato di fine tick nello storico: e' quello che
            # _rewind_move usera' per "tornare indietro" fino al momento
            # reale in cui un tasto direzione e' stato premuto (vedi sopra).
            now = time.monotonic()
            p.pos_history.append((now, p.x, p.y, p.move_accum, p.direction))
            cutoff = now - REWIND_HISTORY_SECONDS
            while p.pos_history and p.pos_history[0][0] < cutoff:
                p.pos_history.popleft()

            # Pallini e portali si valutano sulla cella in cui ci si trova
            # ORA (anche da fermi: copre lo spawn su un pallino).
            self.eat_pellet(p)
            self.try_portal(p)
        return prev_positions

    def _advance_state(self, x, y, accum, direction, next_direction, dt, speed):
        """Avanza UNA sola entita' (posizione+direzione) di dt secondi,
        applicando le stesse regole "vero Pac-Man" di sempre:
         1. Inversione di marcia (direzione opposta): applicata SUBITO,
            anche a meta' cella, senza scatti - la posizione continua
            viene conservata ribaltando l'avanzamento (accum -> 1-accum
            sulla cella successiva).
         2. Svolta perpendicolare in coda: applicata solo AL CENTRO della
            cella (accum == 0), come nel Pac-Man originale.
         3. Se la svolta e' bloccata da un muro, la coda RESTA in attesa e
            scatta da sola al primo incrocio utile.

        Pura funzione di stato (non tocca self.players ne' side-effect come
        pallini/eventi): usata sia dal tick normale (update_movement) sia
        dal riavvolgimento per compensare la latenza (_rewind_move), cosi'
        le due strade producono sempre lo stesso identico risultato per lo
        stesso input, e non possono disallinearsi tra loro.

        Ritorna (x, y, accum, direction, next_direction, facing) dove facing
        e' l'ultima direzione di marcia attraversata (None se l'entita' era
        gia' ferma e non si e' mai mossa in questo intervallo).
        """
        facing = None
        remaining = dt
        while remaining > 1e-9:
            if next_direction is not None:
                if (direction is not None
                        and next_direction == OPPOSITE_DIR[direction]
                        and accum > 1e-9):
                    dx, dy = DIRECTIONS[direction]
                    x += dx
                    y += dy
                    accum = 1.0 - accum
                    direction = next_direction
                    next_direction = None
                elif accum <= 1e-9:
                    ndx, ndy = DIRECTIONS[next_direction]
                    if not is_wall(self.maze, self.maze_w, self.maze_h,
                                   x + ndx, y + ndy):
                        direction = next_direction
                        next_direction = None
                    # se e' muro: la coda resta in memoria (regola 3)
            if direction is None:
                break
            facing = direction
            dx, dy = DIRECTIONS[direction]
            nx, ny = x + dx, y + dy
            if is_wall(self.maze, self.maze_w, self.maze_h, nx, ny):
                accum = 0.0
                break
            step = min(remaining, (1.0 - accum) / speed)
            accum += speed * step
            remaining -= step
            if accum >= 1.0 - 1e-6:
                accum = 0.0
                x, y = nx, ny
            else:
                break
        return x, y, accum, direction, next_direction, facing

    def _rewind_move(self, p, requested_dir):
        """Applica una richiesta di svolta compensando la latenza di rete.

        Invece di limitarsi ad accodare `requested_dir` nella posizione
        ATTUALE del giocatore (che sul server e' gia' "nel futuro" rispetto
        al momento reale in cui il tasto e' stato premuto, per via del
        tempo di viaggio del pacchetto), si cerca nello storico lo stato
        registrato a meta' del round-trip-time stimato fa, si applica li'
        la richiesta con le stesse regole di sempre (_advance_state), e si
        "riavvolge in avanti" fino ad ora. Il risultato e' la posizione
        fisicamente corretta: se al centro-cella di allora la svolta era
        valida, il giocatore la ottiene, senza dover prima sbattere contro
        un muro e senza alcuno scatto visibile (la correzione e' al massimo
        di REWIND_MAX_SECONDS di percorso, meno di mezza cella a velocita'
        normale).
        """
        now = time.monotonic()
        delay = min(p.rtt / 2.0, REWIND_MAX_SECONDS)
        if delay <= 1e-9 or not p.pos_history:
            # Nessuna stima di ritardo utile o storico vuoto (es. appena
            # spawnato): nessun rischio, ma nessun beneficio nemmeno.
            # Si torna al comportamento semplice, sempre corretto.
            p.next_direction = requested_dir
            return

        target_t = now - delay
        # Cerca l'ultimo campione dello storico CON timestamp <= target_t
        # (dal piu' recente al piu' vecchio: di solito e' tra gli ultimi).
        snapshot = None
        for entry in reversed(p.pos_history):
            if entry[0] <= target_t:
                snapshot = entry
                break
        if snapshot is None:
            # Storico troppo corto per coprire il ritardo stimato (partita
            # appena iniziata): fallback sicuro, nessun riavvolgimento.
            p.next_direction = requested_dir
            return

        snap_t, sx, sy, saccum, sdir = snapshot
        replay_dt = now - snap_t
        speed = NORMAL_SPEED * (ASSASSIN_SPEED_MULT if p.is_assassin else 1.0)
        nx, ny, naccum, ndir, nnext, facing = self._advance_state(
            sx, sy, saccum, sdir, requested_dir, replay_dt, speed,
        )

        # Rete di sicurezza: se nel frattempo e' successo qualcosa che
        # _advance_state non conosce (respawn, teletrasporto via portale,
        # trappola, eliminazione...) la posizione ricalcolata puo' finire
        # molto lontana da quella attuale. In quel caso si scarta il
        # riavvolgimento e si torna al comportamento semplice, invece di
        # rischiare di teletrasportare il giocatore per errore.
        if math.hypot(nx - p.x, ny - p.y) > 1.5:
            p.next_direction = requested_dir
            return

        p.x, p.y, p.move_accum, p.direction, p.next_direction = nx, ny, naccum, ndir, nnext
        if facing is not None:
            p.facing = facing

    def update_rtt_pings(self):
        """Manda un ping di misura RTT a ciascun giocatore ogni
        RTT_PING_INTERVAL_SECONDS, e aggiorna p.rtt quando arriva il pong
        (vedi il branch 'rtt_pong' nel loop messaggi). Chiamato una volta
        per tick da run_round, con costo trascurabile (un controllo
        temporale per giocatore, invio solo ogni ~2s)."""
        for p in self.players.values():
            if not p.connected:
                continue
            p.rtt_ping_cd -= TICK_DT
            if p.rtt_ping_cd > 0:
                continue
            p.rtt_ping_cd = RTT_PING_INTERVAL_SECONDS
            p.rtt_ping_sent_at = time.monotonic()
            asyncio.ensure_future(self._safe_send(p.ws, encode_text(
                {"type": "rtt_ping", "t": p.rtt_ping_sent_at}
            )))

    @staticmethod
    async def _safe_send(ws, payload):
        try:
            await ws.send(payload)
        except websockets.exceptions.ConnectionClosed:
            pass

    def eat_pellet(self, p):
        cell = (p.x, p.y)
        if cell not in self.pellets:
            return
        self.pellets.discard(cell)
        is_power = cell in self.power_pellets
        gained = POWER_PELLET_POINTS if is_power else PELLET_POINTS
        p.points += gained
        # Il pallino ricompare da solo dopo PELLET_RESPAWN_SECONDS (stesso
        # tipo, normale o "power", di quello appena mangiato).
        self.pellet_respawns[cell] = PELLET_RESPAWN_SECONDS
        self.push_event({
            "kind": "pellet", "cells": [[p.x, p.y]], "by": p.id,
            "power": is_power, "points": gained,
        })
        self.check_bonuses(p)

    def update_pellet_respawns(self):
        """Fa ricomparire i pallini mangiati dopo PELLET_RESPAWN_SECONDS."""
        if not self.pellet_respawns:
            return
        done = []
        for cell, left in self.pellet_respawns.items():
            left -= TICK_DT
            if left <= 0:
                done.append(cell)
            else:
                self.pellet_respawns[cell] = left
        for cell in done:
            del self.pellet_respawns[cell]
            self.pellets.add(cell)
            self.push_event({
                "kind": "pellet_respawn", "cells": [[cell[0], cell[1]]],
                "power": cell in self.power_pellets,
            })

    def check_bonuses(self, p):
        """Riscatta i traguardi appena superati (una volta sola per round).

        Il ninja (300 punti) e la trappola (500 punti) sono traguardi a
        parte rispetto a BONUS_THRESHOLDS (soglie fisse, non configurabili
        per-mappa). Allo sblocco NON si attivano da soli: segnano solo che
        il bonus e' disponibile (has_ninja / has_trap). L'attivazione vera
        e propria scatta solo quando il giocatore preme il tasto
        corrispondente (vedi try_activate_ninja e try_activate_trap), e
        resta disponibile per tutto il round (si puo' riattivare piu'
        volte, a differenza di laser/mine/missili che si consumano)."""
        for threshold, kind in BONUS_THRESHOLDS:
            if p.points < threshold or threshold in p.claimed:
                continue
            p.claimed.add(threshold)
            if kind == "extra_life":
                p.lives += 1
            elif kind == "extra_life_3":
                p.lives += 3
            elif kind == "laser":
                p.has_laser = True
                p.laser_cd = LASER_FIRST_DELAY_SECONDS
            elif kind == "mines":
                p.has_mines = True
                p.mines_left = MINES_COUNT
            elif kind == "missile":
                p.has_missile = True
                p.missiles_left = MISSILES_COUNT
            self.push_event({
                "kind": "bonus", "player": p.id,
                "bonus": kind, "points": threshold,
            })
        # Bonus 300 punti: sblocca la modalita' ninja (invisibilita' +
        # velocita' + uccisione al contatto), ma NON la attiva. Si attiva
        # a comando col tasto "2" (vedi try_activate_ninja).
        if (
            p.alive
            and p.points >= SUPER_ASSASSIN_THRESHOLD
            and SUPER_ASSASSIN_THRESHOLD not in p.claimed
        ):
            p.claimed.add(SUPER_ASSASSIN_THRESHOLD)
            p.has_ninja = True
            self.push_event({
                "kind": "bonus", "player": p.id,
                "bonus": "ninja", "points": SUPER_ASSASSIN_THRESHOLD,
            })
        # Bonus 500 punti: sblocca la trappola, ma NON intrappola subito
        # nessuno. Si attiva a comando col tasto "4" (vedi try_activate_trap).
        if (
            p.alive
            and p.points >= TRAP_THRESHOLD
            and TRAP_THRESHOLD not in p.claimed
        ):
            p.claimed.add(TRAP_THRESHOLD)
            p.has_trap = True
            p.trap_uses_left = TRAP_MAX_USES
            self.push_event({
                "kind": "bonus", "player": p.id,
                "bonus": "trap", "points": TRAP_THRESHOLD,
            })
        # Bonus 600 punti: sblocca la torretta automatica permanente, ma NON
        # la piazza subito. Si piazza a comando col tasto "5" (vedi
        # try_place_turret), una sola volta per giocatore.
        if (
            p.alive
            and p.points >= TURRET_THRESHOLD
            and TURRET_THRESHOLD not in p.claimed
        ):
            p.claimed.add(TURRET_THRESHOLD)
            p.has_turret = True
            self.push_event({
                "kind": "bonus", "player": p.id,
                "bonus": "turret", "points": TURRET_THRESHOLD,
            })
        # Bonus 700 punti: sblocca la corazza laser, ma NON la attiva. Si
        # attiva a comando col tasto "6" (vedi try_activate_armor), UNA SOLA
        # VOLTA per round (come il ninja).
        if (
            p.alive
            and p.points >= ARMOR_THRESHOLD
            and ARMOR_THRESHOLD not in p.claimed
        ):
            p.claimed.add(ARMOR_THRESHOLD)
            p.has_armor = True
            self.push_event({
                "kind": "bonus", "player": p.id,
                "bonus": "armor", "points": ARMOR_THRESHOLD,
            })
        # Bonus 800 punti: sblocca il fulmine, ma NON lo scatena subito. Si
        # attiva a comando col tasto "7" (vedi try_activate_lightning), UNA
        # SOLA VOLTA per round (come ninja e corazza).
        if (
            p.alive
            and p.points >= LIGHTNING_THRESHOLD
            and LIGHTNING_THRESHOLD not in p.claimed
        ):
            p.claimed.add(LIGHTNING_THRESHOLD)
            p.has_lightning = True
            self.push_event({
                "kind": "bonus", "player": p.id,
                "bonus": "lightning", "points": LIGHTNING_THRESHOLD,
            })
        # Bonus 900 punti: sblocca il pet fedele, ma NON lo evoca subito. Si
        # evoca a comando col tasto "8" (vedi try_summon_pet), una sola volta
        # per giocatore, per round.
        if (
            p.alive
            and p.points >= PET_THRESHOLD
            and PET_THRESHOLD not in p.claimed
        ):
            p.claimed.add(PET_THRESHOLD)
            p.has_pet = True
            self.push_event({
                "kind": "bonus", "player": p.id,
                "bonus": "pet", "points": PET_THRESHOLD,
            })
        # Bonus 1000 punti: sblocca l'evoluzione della torretta in robot
        # mobile, ma NON la evolve subito. Si evolve a comando col tasto "9"
        # (vedi try_evolve_turret), UNA SOLA VOLTA per round, e solo se la
        # torretta e' ancora viva sulla mappa in quel momento.
        if (
            p.alive
            and p.points >= ROBOT_THRESHOLD
            and ROBOT_THRESHOLD not in p.claimed
        ):
            p.claimed.add(ROBOT_THRESHOLD)
            p.has_robot = True
            self.push_event({
                "kind": "bonus", "player": p.id,
                "bonus": "robot", "points": ROBOT_THRESHOLD,
            })
        # Bonus 1200 punti: sblocca il mortaio, ma NON lo schiera subito. Si
        # schiera a comando col tasto "0" (vedi try_place_mortar), UNA SOLA
        # VOLTA per giocatore, per round.
        if (
            p.alive
            and p.points >= MORTAR_THRESHOLD
            and MORTAR_THRESHOLD not in p.claimed
        ):
            p.claimed.add(MORTAR_THRESHOLD)
            p.has_mortar = True
            self.push_event({
                "kind": "bonus", "player": p.id,
                "bonus": "mortar", "points": MORTAR_THRESHOLD,
            })
        # Bonus 1400 punti: sblocca il bombolone ad area, ma NON lo piazza
        # subito. Si piazza a comando RIUSANDO il tasto "0" (vedi
        # try_place_superbomb), UNA SOLA VOLTA per giocatore per round, ma
        # solo DOPO aver gia' schierato il mortaio (bonus 1200 punti): finche'
        # il mortaio non e' stato piazzato, il tasto "0" resta dedicato a
        # quello (vedi il dispatch del messaggio "place_mortar").
        if (
            p.alive
            and p.points >= SUPERBOMB_THRESHOLD
            and SUPERBOMB_THRESHOLD not in p.claimed
        ):
            p.claimed.add(SUPERBOMB_THRESHOLD)
            p.has_superbomb = True
            self.push_event({
                "kind": "bonus", "player": p.id,
                "bonus": "superbomb", "points": SUPERBOMB_THRESHOLD,
            })
        # Bonus 1600 punti: sblocca la mongolfiera vagante, ma NON la fa
        # librare subito. Si libra a comando RIUSANDO ancora il tasto "0"
        # (vedi try_launch_balloon), UNA SOLA VOLTA per giocatore per round,
        # ma solo DOPO aver gia' piazzato sia il mortaio (1200) sia il
        # bombolone (1400): finche' entrambi non sono stati piazzati, il
        # tasto "0" resta dedicato a quelli (vedi il dispatch del messaggio
        # "place_mortar").
        if (
            p.alive
            and p.points >= BALLOON_THRESHOLD
            and BALLOON_THRESHOLD not in p.claimed
        ):
            p.claimed.add(BALLOON_THRESHOLD)
            p.has_balloon = True
            self.push_event({
                "kind": "bonus", "player": p.id,
                "bonus": "balloon", "points": BALLOON_THRESHOLD,
            })

    def try_portal(self, p):
        """Se il giocatore e' su un portale (e non e' appena arrivato da un
        teletrasporto), lo sposta al portale opposto mantenendo la direzione.
        Funziona solo mentre i portali sono ACCESI (vedi update_portal_cycle):
        da spenti, stare sulla cella del portale non ha alcun effetto."""
        if not self.portals or p.portal_cd > 0 or not self.portal_on:
            return
        pos = (p.x, p.y)
        if pos == self.portals[0]:
            dest = self.portals[1]
        elif pos == self.portals[1]:
            dest = self.portals[0]
        else:
            return
        src = pos
        p.x, p.y = dest
        p.move_accum = 0.0
        p.portal_cd = PORTAL_COOLDOWN_SECONDS
        # Vedi assign_spawns: dopo un salto di posizione non dovuto al
        # normale movimento, lo storico va azzerato per non offrire a
        # _rewind_move dati di "prima del teletrasporto".
        p.pos_history.clear()
        self.push_event({
            "kind": "teleport", "player": p.id,
            "from": [src[0], src[1]], "to": [dest[0], dest[1]],
        })

    def update_portal_cycle(self):
        """Alterna i portali tra acceso (PORTAL_ON_SECONDS) e spento
        (PORTAL_OFF_SECONDS), avanti e indietro per tutto il round."""
        if not self.portals:
            return
        self.portal_cycle_left -= TICK_DT
        if self.portal_cycle_left <= 0:
            self.portal_on = not self.portal_on
            self.portal_cycle_left = PORTAL_ON_SECONDS if self.portal_on else PORTAL_OFF_SECONDS
            self.push_event({"kind": "portal_toggle", "on": self.portal_on})

    def respawn_player(self, p):
        """Rimette in gioco chi aveva una vita extra: se un super assassino
        e' attivo, nello spawn piu' lontano da lui, con qualche secondo di
        protezione; altrimenti in uno spawn casuale."""
        assassins = [q for q in self.players.values() if q.alive and q.is_assassin]
        spots = self.spawn_points[:]
        if assassins:
            def min_dist(s):
                return min(abs(s[0] - a.x) + abs(s[1] - a.y) for a in assassins)
            spots.sort(key=lambda s: -min_dist(s))
            x, y = spots[0]
        else:
            x, y = random.choice(spots)
        p.x, p.y = x, y
        p.direction = None
        p.next_direction = None
        p.move_accum = 0.0
        p.prot_left = SPAWN_PROTECT_SECONDS
        p.portal_cd = 0.5  # se lo spawn fosse vicino a un portale, niente teletrasporto istantaneo
        p.pos_history.clear()  # vedi assign_spawns

    def kill_player(self, victim, cause, shooter_id=None):
        """Unica via per togliere una vita: usata dal tocco del super
        assassino, dal laser e dalle mine. Con vite extra si respawna,
        altrimenti si e' fuori.

        Chi uccide ruba il 20% dei punti della vittima (KILL_STEAL_FRACTION,
        arrotondato per difetto): la vittima CONSERVA l'altra meta' delle
        sue risorse - un'eliminazione fa male ma non azzera piu' tutto."""
        self.last_kill = {"cause": cause, "by": shooter_id}
        killer_player = self.players.get(shooter_id) if shooter_id else None
        stolen = 0
        if killer_player is not None and killer_player.id != victim.id:
            stolen = int(victim.points * KILL_STEAL_FRACTION)
            if stolen > 0:
                victim.points -= stolen
                killer_player.points += stolen
                self.check_bonuses(killer_player)
            # Ogni 2 uccisioni fatte, il killer guadagna una vita extra
            # (indipendente dalle soglie punti: conta solo il numero di kill).
            killer_player.kills += 1
            if killer_player.kills % 2 == 0:
                killer_player.lives += 1
                self.push_event({
                    "kind": "kill_life_bonus", "player": killer_player.id,
                    "kills": killer_player.kills, "lives": killer_player.lives,
                })
        # Pulizia stato trappola: sia se la vittima era intrappolata, sia se
        # la vittima stessa aveva qualcuno intrappolato (il bersaglio torna
        # libero, dato che chi lo teneva e' stato eliminato).
        if victim.trapped_by:
            trapper = self.players.get(victim.trapped_by)
            if trapper is not None and trapper.trap_target == victim.id:
                trapper.trap_target = None
            victim.trapped_by = None
        victim.trapped_left = 0.0
        if victim.trap_target:
            freed = self.players.get(victim.trap_target)
            if freed is not None and freed.trapped_by == victim.id:
                freed.trapped_left = 0.0
                freed.trapped_by = None
                self.push_event({"kind": "trap_expired", "player": freed.id})
            victim.trap_target = None
        victim.lives -= 1
        died_at = [victim.x, victim.y]
        if victim.lives > 0:
            self.respawn_player(victim)
            respawned = True
        else:
            victim.alive = False
            victim.direction = None
            victim.next_direction = None
            victim.move_accum = 0.0
            respawned = False
        if victim.is_assassin:
            victim.is_assassin = False
            victim.assassin_left = 0.0
            self.push_event({"kind": "assassin_off", "player": victim.id})
        self.push_event({
            "kind": "kill", "victim": victim.id, "cause": cause,
            "by": shooter_id, "at": died_at,
            "respawn": respawned, "lives": max(victim.lives, 0),
            "stolen": stolen,
        })

    def check_collisions(self, prev_positions):
        """A SUPER_ASSASSIN_THRESHOLD punti un giocatore diventa "super
        assassino" per SUPER_ASSASSIN_DURATION_SECONDS (vedi check_bonuses):
        invisibile agli altri, piu' veloce, e uccide chiunque tocchi. Allo
        stesso modo, a ARMOR_THRESHOLD punti la corazza laser (bonus 700,
        visibile a tutti) uccide chiunque tocchi mentre e' attiva. Due
        giocatori "letali" (ninja e/o corazza, in qualsiasi combinazione)
        non si uccidono a vicenda toccandosi."""
        lethal = [p for p in self.players.values() if p.alive and (p.is_assassin or p.armor_active)]
        if not lethal:
            return
        killed_ids = set()
        for L in lethal:
            if L.id in killed_ids or not L.alive:
                continue
            for p in list(self.players.values()):
                if p.id == L.id or not p.alive or p.id in killed_ids:
                    continue
                if p.is_assassin or p.armor_active:
                    continue  # due giocatori "letali" non si eliminano a vicenda
                # Il tocco non puo' nulla contro la protezione post-respawn
                # (il bonus fantasma e' stato rimosso dal gioco, ghost_left
                # resta sempre 0). Laser e mine ignorano comunque entrambe.
                if p.ghost_left > 0 or p.prot_left > 0:
                    continue
                same_cell = (p.x == L.x and p.y == L.y)
                swapped = (
                    prev_positions.get(p.id) == (L.x, L.y)
                    and prev_positions.get(L.id) == (p.x, p.y)
                )
                if same_cell or swapped:
                    cause = "assassin" if L.is_assassin else "armor"
                    self.kill_player(p, cause, shooter_id=L.id)
                    killed_ids.add(p.id)

    def update_lasers(self):
        """Bonus 150 punti: arma principale, sbloccata UNA VOLTA e attiva
        per TUTTA la partita da quel momento (non scade piu'). Ogni
        LASER_INTERVAL_SECONDS (1 secondo) parte un singolo colpo
        (proiettile) dal lato frontale del personaggio, con la stessa
        identica meccanica di sempre (spawn_laser) - ma SOLO quando almeno
        un avversario vivo si trova entro LASER_RANGE_CELLS caselle
        (distanza Manhattan), esattamente come il raggio d'azione della
        torretta (vedi update_turrets). Se nessuno e' abbastanza vicino resta
        carico (cd fermo a zero) e spara ISTANTANEAMENTE appena qualcuno
        entra nel raggio, invece di sprecare colpi a vuoto o di accumulare
        colpi arretrati. Il super assassino (300 punti, invisibile agli
        altri) NON spara: il proiettile e' visibile a tutti e rivelerebbe
        subito la sua posizione, vanificando l'invisibilita'."""
        for p in list(self.players.values()):
            if not p.alive or not p.has_laser or p.is_assassin:
                continue
            nearest = self.nearest_alive(p.x, p.y, {p.id})
            in_range = (
                nearest is not None
                and abs(nearest.x - p.x) + abs(nearest.y - p.y) <= LASER_RANGE_CELLS
            )
            p.laser_cd -= TICK_DT
            if p.laser_cd > 0:
                continue
            if not in_range:
                p.laser_cd = 0.0
                continue
            p.laser_cd = LASER_INTERVAL_SECONDS
            self.spawn_laser(p)

    def spawn_laser(self, shooter):
        """Crea un nuovo proiettile laser (singolo colpo) che parte dalla
        cella dello sparatore e viaggia nella sua direzione frontale. Il
        proiettile vero e proprio avanza poi, un tick alla volta, dentro
        move_lasers()."""
        dx, dy = DIRECTIONS.get(shooter.facing, (1, 0))
        laser = {
            "id": uuid.uuid4().hex[:8],
            "owner": shooter.id,
            "x": shooter.x, "y": shooter.y,   # cella intera corrente
            "dx": dx, "dy": dy,
            "move_accum": 0.0,
            "bounce_left": None,  # None finche' non ha ancora rimbalzato
        }
        self.lasers.append(laser)
        self.push_event({
            "kind": "laser_fire", "id": laser["id"], "shooter": shooter.id,
            "x": shooter.x, "y": shooter.y, "dir": shooter.facing,
        })

    def move_lasers(self):
        """Avanza tutti i proiettili laser attivi di un tick. Un proiettile
        elimina QUALSIASI giocatore colpito (protezioni incluse, come il
        vecchio raggio istantaneo) e si estingue sul primo muro incontrato,
        a meno che lo sparatore abbia sbloccato il rimbalzo (bonus 150
        punti): in quel caso rimbalza in una direzione libera scelta a caso
        e prosegue per altre LASER_BOUNCE_DISTANCE celle prima di sparire."""
        if not self.lasers:
            return
        survivors = []
        for lz in self.lasers:
            lz["move_accum"] += LASER_PROJECTILE_SPEED * TICK_DT
            destroyed = False
            while lz["move_accum"] >= 1.0 and not destroyed:
                nx, ny = lz["x"] + lz["dx"], lz["y"] + lz["dy"]
                if is_wall(self.maze, self.maze_w, self.maze_h, nx, ny):
                    shooter = self.players.get(lz["owner"])
                    can_bounce = (
                        shooter is not None and shooter.has_bounce
                        and (lz["bounce_left"] is None or lz["bounce_left"] > 0)
                    )
                    if not can_bounce:
                        destroyed = True
                        break
                    # Sceglie una direzione libera a caso (diversa da quella
                    # che ha appena portato al muro, se possibile).
                    options = []
                    for ddx, ddy in DIRECTIONS.values():
                        if (ddx, ddy) == (-lz["dx"], -lz["dy"]):
                            continue  # evita di tornare indietro sui propri passi
                        tx, ty = lz["x"] + ddx, lz["y"] + ddy
                        if not is_wall(self.maze, self.maze_w, self.maze_h, tx, ty):
                            options.append((ddx, ddy))
                    if not options:
                        # Vicolo cieco: nessuna via libera nemmeno tornando
                        # indietro, il proiettile si estingue qui.
                        destroyed = True
                        break
                    lz["dx"], lz["dy"] = random.choice(options)
                    first_bounce = lz["bounce_left"] is None
                    if first_bounce:
                        lz["bounce_left"] = LASER_BOUNCE_DISTANCE
                        self.push_event({
                            "kind": "laser_bounce", "id": lz["id"],
                            "x": lz["x"], "y": lz["y"],
                        })
                    continue  # riprova subito nella nuova direzione, stesso tick
                # Cella libera: avanza di una cella.
                lz["move_accum"] -= 1.0
                lz["x"], lz["y"] = nx, ny
                if lz["bounce_left"] is not None:
                    lz["bounce_left"] -= 1
                victims = [
                    q for q in self.players.values()
                    if q.alive and q.id != lz["owner"] and q.x == nx and q.y == ny
                ]
                if victims:
                    armored = [v for v in victims if v.armor_active]
                    if armored:
                        # Bonus 700 punti: la corazza laser RESPINGE il
                        # colpo invece di subirlo. Il proiettile inverte
                        # direzione e riparte come "sparato" dal portatore
                        # della corazza, quindi puo' colpire chiunque trovi
                        # sulla via del ritorno, compreso lo sparatore
                        # originale. Il portatore della corazza non subisce
                        # alcun danno.
                        lz["dx"], lz["dy"] = -lz["dx"], -lz["dy"]
                        lz["owner"] = armored[0].id
                        self.push_event({
                            "kind": "laser_reflect", "id": lz["id"],
                            "x": nx, "y": ny, "by": armored[0].id,
                        })
                        continue  # riprova subito nella direzione invertita, stesso tick
                    for v in victims:
                        self.kill_player(v, "laser", lz["owner"])
                    destroyed = True
                    break
                # Bonus 900 punti: un colpo laser NEMICO (di un altro
                # giocatore o di un'altra torretta/pet) distrugge il pet che
                # trova sulla sua strada, cosi' come un giocatore.
                pet_victims = [
                    pet for pet in self.pets
                    if pet["owner"] != lz["owner"] and pet["x"] == nx and pet["y"] == ny
                ]
                if pet_victims:
                    for pet in pet_victims:
                        self.destroy_pet(pet, "laser", lz["owner"])
                    destroyed = True
                    break
                if lz["bounce_left"] is not None and lz["bounce_left"] <= 0:
                    destroyed = True
                    break
            if destroyed:
                self.push_event({"kind": "laser_end", "id": lz["id"], "x": lz["x"], "y": lz["y"]})
            else:
                survivors.append(lz)
        self.lasers = survivors

    def try_place_mine(self, player):
        """Bonus 200 punti: sgancia una mina nella cella corrente del
        giocatore (finche' ne ha ancora disponibili). Chiamato dalla
        pressione del tasto "1" lato client.

        Se il giocatore e' intrappolato dalla trappola di un avversario
        (bonus 500 punti), NON puo' usare alcun bonus finche' non torna
        libero di muoversi (vedi player.trapped_left)."""
        if not player.alive or player.trapped_left > 0 or not player.has_mines or player.mines_left <= 0:
            return
        if any(m["x"] == player.x and m["y"] == player.y for m in self.mines):
            return  # niente due mine sulla stessa cella
        player.mines_left -= 1
        mine = {"id": uuid.uuid4().hex[:8], "owner": player.id, "x": player.x, "y": player.y}
        self.mines.append(mine)
        self.push_event({
            "kind": "mine_place", "id": mine["id"], "player": player.id,
            "x": player.x, "y": player.y, "left": player.mines_left,
        })

    def check_mines(self):
        """Fa esplodere le mine calpestate: elimina chiunque le tocchi
        (proprietario escluso), ignorando protezioni, come il laser. Se sulla
        stessa cella c'e' il pet (bonus 900 punti) di un altro giocatore, la
        mina distrugge anche lui.

        Eccezione: la modalita' ninja (300 punti) rende immuni alle mine,
        esattamente come rende invisibili e letali al tocco - camminarci
        sopra da ninja non la fa esplodere (la mina resta innescata, pronta
        per chi non e' ninja).

        Eccezione 2: chi ha la corazza laser ATTIVA (bonus 700 punti) e'
        immune al contatto con una mina AVVERSARIA - non la fa esplodere e
        non subisce danno, la mina resta sul posto pronta per essere
        disinnescata subito dopo da check_armor_effects (stesso tick)."""
        if not self.mines:
            return
        remaining = []
        for m in self.mines:
            victims = [
                q for q in self.players.values()
                if q.alive and not q.is_assassin and not q.armor_active and q.id != m["owner"]
                and q.x == m["x"] and q.y == m["y"]
            ]
            pet_victims = [
                pet for pet in self.pets
                if pet["owner"] != m["owner"] and pet["x"] == m["x"] and pet["y"] == m["y"]
            ]
            if victims or pet_victims:
                for v in victims:
                    self.kill_player(v, "mine", m["owner"])
                for pet in pet_victims:
                    self.destroy_pet(pet, "mine", m["owner"])
                self.push_event({"kind": "mine_boom", "id": m["id"], "x": m["x"], "y": m["y"]})
            else:
                remaining.append(m)
        self.mines = remaining

    def destroy_pet(self, pet, cause, by=None):
        """Unica via per rimuovere un pet (bonus 900 punti) dalla mappa: lo
        toglie da self.pets e notifica i client, esattamente come
        kill_player fa per i giocatori. Il proprietario NON puo' rievocarlo:
        pet_summoned resta True per tutto il resto del round."""
        if pet in self.pets:
            self.pets.remove(pet)
        self.push_event({
            "kind": "pet_destroyed", "id": pet["id"], "owner": pet["owner"],
            "x": pet["x"], "y": pet["y"], "cause": cause, "by": by,
        })

    def pet_public(self, pt):
        """Come Player.to_public: la griglia interna (pt['x']/pt['y'],
        interi) resta l'autorita' per collisioni, ma al client mandiamo
        anche l'avanzamento reale dentro la cella corrente (move_accum,
        verso la prossima cella del percorso), cosi' il pet si muove in
        modo fluido come un giocatore invece di scattare da una cella
        intera alla successiva solo quando il movimento e' completato."""
        dx = dy = 0
        path = pt.get("path")
        if path:
            nx, ny = path[0]
            dx, dy = nx - pt["x"], ny - pt["y"]
        accum = pt.get("move_accum", 0.0)
        fx = pt["x"] + dx * accum
        fy = pt["y"] + dy * accum
        dir_name = next((k for k, v in DIRECTIONS.items() if v == (dx, dy)), None)
        return {
            "id": pt["id"], "x": round(fx, 4), "y": round(fy, 4),
            "owner": pt["owner"], "aim": pt.get("aim"), "dir": dir_name,
        }

    def missile_public(self, mz):
        """Come pet_public: la griglia interna (mz['x']/mz['y'], interi)
        resta l'autorita' per collisioni, ma al client mandiamo anche
        l'avanzamento reale dentro la cella corrente (move_accum, verso la
        prossima cella del percorso) piu' la direzione corrente, cosi' il
        missile si disegna in modo fluido come un giocatore/pet invece di
        scattare da una cella intera alla successiva ad ogni tick."""
        dx = dy = 0
        path = mz.get("path")
        if path:
            nx, ny = path[0]
            dx, dy = nx - mz["x"], ny - mz["y"]
        accum = mz.get("move_accum", 0.0)
        fx = mz["x"] + dx * accum
        fy = mz["y"] + dy * accum
        dir_name = next((k for k, v in DIRECTIONS.items() if v == (dx, dy)), None)
        return {
            "id": mz["id"], "x": round(fx, 4), "y": round(fy, 4),
            "owner": mz["owner"], "target": mz["target"], "dir": dir_name,
        }

    def nearest_alive(self, x, y, exclude_ids):
        """Giocatore vivo piu' vicino (distanza Manhattan) a (x, y), tra
        quelli il cui id non e' in exclude_ids. None se nessuno qualifica."""
        candidates = [
            q for q in self.players.values()
            if q.alive and q.id not in exclude_ids
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda q: abs(q.x - x) + abs(q.y - y))

    def nearest_alive_non_ninja(self, x, y, exclude_ids):
        """Come nearest_alive, ma esclude anche chi e' attualmente in
        modalita' ninja (invisibile): usata dal missile guidato, che non
        puo' agganciare un bersaglio che non vede."""
        candidates = [
            q for q in self.players.values()
            if q.alive and not q.is_assassin and q.id not in exclude_ids
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda q: abs(q.x - x) + abs(q.y - y))

    # ---- bonus 300 punti: modalita' ninja (tasto "2") ----

    def try_activate_ninja(self, player):
        """Tasto '2': se il bonus e' sbloccato (300 punti) e non e' ancora
        stato usato in questo round, attiva la modalita' ninja per
        SUPER_ASSASSIN_DURATION_SECONDS (45s: invisibile agli altri, 1.1x
        piu' veloce, uccide chiunque tocchi). UTILIZZABILE UNA SOLA VOLTA
        per round: una volta terminata (scaduto il tempo o dopo
        un'eliminazione) non si puo' piu' riattivare, a differenza di
        prima.

        Se il giocatore e' intrappolato dalla trappola di un avversario,
        NON puo' usare alcun bonus finche' non torna libero di muoversi."""
        if not player.alive or player.trapped_left > 0 or not player.has_ninja or player.is_assassin or player.ninja_used:
            return
        player.ninja_used = True
        player.is_assassin = True
        player.assassin_left = SUPER_ASSASSIN_DURATION_SECONDS
        self.push_event({
            "kind": "assassin_on", "player": player.id,
            "bonus": "ninja", "points": SUPER_ASSASSIN_THRESHOLD,
        })

    # ---- bonus 700 punti: corazza laser (tasto "6") ----

    def try_activate_armor(self, player):
        """Tasto '6': se il bonus e' sbloccato (700 punti) e non e' ancora
        stato usato in questo round, attiva la corazza laser per
        ARMOR_DURATION_SECONDS: respinge ogni proiettile che la colpisce
        (vedi move_lasers/move_missiles), distrugge le torrette toccate
        (vedi check_armor_effects) e uccide chiunque tocchi (vedi
        check_collisions). Resta visibile a tutti (niente invisibilita').
        UTILIZZABILE UNA SOLA VOLTA per round.

        Se il giocatore e' intrappolato dalla trappola di un avversario,
        NON puo' usare alcun bonus finche' non torna libero di muoversi."""
        if not player.alive or player.trapped_left > 0 or not player.has_armor or player.armor_active or player.armor_used:
            return
        player.armor_used = True
        player.armor_active = True
        player.armor_left = ARMOR_DURATION_SECONDS
        self.push_event({
            "kind": "armor_on", "player": player.id,
            "bonus": "armor", "points": ARMOR_THRESHOLD,
        })

    # ---- bonus 800 punti: fulmine (tasto "7") ----

    def try_activate_lightning(self, player):
        """Tasto '7': se il bonus e' sbloccato (800 punti) e non e' ancora
        stato usato in questo round, scatena un fulmine che colpisce
        ISTANTANEAMENTE tutti gli avversari vivi presenti sulla mappa,
        ovunque si trovino (nessun raggio d'azione, a differenza della
        torretta): ciascuno perde una vita tramite kill_player (la stessa
        unica via usata da laser/mine/missili/trappola), con lo stesso
        furto del 50% dei punti e lo stesso conteggio kill/vita-extra-ogni-
        2-uccisioni del killer. UTILIZZABILE UNA SOLA VOLTA per round.

        Se il giocatore e' intrappolato dalla trappola di un avversario,
        NON puo' usare alcun bonus finche' non torna libero di muoversi."""
        if not player.alive or player.trapped_left > 0 or not player.has_lightning or player.lightning_used:
            return
        player.lightning_used = True
        targets = [
            q for q in self.players.values()
            if q.alive and q.id != player.id
        ]
        self.push_event({
            "kind": "lightning_on", "player": player.id,
            "bonus": "lightning", "points": LIGHTNING_THRESHOLD,
            "targets": [t.id for t in targets],
        })
        # Si copia la lista prima di iterare: kill_player puo' modificare
        # lo stato (respawn/eliminazione) dei bersagli, ma non la lista dei
        # giocatori della stanza, quindi qui non serve altro accorgimento;
        # si usa comunque una lista "congelata" per chiarezza.
        for victim in targets:
            self.kill_player(victim, "lightning", player.id)
        # Bonus 900 punti: il fulmine distrugge anche i pet di tutti gli
        # avversari colpiti (il proprio pet, se ne hai uno, resta illeso).
        for pet in [pt for pt in list(self.pets) if pt["owner"] != player.id]:
            self.destroy_pet(pet, "lightning", player.id)

    # ---- bonus 400 punti: missile guidato (tasto "3") ----

    def try_fire_missile(self, player):
        """Spara un missile (finche' ne restano) verso il nemico piu' vicino
        in questo istante. Il missile e' 'guidato': segue i corridoi via
        pathfinding (vedi move_missiles), non attraversa mai i muri, e si
        aggancia di nuovo al bersaglio piu' vicino se quello originale muore
        prima dell'impatto.

        Se il giocatore e' intrappolato dalla trappola di un avversario,
        NON puo' usare alcun bonus finche' non torna libero di muoversi."""
        if not player.alive or player.trapped_left > 0 or not player.has_missile or player.missiles_left <= 0:
            return
        # Un ninja e' invisibile: il missile non puo' agganciarlo nemmeno al
        # lancio (vedi anche move_missiles per il riaggancio in volo).
        target = self.nearest_alive_non_ninja(player.x, player.y, {player.id})
        if target is None:
            return
        player.missiles_left -= 1
        missile = {
            "id": uuid.uuid4().hex[:8],
            "owner": player.id,
            "x": player.x, "y": player.y,
            "move_accum": 0.0,
            "target": target.id,
            "path": [],
            "retarget_cd": 0.0,
        }
        self.missiles.append(missile)
        self.push_event({
            "kind": "missile_fire", "id": missile["id"], "owner": player.id,
            "x": player.x, "y": player.y, "left": player.missiles_left,
        })

    def move_missiles(self):
        """Avanza tutti i missili in volo di un tick: ognuno ricalcola il
        percorso verso il bersaglio ad intervalli regolari (il bersaglio si
        muove) e procede una cella alla volta lungo quel percorso, quindi non
        si schianta mai contro un muro. Al contatto col bersaglio (o con
        chiunque altro capiti sulla sua strada, escluso lo sparatore),
        quella vittima perde una vita."""
        if not self.missiles:
            return
        survivors = []
        for mz in self.missiles:
            destroyed = False
            target = self.players.get(mz["target"])
            # La modalita' ninja (300 punti) rende invisibili: un missile
            # guidato che aveva agganciato un giocatore diventato ninja
            # DEVE perdere il bersaglio (non puo' colpire un nemico che non
            # vede piu') e riagganciarsi subito al nemico vivo, non-ninja,
            # piu' vicino, esattamente come quando il bersaglio originale
            # muore prima dell'impatto.
            if target is None or not target.alive or target.is_assassin:
                target = self.nearest_alive_non_ninja(mz["x"], mz["y"], {mz["owner"]})
                if target is None:
                    destroyed = True
                else:
                    mz["target"] = target.id
                    mz["path"] = []
                    mz["retarget_cd"] = 0.0

            if not destroyed:
                mz["retarget_cd"] -= TICK_DT
                if mz["retarget_cd"] <= 0 or not mz["path"]:
                    mz["retarget_cd"] = MISSILE_RETARGET_SECONDS
                    path = bfs_path(
                        self.maze, self.maze_w, self.maze_h,
                        (mz["x"], mz["y"]), (target.x, target.y),
                    )
                    mz["path"] = path or []

                speed = NORMAL_SPEED * MISSILE_SPEED_MULT
                mz["move_accum"] += speed * TICK_DT
                while mz["move_accum"] >= 1.0 and mz["path"] and not destroyed:
                    mz["move_accum"] -= 1.0
                    nx, ny = mz["path"].pop(0)
                    mz["x"], mz["y"] = nx, ny
                    # Un ninja e' immune: il missile lo attraversa senza
                    # detonare, invece di colpirlo (coerente col riaggancio
                    # automatico al bersaglio piu' vicino non-ninja sopra).
                    victims = [
                        q for q in self.players.values()
                        if q.alive and not q.is_assassin
                        and q.id != mz["owner"] and q.x == nx and q.y == ny
                    ]
                    if victims:
                        armored = [v for v in victims if v.armor_active]
                        other_victims = [v for v in victims if not v.armor_active]
                        if armored:
                            # Bonus 700 punti: la corazza laser respinge
                            # anche il missile guidato (che non puo' essere
                            # rimandato indietro come il laser, dato che e'
                            # a ricerca automatica: viene semplicemente
                            # distrutto all'impatto, senza fare danno).
                            self.push_event({
                                "kind": "missile_reflect", "id": mz["id"],
                                "x": nx, "y": ny, "by": armored[0].id,
                            })
                        for v in other_victims:
                            self.kill_player(v, "missile", mz["owner"])
                        destroyed = True
                    # Bonus 900 punti: il missile guidato distrugge anche il
                    # pet nemico che trova sulla sua strada.
                    pet_victims = [
                        pet for pet in self.pets
                        if pet["owner"] != mz["owner"] and pet["x"] == nx and pet["y"] == ny
                    ]
                    if pet_victims:
                        for pet in pet_victims:
                            self.destroy_pet(pet, "missile", mz["owner"])
                        destroyed = True

            if destroyed:
                self.push_event({"kind": "missile_end", "id": mz["id"], "x": mz["x"], "y": mz["y"]})
            else:
                survivors.append(mz)
        self.missiles = survivors

    # ---- bonus 500 punti: trappola (tasto "4") ----

    def try_activate_trap(self, player):
        """Tasto '4': un solo tasto per tutto il meccanismo della trappola.

        - Se questo giocatore non ha ancora nessuno intrappolato (o la sua
          vittima precedente e' scappata/scaduta), intrappola SUBITO il
          nemico piu' vicino: resta bloccato sul posto per
          TRAP_DURATION_SECONDS.
        - Se invece ha gia' una vittima intrappolata ed e' abbastanza
          vicino (TRAP_RANGE celle), la fa detonare con una piccola
          esplosione (perde una vita).
        L'INNESCO (intrappolare un nuovo bersaglio) e' limitato a
        TRAP_MAX_USES volte per giocatore, per round: la detonazione di una
        trappola gia' innescata non consuma un uso extra.

        Se il giocatore e' intrappolato dalla trappola di un avversario,
        NON puo' usare alcun bonus (nemmeno far detonare una sua trappola
        gia' innescata) finche' non torna libero di muoversi."""
        if not player.alive or player.trapped_left > 0 or not player.has_trap:
            return

        if player.trap_target:
            victim = self.players.get(player.trap_target)
            if victim is not None and victim.alive and victim.trapped_left > 0:
                dist = max(abs(victim.x - player.x), abs(victim.y - player.y))
                if dist <= TRAP_RANGE:
                    self.push_event({"kind": "trap_boom", "x": victim.x, "y": victim.y})
                    self.kill_player(victim, "trap", player.id)
                    player.trap_target = None
                    return
                return
            player.trap_target = None

        # Nessuna vittima attualmente intrappolata: per innescarne una nuova
        # serve almeno un uso residuo (ne restano al massimo TRAP_MAX_USES
        # per round).
        if player.trap_uses_left <= 0:
            return

        target = self.nearest_alive(player.x, player.y, {player.id})
        if target is None:
            return
        player.trap_uses_left -= 1
        target.trapped_left = TRAP_DURATION_SECONDS
        target.trapped_by = player.id
        player.trap_target = target.id
        self.push_event({
            "kind": "trap_start", "player": player.id, "victim": target.id,
            "seconds": TRAP_DURATION_SECONDS, "uses_left": player.trap_uses_left,
        })

    # ---- bonus 600 punti: torretta automatica permanente (tasto "5") ----

    def try_place_turret(self, player):
        """Tasto '5': piazza UNA SOLA VOLTA (per tutto il round) una
        torretta nella cella corrente del giocatore. Da quel momento la
        torretta e' permanente (resta sulla mappa fino a fine round, anche
        se il proprietario muore o si disconnette) e spara da sola verso il
        nemico vivo piu' vicino, con la stessa cadenza del laser (vedi
        update_turrets).

        Se il giocatore e' intrappolato dalla trappola di un avversario,
        NON puo' usare alcun bonus finche' non torna libero di muoversi."""
        if not player.alive or player.trapped_left > 0 or not player.has_turret or player.turret_placed:
            return
        player.turret_placed = True
        turret = {
            "id": uuid.uuid4().hex[:8],
            "owner": player.id,
            "x": player.x, "y": player.y,
            "cd": LASER_FIRST_DELAY_SECONDS,
        }
        self.turrets.append(turret)
        self.push_event({
            "kind": "turret_place", "id": turret["id"], "player": player.id,
            "x": player.x, "y": player.y,
        })

    def try_evolve_turret(self, player):
        """Tasto '9': se il giocatore ha sbloccato il bonus 1000 punti, non
        lo ha ancora usato in questo round, e la sua torretta (bonus 600
        punti) e' ANCORA VIVA sulla mappa (non distrutta dalla corazza di un
        avversario), la fa evolvere in un robot mobile su 3 gambe. Da quel
        momento il robot smette di restare fermo: pattuglia la mappa a caso
        cercando nemici (vedi update_robot_wander), con cadenza di fuoco
        raddoppiata (vedi update_turrets) e velocita' di camminata pari a
        NORMAL_SPEED * ROBOT_SPEED_MULT. Utilizzabile una sola volta per
        round, come la torretta stessa.

        Se il giocatore e' intrappolato dalla trappola di un avversario,
        NON puo' usare alcun bonus finche' non torna libero di muoversi."""
        if not player.alive or player.trapped_left > 0 or not player.has_robot or player.robot_used:
            return
        turret = next((t for t in self.turrets if t["owner"] == player.id), None)
        if turret is None:
            # La torretta non e' mai stata piazzata, oppure e' gia' stata
            # distrutta dalla corazza di un avversario: niente da evolvere.
            return
        player.robot_used = True
        turret["evolved"] = True
        turret["level_up_left"] = ROBOT_LEVELUP_DISPLAY_SECONDS
        turret["wander_path"] = []
        turret["wander_cd"] = 0.0
        turret["move_accum"] = 0.0
        self.push_event({
            "kind": "turret_evolve", "id": turret["id"], "player": player.id,
            "x": turret["x"], "y": turret["y"],
        })

    def update_robot_wander(self, t):
        """Bonus 1000 punti: la navicella (torretta evoluta) non pattuglia
        piu' a caso, insegue ATTIVAMENTE il nemico vivo piu' vicino, esatta-
        mente come il missile guidato (vedi move_missiles): ogni
        ROBOT_WANDER_RETARGET_SECONDS ricalcola il percorso verso la
        posizione corrente del bersaglio (che si muove) via bfs_path, quindi
        non attraversa mai i muri, alla velocita' dimezzata
        NORMAL_SPEED * ROBOT_SPEED_MULT. Se al momento non c'e' nessun
        nemico vivo, resta ferma sull'ultimo percorso residuo invece di
        vagare senza meta."""
        target = self.nearest_alive(t["x"], t["y"], {t["owner"]})
        t["wander_cd"] = t.get("wander_cd", 0.0) - TICK_DT
        if target is not None and (t["wander_cd"] <= 0 or not t.get("wander_path")):
            t["wander_cd"] = ROBOT_WANDER_RETARGET_SECONDS
            path = bfs_path(self.maze, self.maze_w, self.maze_h, (t["x"], t["y"]), (target.x, target.y))
            t["wander_path"] = path or []
        speed = NORMAL_SPEED * ROBOT_SPEED_MULT
        t["move_accum"] = t.get("move_accum", 0.0) + speed * TICK_DT
        while t["move_accum"] >= 1.0 and t["wander_path"]:
            t["move_accum"] -= 1.0
            nx, ny = t["wander_path"].pop(0)
            t["x"], t["y"] = nx, ny

    def turret_public(self, t):
        """Come pet_public: se il robot (torretta evoluta) si sta muovendo
        lungo il proprio percorso di pattugliamento, manda anche
        l'avanzamento reale dentro la cella corrente (move_accum), cosi' il
        client lo disegna scivolare in modo fluido invece di scattare da una
        cella intera alla successiva. Una torretta non ancora evoluta resta
        ferma (dx=dy=0), esattamente come prima."""
        dx = dy = 0
        if t.get("evolved") and t.get("wander_path"):
            nx, ny = t["wander_path"][0]
            dx, dy = nx - t["x"], ny - t["y"]
        accum = t.get("move_accum", 0.0)
        fx = t["x"] + dx * accum
        fy = t["y"] + dy * accum
        return {
            "id": t["id"], "x": round(fx, 4), "y": round(fy, 4),
            "owner": t["owner"], "aim": t.get("aim"),
            "evolved": t.get("evolved", False),
            "level_up": t.get("level_up_left", 0) > 0,
        }

    def update_turrets(self):
        """Ogni torretta piazzata spara automaticamente verso il nemico
        vivo piu' vicino ogni TURRET_FIRE_INTERVAL_SECONDS (stessa cadenza
        del laser): riusa esattamente la stessa meccanica dei proiettili
        laser (self.lasers / move_lasers), scegliendo la direzione cardinale
        piu' vicina al bersaglio dato che la torretta non si muove.

        Se la torretta si e' evoluta in robot (bonus 1000 punti, tasto "9"):
        pattuglia la mappa a caso (vedi update_robot_wander) invece di
        restare ferma, e spara con cadenza raddoppiata
        (ROBOT_FIRE_INTERVAL_SECONDS invece di TURRET_FIRE_INTERVAL_SECONDS)."""
        if not self.turrets:
            return
        for t in self.turrets:
            if t.get("level_up_left", 0) > 0:
                t["level_up_left"] = max(0.0, t["level_up_left"] - TICK_DT)
            evolved = t.get("evolved", False)
            if evolved:
                self.update_robot_wander(t)
            # Tracciamento continuo: ad OGNI tick la torretta individua il
            # nemico vivo piu' vicino e, se e' entro TURRET_RANGE_CELLS (10
            # caselle, distanza Manhattan), gli punta contro la canna. La
            # mira (t["aim"]) finisce nello snapshot cosi' il client la
            # disegna che ruota verso il bersaglio in tempo reale.
            target = self.nearest_alive(t["x"], t["y"], {t["owner"]})
            in_range = (
                target is not None
                and abs(target.x - t["x"]) + abs(target.y - t["y"]) <= TURRET_RANGE_CELLS
            )
            t["aim"] = [target.x, target.y] if in_range else None
            t["cd"] -= TICK_DT
            if t["cd"] > 0:
                continue
            if not in_range:
                # Nessuno nel raggio: la torretta resta carica (cd fermo a
                # zero) e spara ISTANTANEAMENTE appena qualcuno entra nelle
                # 10 caselle, invece di sprecare colpi a vuoto.
                t["cd"] = 0.0
                continue
            t["cd"] = ROBOT_FIRE_INTERVAL_SECONDS if evolved else TURRET_FIRE_INTERVAL_SECONDS
            ddx, ddy = target.x - t["x"], target.y - t["y"]
            # Scelta della direzione di fuoco: prima l'asse con lo scarto
            # maggiore, ma se quella canna e' subito contro un muro si prova
            # l'altro asse (il colpo esce nel corridoio libero invece di
            # morire sul muro adiacente).
            cand = []
            horiz = (1, 0) if ddx >= 0 else (-1, 0)
            vert = (0, 1) if ddy >= 0 else (0, -1)
            cand = [horiz, vert] if abs(ddx) >= abs(ddy) else [vert, horiz]
            dx, dy = cand[0]
            if is_wall(self.maze, self.maze_w, self.maze_h, t["x"] + dx, t["y"] + dy) \
                    and not is_wall(self.maze, self.maze_w, self.maze_h,
                                    t["x"] + cand[1][0], t["y"] + cand[1][1]):
                dx, dy = cand[1]
            dir_name = next((k for k, v in DIRECTIONS.items() if v == (dx, dy)), "right")
            laser = {
                "id": uuid.uuid4().hex[:8],
                "owner": t["owner"],
                "x": t["x"], "y": t["y"],
                "dx": dx, "dy": dy,
                "move_accum": 0.0,
                "bounce_left": None,
            }
            self.lasers.append(laser)
            self.push_event({
                "kind": "laser_fire", "id": laser["id"], "shooter": t["owner"],
                "x": t["x"], "y": t["y"], "dir": dir_name, "turret": True,
            })

    # ---- bonus 1200 punti: mortaio (tasto "0") ----

    def try_place_mortar(self, player):
        """Tasto '0': schiera UNA SOLA VOLTA (per tutto il round) un
        mortaio nella cella corrente del giocatore. Da quel momento il
        mortaio e' permanente (resta sulla mappa fino a fine round, anche
        se il proprietario muore o si disconnette) e spara da solo bombe
        ad arco contro il nemico vivo piu' vicino entro MORTAR_RANGE_CELLS
        caselle (vedi update_mortars).

        Se il giocatore e' intrappolato dalla trappola di un avversario,
        NON puo' usare alcun bonus finche' non torna libero di muoversi."""
        if not player.alive or player.trapped_left > 0 or not player.has_mortar or player.mortar_placed:
            return
        player.mortar_placed = True
        mortar = {
            "id": uuid.uuid4().hex[:8],
            "owner": player.id,
            "x": player.x, "y": player.y,
            "cd": LASER_FIRST_DELAY_SECONDS,
        }
        self.mortars.append(mortar)
        self.push_event({
            "kind": "mortar_place", "id": mortar["id"], "player": player.id,
            "x": player.x, "y": player.y,
        })

    # ---- bonus 1400 punti: bombolone ad area (tasto "0", DOPO il mortaio) ----

    def try_place_superbomb(self, player):
        """Tasto '0', RIUSATO: viene chiamato dal dispatch del messaggio
        "place_mortar" solo quando player.mortar_placed e' gia' True (finche'
        il mortaio non e' stato piazzato, quella stessa pressione richiama
        invece try_place_mortar). Piazza UNA SOLA VOLTA (per tutto il round)
        un bombolone nella cella corrente del giocatore: un ordigno rotondo,
        grande quanto una casella, dello stesso colore del proprietario e
        visibile a TUTTI. Resta a terra per SUPERBOMB_FUSE_SECONDS, poi
        esplode (vedi update_superbombs/explode_superbomb) con un'onda
        concentrica che distrugge/neutralizza tutto cio' che si trova entro
        SUPERBOMB_RADIUS_CELLS caselle.

        Se il giocatore e' intrappolato dalla trappola di un avversario,
        NON puo' usare alcun bonus finche' non torna libero di muoversi."""
        if not player.alive or player.trapped_left > 0 or not player.has_superbomb or player.superbomb_placed:
            return
        player.superbomb_placed = True
        bomb = {
            "id": uuid.uuid4().hex[:8],
            "owner": player.id,
            "x": player.x, "y": player.y,
            "t": 0.0,
        }
        self.superbombs.append(bomb)
        self.push_event({
            "kind": "superbomb_place", "id": bomb["id"], "player": player.id,
            "x": player.x, "y": player.y,
        })

    def superbomb_public(self, bomb):
        return {
            "id": bomb["id"], "x": bomb["x"], "y": bomb["y"],
            "owner": bomb["owner"],
            "fuse_left": round(max(SUPERBOMB_FUSE_SECONDS - bomb["t"], 0), 2),
        }

    def update_superbombs(self):
        """Avanza il conto alla rovescia di ogni bombolone piazzato: dopo
        SUPERBOMB_FUSE_SECONDS esplode (vedi explode_superbomb) e viene
        rimosso dalla mappa."""
        if not self.superbombs:
            return
        remaining = []
        for bomb in self.superbombs:
            bomb["t"] += TICK_DT
            if bomb["t"] >= SUPERBOMB_FUSE_SECONDS:
                self.explode_superbomb(bomb)
            else:
                remaining.append(bomb)
        self.superbombs = remaining

    def explode_superbomb(self, bomb):
        """Esplosione del bombolone (bonus 1400 punti): onda concentrica di
        SUPERBOMB_RADIUS_CELLS caselle (distanza Manhattan) che distrugge o
        neutralizza tutto cio' che trova nel raggio, tranne le cose del
        proprietario stesso:
          - fa perdere una vita a ogni avversario vivo nel raggio (stessa
            immunita' ghost/protezione post-respawn di mortaio/mine/laser);
          - disinnesca ogni mina avversaria nel raggio;
          - distrugge ogni torretta/robot avversario nel raggio;
          - distrugge ogni mortaio avversario nel raggio;
          - distrugge ogni pet avversario nel raggio (vedi destroy_pet)."""
        ox, oy = bomb["x"], bomb["y"]
        owner = bomb["owner"]
        self.push_event({
            "kind": "superbomb_explode", "id": bomb["id"],
            "x": ox, "y": oy, "by": owner, "radius": SUPERBOMB_RADIUS_CELLS,
        })

        victims = [
            p for p in self.players.values()
            if p.alive and p.id != owner
            and p.ghost_left <= 0 and p.prot_left <= 0
            and abs(p.x - ox) + abs(p.y - oy) <= SUPERBOMB_RADIUS_CELLS
        ]
        for victim in victims:
            self.kill_player(victim, "superbomb", shooter_id=owner)

        if self.mines:
            remaining_mines = []
            for m in self.mines:
                if m["owner"] != owner and abs(m["x"] - ox) + abs(m["y"] - oy) <= SUPERBOMB_RADIUS_CELLS:
                    self.push_event({
                        "kind": "mine_destroyed", "id": m["id"],
                        "x": m["x"], "y": m["y"], "by": owner, "cause": "superbomb",
                    })
                else:
                    remaining_mines.append(m)
            self.mines = remaining_mines

        if self.turrets:
            remaining_turrets = []
            for t in self.turrets:
                if t["owner"] != owner and abs(t["x"] - ox) + abs(t["y"] - oy) <= SUPERBOMB_RADIUS_CELLS:
                    self.push_event({
                        "kind": "turret_destroyed", "id": t["id"],
                        "x": t["x"], "y": t["y"], "by": owner, "cause": "superbomb",
                        "evolved": t.get("evolved", False),
                    })
                else:
                    remaining_turrets.append(t)
            self.turrets = remaining_turrets

        if self.mortars:
            remaining_mortars = []
            for mt in self.mortars:
                if mt["owner"] != owner and abs(mt["x"] - ox) + abs(mt["y"] - oy) <= SUPERBOMB_RADIUS_CELLS:
                    self.push_event({
                        "kind": "mortar_destroyed", "id": mt["id"],
                        "x": mt["x"], "y": mt["y"], "by": owner, "cause": "superbomb",
                    })
                else:
                    remaining_mortars.append(mt)
            self.mortars = remaining_mortars

        for pet in list(self.pets):
            if pet["owner"] != owner and abs(pet["x"] - ox) + abs(pet["y"] - oy) <= SUPERBOMB_RADIUS_CELLS:
                self.destroy_pet(pet, "superbomb", owner)

    # ---- bonus 1600 punti: mongolfiera vagante (tasto "0", DOPO il bombolone) ----

    def try_launch_balloon(self, player):
        """Tasto '0', RIUSATO una terza volta: viene chiamato dal dispatch
        del messaggio "place_mortar" solo quando sia player.mortar_placed
        sia player.superbomb_placed sono gia' True (finche' non lo sono
        entrambi, quella stessa pressione richiama invece
        try_place_mortar/try_place_superbomb). Fa librare in aria, UNA SOLA
        VOLTA per round, una mongolfiera che nasce sulla cella corrente del
        giocatore: da quel momento vaga a caso su TUTTA la mappa (vedi
        update_balloons), volando sopra ogni muro senza alcun bersaglio, e
        sgancia una bomba ogni BALLOON_BOMB_INTERVAL_SECONDS nella propria
        posizione corrente, che esplode ISTANTANEAMENTE (vedi
        explode_balloon_bomb) con un raggio di BALLOON_BOMB_RADIUS_CELLS
        caselle. E' permanente: resta in volo per tutto il resto del round,
        anche se il proprietario muore o si disconnette (come il mortaio).

        Se il giocatore e' intrappolato dalla trappola di un avversario,
        NON puo' usare alcun bonus finche' non torna libero di muoversi."""
        if not player.alive or player.trapped_left > 0 or not player.has_balloon or player.balloon_launched:
            return
        player.balloon_launched = True
        balloon = {
            "id": uuid.uuid4().hex[:8],
            "owner": player.id,
            "x": float(player.x), "y": float(player.y),
            "tx": float(player.x), "ty": float(player.y),
            "bomb_cd": BALLOON_BOMB_INTERVAL_SECONDS,
        }
        self.balloons.append(balloon)
        self.push_event({
            "kind": "balloon_launch", "id": balloon["id"], "player": player.id,
            "x": player.x, "y": player.y,
        })

    def balloon_public(self, b):
        return {
            "id": b["id"], "x": round(b["x"], 3), "y": round(b["y"], 3),
            "owner": b["owner"],
        }

    def update_balloons(self):
        """Ogni mongolfiera in volo non ha alcun bersaglio: vaga a caso su
        tutta la mappa, scegliendo una nuova meta' casuale (in linea d'aria,
        MAI attraverso bfs_path/corridoi) ogni volta che raggiunge quella
        corrente, esattamente come vola sopra i muri una bomba di mortaio in
        volo. Ogni BALLOON_BOMB_INTERVAL_SECONDS sgancia una bomba nella
        propria posizione attuale (vedi explode_balloon_bomb)."""
        if not self.balloons:
            return
        for b in self.balloons:
            dx, dy = b["tx"] - b["x"], b["ty"] - b["y"]
            dist = math.hypot(dx, dy)
            if dist <= BALLOON_RETARGET_EPSILON:
                b["tx"] = random.uniform(0, self.maze_w - 1)
                b["ty"] = random.uniform(0, self.maze_h - 1)
            else:
                step = BALLOON_SPEED * TICK_DT
                if step >= dist:
                    b["x"], b["y"] = b["tx"], b["ty"]
                else:
                    b["x"] += dx / dist * step
                    b["y"] += dy / dist * step
            b["bomb_cd"] -= TICK_DT
            if b["bomb_cd"] <= 0:
                b["bomb_cd"] = BALLOON_BOMB_INTERVAL_SECONDS
                self.explode_balloon_bomb(b)

    def explode_balloon_bomb(self, b):
        """Bomba sganciata dalla mongolfiera (bonus 1600 punti): a
        differenza del bombolone NON ha alcuna miccia, esplode
        ISTANTANEAMENTE nel punto di sgancio con un raggio di
        BALLOON_BOMB_RADIUS_CELLS caselle (distanza Manhattan), colpendo
        dall'alto (come l'impatto del mortaio) e neutralizzando tutto cio'
        che trova nel raggio tranne le cose del proprietario stesso:
          - fa perdere una vita a ogni avversario vivo nel raggio (stessa
            immunita' ghost/protezione post-respawn degli altri ordigni);
          - disinnesca ogni mina avversaria nel raggio;
          - distrugge ogni torretta/robot avversario nel raggio;
          - distrugge ogni mortaio avversario nel raggio;
          - distrugge ogni pet avversario nel raggio (vedi destroy_pet)."""
        ox, oy = b["x"], b["y"]
        owner = b["owner"]
        self.push_event({
            "kind": "balloon_bomb_drop", "id": b["id"],
            "x": ox, "y": oy, "by": owner, "radius": BALLOON_BOMB_RADIUS_CELLS,
        })

        victims = [
            p for p in self.players.values()
            if p.alive and p.id != owner
            and p.ghost_left <= 0 and p.prot_left <= 0
            and abs(p.x - ox) + abs(p.y - oy) <= BALLOON_BOMB_RADIUS_CELLS
        ]
        for victim in victims:
            self.kill_player(victim, "balloon", shooter_id=owner)

        if self.mines:
            remaining_mines = []
            for m in self.mines:
                if m["owner"] != owner and abs(m["x"] - ox) + abs(m["y"] - oy) <= BALLOON_BOMB_RADIUS_CELLS:
                    self.push_event({
                        "kind": "mine_destroyed", "id": m["id"],
                        "x": m["x"], "y": m["y"], "by": owner, "cause": "balloon",
                    })
                else:
                    remaining_mines.append(m)
            self.mines = remaining_mines

        if self.turrets:
            remaining_turrets = []
            for t in self.turrets:
                if t["owner"] != owner and abs(t["x"] - ox) + abs(t["y"] - oy) <= BALLOON_BOMB_RADIUS_CELLS:
                    self.push_event({
                        "kind": "turret_destroyed", "id": t["id"],
                        "x": t["x"], "y": t["y"], "by": owner, "cause": "balloon",
                        "evolved": t.get("evolved", False),
                    })
                else:
                    remaining_turrets.append(t)
            self.turrets = remaining_turrets

        if self.mortars:
            remaining_mortars = []
            for mt in self.mortars:
                if mt["owner"] != owner and abs(mt["x"] - ox) + abs(mt["y"] - oy) <= BALLOON_BOMB_RADIUS_CELLS:
                    self.push_event({
                        "kind": "mortar_destroyed", "id": mt["id"],
                        "x": mt["x"], "y": mt["y"], "by": owner, "cause": "balloon",
                    })
                else:
                    remaining_mortars.append(mt)
            self.mortars = remaining_mortars

        for pet in list(self.pets):
            if pet["owner"] != owner and abs(pet["x"] - ox) + abs(pet["y"] - oy) <= BALLOON_BOMB_RADIUS_CELLS:
                self.destroy_pet(pet, "balloon", owner)

    def mortar_public(self, mt):
        return {
            "id": mt["id"], "x": mt["x"], "y": mt["y"],
            "owner": mt["owner"], "aim": mt.get("aim"),
        }

    def bomb_public(self, bomb):
        """Posizione "in volo" della bomba: interpolazione lineare (in
        linea d'aria, NON sui corridoi) tra il punto di lancio e il punto
        di impatto, in base alla frazione di tempo di volo trascorsa. Serve
        al client per disegnare l'arco e l'ombra proiettata a terra."""
        frac = min(1.0, bomb["t"] / bomb["duration"]) if bomb["duration"] > 0 else 1.0
        fx = bomb["x0"] + (bomb["x1"] - bomb["x0"]) * frac
        fy = bomb["y0"] + (bomb["y1"] - bomb["y0"]) * frac
        return {
            "id": bomb["id"], "x": round(fx, 4), "y": round(fy, 4),
            "tx": bomb["x1"], "ty": bomb["y1"], "owner": bomb["owner"],
            "frac": round(frac, 4),
        }

    def update_mortars(self):
        """Ogni mortaio schierato individua ad OGNI tick il nemico vivo
        piu' vicino e, se e' entro MORTAR_RANGE_CELLS (15 caselle,
        distanza Manhattan), gli spara contro una bomba ogni
        MORTAR_FIRE_INTERVAL_SECONDS (cadenza piu' lenta della torretta,
        e' un'arma d'area molto piu' potente). La bomba non segue i
        corridoi come laser/missili: vola in linea retta SOPRA la mappa
        (vedi update_bombs/bomb_public), scavalcando qualsiasi muro, e
        ricade esplodendo sul bersaglio."""
        if not self.mortars:
            return
        for mt in self.mortars:
            target = self.nearest_alive(mt["x"], mt["y"], {mt["owner"]})
            in_range = (
                target is not None
                and abs(target.x - mt["x"]) + abs(target.y - mt["y"]) <= MORTAR_RANGE_CELLS
            )
            mt["aim"] = [target.x, target.y] if in_range else None
            mt["cd"] -= TICK_DT
            if mt["cd"] > 0:
                continue
            if not in_range:
                # Nessuno nel raggio: il mortaio resta carico (cd fermo a
                # zero) e spara ISTANTANEAMENTE appena qualcuno entra nelle
                # 15 caselle, invece di sprecare colpi a vuoto.
                mt["cd"] = 0.0
                continue
            mt["cd"] = MORTAR_FIRE_INTERVAL_SECONDS
            dist = abs(target.x - mt["x"]) + abs(target.y - mt["y"])
            duration = max(0.15, dist * MORTAR_FLIGHT_SECONDS_PER_CELL)
            bomb = {
                "id": uuid.uuid4().hex[:8],
                "owner": mt["owner"],
                "x0": mt["x"], "y0": mt["y"],
                "x1": target.x, "y1": target.y,
                "t": 0.0, "duration": duration,
            }
            self.bombs.append(bomb)
            self.push_event({
                "kind": "mortar_fire", "id": bomb["id"], "shooter": mt["owner"],
                "x0": mt["x"], "y0": mt["y"], "x1": target.x, "y1": target.y,
                "duration": duration,
            })

    def update_bombs(self):
        """Avanza il tempo di volo di ogni bomba in aria; quando raggiunge
        il punto di impatto esplode (vedi land_bomb) e viene rimossa."""
        if not self.bombs:
            return
        remaining = []
        for bomb in self.bombs:
            bomb["t"] += TICK_DT
            if bomb["t"] >= bomb["duration"]:
                self.land_bomb(bomb)
            else:
                remaining.append(bomb)
        self.bombs = remaining

    def land_bomb(self, bomb):
        """Impatto di una bomba di mortaio (bonus 1200 punti): colpendo
        dall'alto non le importa cosa c'e' nella cella (muro compreso), e
        fa perdere una vita a chiunque si trovi entro MORTAR_BLAST_RADIUS_CELLS
        caselle (distanza Manhattan) dal punto di impatto - un solo colpo
        puo' quindi coinvolgere piu' avversari vicini tra loro. Come per il
        tocco del ninja/corazza, la protezione post-respawn resta immune.

        Oltre al colpo diretto, sul punto di impatto resta a terra una
        nuvola di gas velenoso (vedi update_poison_zones) che continua a
        fare danno ad area nel tempo per POISON_DURATION_SECONDS."""
        self.push_event({
            "kind": "mortar_impact", "id": bomb["id"],
            "x": bomb["x1"], "y": bomb["y1"], "by": bomb["owner"],
        })
        victims = [
            p for p in self.players.values()
            if p.alive and p.id != bomb["owner"]
            and p.ghost_left <= 0 and p.prot_left <= 0
            and abs(p.x - bomb["x1"]) + abs(p.y - bomb["y1"]) <= MORTAR_BLAST_RADIUS_CELLS
        ]
        for victim in victims:
            self.kill_player(victim, "mortar", shooter_id=bomb["owner"])

        poison = {
            "id": uuid.uuid4().hex[:8],
            "owner": bomb["owner"],
            "x": bomb["x1"], "y": bomb["y1"],
            "left": POISON_DURATION_SECONDS,
            "tick_cd": POISON_TICK_SECONDS,
        }
        self.poison_zones.append(poison)
        self.push_event({
            "kind": "poison_spawn", "id": poison["id"],
            "x": poison["x"], "y": poison["y"],
            "duration": POISON_DURATION_SECONDS, "radius": POISON_RADIUS_CELLS,
        })

    def update_poison_zones(self):
        """Avanza ogni nuvola velenosa lasciata a terra dagli impatti del
        mortaio: ogni POISON_TICK_SECONDS toglie una vita a chiunque
        (avversario del proprietario) si trovi ancora entro
        POISON_RADIUS_CELLS caselle dal centro, esattamente come il colpo
        diretto (stessa immunita' di ghost/protezione post-respawn). La
        nuvola svanisce da sola dopo POISON_DURATION_SECONDS."""
        if not self.poison_zones:
            return
        remaining = []
        for pz in self.poison_zones:
            pz["left"] -= TICK_DT
            pz["tick_cd"] -= TICK_DT
            if pz["tick_cd"] <= 0:
                pz["tick_cd"] += POISON_TICK_SECONDS
                victims = [
                    p for p in self.players.values()
                    if p.alive and p.id != pz["owner"]
                    and p.ghost_left <= 0 and p.prot_left <= 0
                    and abs(p.x - pz["x"]) + abs(p.y - pz["y"]) <= POISON_RADIUS_CELLS
                ]
                for victim in victims:
                    self.kill_player(victim, "poison", shooter_id=pz["owner"])
            if pz["left"] > 0:
                remaining.append(pz)
            else:
                self.push_event({"kind": "poison_expire", "id": pz["id"]})
        self.poison_zones = remaining

    def check_armor_effects(self):
        """Bonus 700 punti: chi ha la corazza laser ATTIVA distrugge ogni
        torretta AVVERSARIA (di un altro giocatore, NON la propria) la cui
        cella tocca, e allo stesso modo disinnesca ogni mina AVVERSARIA
        calpestata (anche qui, la propria resta intatta)."""
        armored = [p for p in self.players.values() if p.alive and p.armor_active]
        if not armored:
            return
        if self.turrets:
            remaining = []
            for t in self.turrets:
                destroyer = next(
                    (a for a in armored if a.id != t["owner"] and a.x == t["x"] and a.y == t["y"]),
                    None,
                )
                if destroyer is not None:
                    self.push_event({
                        "kind": "turret_destroyed", "id": t["id"],
                        "x": t["x"], "y": t["y"], "by": destroyer.id,
                        "evolved": t.get("evolved", False),
                    })
                else:
                    remaining.append(t)
            self.turrets = remaining
        if self.mines:
            remaining_mines = []
            for m in self.mines:
                destroyer = next(
                    (a for a in armored if a.id != m["owner"] and a.x == m["x"] and a.y == m["y"]),
                    None,
                )
                if destroyer is not None:
                    self.push_event({
                        "kind": "mine_destroyed", "id": m["id"],
                        "x": m["x"], "y": m["y"], "by": destroyer.id,
                    })
                else:
                    remaining_mines.append(m)
            self.mines = remaining_mines
        if self.mortars:
            remaining_mortars = []
            for mt in self.mortars:
                destroyer = next(
                    (a for a in armored if a.id != mt["owner"] and a.x == mt["x"] and a.y == mt["y"]),
                    None,
                )
                if destroyer is not None:
                    self.push_event({
                        "kind": "mortar_destroyed", "id": mt["id"],
                        "x": mt["x"], "y": mt["y"], "by": destroyer.id,
                    })
                else:
                    remaining_mortars.append(mt)
            self.mortars = remaining_mortars
        # Bonus 900 punti: chi ha la corazza distrugge ogni pet AVVERSARIO
        # (di un altro giocatore, NON il proprio) la cui cella tocca, stessa
        # regola di mine/torrette/mortai qui sopra. NOTA: il proprietario va
        # escluso (a.id != pet["owner"]), altrimenti il proprio pet, che
        # nasce esattamente sulla cella del giocatore, si autodistrugge
        # all'istante se la corazza e' gia' attiva al momento dell'evocazione
        # (bug corretto: il pet "spariva" prima ancora di essere visibile).
        if self.pets:
            for pet in list(self.pets):
                destroyer = next(
                    (a for a in armored if a.id != pet["owner"] and a.x == pet["x"] and a.y == pet["y"]),
                    None,
                )
                if destroyer is not None:
                    self.destroy_pet(pet, "armor", destroyer.id)

    # ---- bonus 900 punti: pet fedele permanente (tasto "8") ----

    def try_summon_pet(self, player):
        """Tasto '8': evoca UNA SOLA VOLTA (per tutto il round) un piccolo
        Pac-Man "pet" dello stesso colore del proprietario, nella sua cella
        corrente. Da quel momento il pet e' permanente: segue il
        proprietario per tutto il resto del round (anche se muore e
        respawna altrove) finche' non aggancia un nemico entro
        PET_RANGE_CELLS caselle, nel qual caso lo insegue attivamente fino
        al contatto (vedi update_pets), finche' non viene distrutto (vedi
        check_mines/move_missiles/move_lasers/try_activate_lightning/
        check_armor_effects): a quel punto sparisce per il resto del round
        e NON si puo' rievocare.

        Se il giocatore e' intrappolato dalla trappola di un avversario,
        NON puo' usare alcun bonus finche' non torna libero di muoversi."""
        if not player.alive or player.trapped_left > 0 or not player.has_pet or player.pet_summoned:
            return
        player.pet_summoned = True
        pet = {
            "id": uuid.uuid4().hex[:8],
            "owner": player.id,
            "x": player.x, "y": player.y,
            "move_accum": 0.0,
            "path": [],
            "retarget_cd": 0.0,
            "target_id": None,
            "aim": None,
        }
        self.pets.append(pet)
        self.push_event({
            "kind": "pet_summon", "id": pet["id"], "player": player.id,
            "x": player.x, "y": player.y,
        })

    def update_pets(self):
        """Il pet NON spara piu' alcun proiettile. Finche' non ha agganciato
        nessuno insegue il proprietario (come prima, fermandosi entro
        PET_STAY_RANGE caselle). Ad ogni tick, se non ha gia' un bersaglio,
        cerca il nemico vivo piu' vicino entro PET_RANGE_CELLS (6) caselle:
        appena lo trova lo aggancia e da quel momento lo insegue ATTIVAMENTE
        (bfs_path, mai attraverso i muri, esattamente come il missile
        guidato) ovunque vada, finche' non lo raggiunge o il bersaglio non
        muore/si disconnette. Al contatto (stessa cella) gli fa perdere una
        vita con il tocco, come il ninja o la corazza laser."""
        if not self.pets:
            return
        for pet in list(self.pets):
            owner = self.players.get(pet["owner"])
            if owner is None:
                continue

            # ---- mantenimento/selezione del bersaglio agganciato ----
            target = self.players.get(pet.get("target_id"))
            if target is not None and not target.alive:
                target = None
            if target is None:
                pet["target_id"] = None
                candidate = self.nearest_alive(pet["x"], pet["y"], {pet["owner"]})
                if candidate is not None and \
                        abs(candidate.x - pet["x"]) + abs(candidate.y - pet["y"]) <= PET_RANGE_CELLS:
                    target = candidate
                    pet["target_id"] = candidate.id
                    pet["path"] = []  # forza un ricalcolo immediato del percorso verso il nuovo bersaglio
            pet["aim"] = [target.x, target.y] if target is not None else None

            # ---- movimento: insegue il bersaglio agganciato, altrimenti il proprietario ----
            if target is not None:
                chase_x, chase_y = target.x, target.y
                stay_range = 0  # nessuna distanza di sicurezza: deve arrivare a contatto
            else:
                chase_x, chase_y = owner.x, owner.y
                stay_range = PET_STAY_RANGE
            dist = abs(chase_x - pet["x"]) + abs(chase_y - pet["y"])
            if dist > stay_range:
                pet["retarget_cd"] -= TICK_DT
                if pet["retarget_cd"] <= 0 or not pet["path"]:
                    pet["retarget_cd"] = PET_RETARGET_SECONDS
                    path = bfs_path(
                        self.maze, self.maze_w, self.maze_h,
                        (pet["x"], pet["y"]), (chase_x, chase_y),
                    )
                    pet["path"] = path or []
                speed = NORMAL_SPEED * PET_SPEED_MULT
                pet["move_accum"] += speed * TICK_DT
                while pet["move_accum"] >= 1.0 and pet["path"]:
                    pet["move_accum"] -= 1.0
                    nx, ny = pet["path"].pop(0)
                    pet["x"], pet["y"] = nx, ny
                    # Appena e' arrivato abbastanza vicino si ferma subito,
                    # invece di continuare a scavalcare il bersaglio/il
                    # proprietario ad ogni tick.
                    if abs(chase_x - pet["x"]) + abs(chase_y - pet["y"]) <= stay_range:
                        pet["path"] = []
                        break
            else:
                pet["path"] = []
                pet["move_accum"] = 0.0

            # ---- attacco al contatto (nessun proiettile, come il ninja/la corazza) ----
            if target is not None and target.alive \
                    and target.ghost_left <= 0 and target.prot_left <= 0 \
                    and pet["x"] == target.x and pet["y"] == target.y:
                self.kill_player(target, "pet", shooter_id=pet["owner"])
                pet["target_id"] = None
                pet["aim"] = None
                pet["path"] = []

    def check_win(self):
        """Il round finisce quando resta UN SOLO giocatore vivo: quello e'
        il vincitore. Con le vite extra e i bonus (laser, mine, super
        assassino) chiunque puo' essere eliminato, quindi il conteggio
        giusto e' sui vivi totali."""
        alive = [p for p in self.players.values() if p.alive]
        if len(alive) == 0:
            # Puo' capitare solo in casi limite (es. disconnessioni):
            # si chiude il round senza vincitori "veri".
            return [], "no_survivors"
        if len(alive) == 1:
            return [alive[0].id], "last_survivor"
        return None, None

    def state_snapshot(self):
        return {
            "type": "state",
            "phase": self.state.lower(),
            "countdown": round(max(self.countdown_left, 0), 1),
            "timer": round(max(self.timer_left, 0), 1),
            "players": [p.to_public() for p in self.players.values()],
            "lasers": [
                {"id": lz["id"], "x": lz["x"], "y": lz["y"], "dir": [lz["dx"], lz["dy"]]}
                for lz in self.lasers
            ],
            "mines": [{"id": m["id"], "x": m["x"], "y": m["y"], "owner": m["owner"]} for m in self.mines],
            "turrets": [self.turret_public(t) for t in self.turrets],
            "pets": [self.pet_public(pt) for pt in self.pets],
            "mortars": [self.mortar_public(mt) for mt in self.mortars],
            "poison_zones": [
                {"id": pz["id"], "x": pz["x"], "y": pz["y"], "left": round(pz["left"], 2)}
                for pz in self.poison_zones
            ],
            "bombs": [self.bomb_public(bomb) for bomb in self.bombs],
            "superbombs": [self.superbomb_public(b) for b in self.superbombs],
            "balloons": [self.balloon_public(b) for b in self.balloons],
            "portal_on": self.portal_on,
            "portal_cycle_left": round(max(self.portal_cycle_left, 0), 1),
            "missiles": [self.missile_public(mz) for mz in self.missiles],
        }

    async def drain_events(self):
        """Invia (e svuota) la coda degli eventi accumulati nel tick."""
        if not self.events:
            return
        pending, self.events = self.events, []
        for ev in pending:
            await self.broadcast(ev)

    def reset_to_lobby(self):
        self.state = "LOBBY"
        self.last_kill = None
        self.events = []
        self.lasers = []
        self.mines = []
        self.missiles = []
        self.turrets = []
        self.pets = []
        self.mortars = []
        self.superbombs = []
        self.balloons = []
        self.bombs = []
        self.poison_zones = []  # nuvole velenose lasciate a terra dagli impatti del mortaio
        for p in self.players.values():
            p.alive = True
            p.direction = None
            p.has_ninja = False
            p.is_assassin = False
            p.assassin_left = 0.0
            p.ninja_used = False
            p.ghost_left = 0.0
            p.prot_left = 0.0
            p.has_laser = False
            p.has_bounce = False
            p.has_mines = False
            p.mines_left = 0
            p.has_missile = False
            p.missiles_left = 0
            p.has_trap = False
            p.trap_target = None
            p.trapped_left = 0.0
            p.trapped_by = None
            p.trap_uses_left = 0
            p.has_turret = False
            p.turret_placed = False
            p.has_armor = False
            p.armor_active = False
            p.armor_left = 0.0
            p.armor_used = False
            p.has_lightning = False
            p.lightning_used = False
            p.has_pet = False
            p.pet_summoned = False
            p.has_robot = False
            p.robot_used = False
            p.has_mortar = False
            p.mortar_placed = False
            p.has_superbomb = False
            p.superbomb_placed = False
            p.has_balloon = False
            p.balloon_launched = False
            p.kills = 0

    # ---------- main loop ----------

    async def run_round(self):
        # Ad ogni nuova partita si pesca a caso una delle 10 mappe: forma,
        # colori e dimensioni cambiano, ma la giocabilita' e' garantita (ogni
        # mappa e' verificata per connettivita' totale al momento della
        # generazione).
        self.pick_new_map()
        self.state = "COUNTDOWN"
        self.countdown_left = COUNTDOWN_SECONDS
        self.assign_spawns()
        await self.broadcast({"type": "round_start", **self.map_payload()})
        await self.broadcast(self.state_snapshot())

        while self.state in ("COUNTDOWN", "PLAYING"):
            await asyncio.sleep(TICK_DT)
            if not self.players:
                return

            # Il movimento e' attivo sia in countdown che in gioco: ci si puo'
            # muovere subito, ancora prima che il round entri nel vivo.
            prev = self.update_movement()
            self.update_rtt_pings()
            self.update_pellet_respawns()
            self.update_portal_cycle()   # accende/spegne i portali ogni 30s
            self.check_collisions(prev)  # no-op finche' nessuno e' super assassino

            if self.state == "COUNTDOWN":
                self.countdown_left -= TICK_DT
                if self.countdown_left <= 0:
                    self.begin_playing()

            elif self.state == "PLAYING":
                self.timer_left -= TICK_DT
                self.update_lasers()  # bonus 150 punti: arma principale permanente, un colpo al secondo se un nemico e' entro 12 caselle
                self.update_turrets() # bonus 600 punti: torretta automatica, stessa cadenza del laser
                self.update_pets()    # bonus 900 punti: il pet insegue il proprietario e attacca chi si avvicina
                self.update_mortars() # bonus 1200 punti: il mortaio spara bombe ad arco contro il nemico piu' vicino entro 15 caselle
                self.move_lasers()    # avanza i proiettili laser in volo (con eventuale rimbalzo)
                self.check_mines()    # bonus 200 punti: fa esplodere le mine calpestate
                self.move_missiles()  # bonus 400 punti: avanza i missili guidati verso il bersaglio
                self.update_bombs()   # bonus 1200 punti: avanza le bombe di mortaio in volo e le fa esplodere all'impatto
                self.update_poison_zones()  # bonus 1200 punti: le nuvole velenose lasciate dagli impatti continuano a fare danno nel tempo
                self.update_superbombs()  # bonus 1400 punti: avanza il conto alla rovescia dei bomboloni piazzati e li fa esplodere dopo 2 secondi
                self.update_balloons()    # bonus 1600 punti: fa vagare a caso le mongolfiere in volo e sganciano bombe istantanee ogni 3 secondi
                self.check_armor_effects()  # bonus 700 punti: la corazza distrugge torrette/mine/pet/mortai avversari toccati
                winners, reason = self.check_win()
                if winners is None and self.timer_left <= 0:
                    alive = [p for p in self.players.values() if p.alive]
                    if alive:
                        best = max(p.points for p in alive)
                        winners = [p.id for p in alive if p.points == best]
                    else:
                        winners = []
                    reason = "time_up"
                if winners is not None:
                    self.state = "ENDED"
                    self.last_result = {"winners": winners, "reason": reason}
                    await self.drain_events()
                    await self.broadcast(self.state_snapshot())
                    await self.broadcast({
                        "type": "round_end",
                        "winners": winners,
                        "reason": reason,
                        "names": {p.id: p.name for p in self.players.values()},
                        "scores": {p.id: p.points for p in self.players.values()},
                    })
                    break

            await self.drain_events()
            await self.broadcast(self.state_snapshot())

        await asyncio.sleep(8)
        if self.code in ROOMS:
            self.reset_to_lobby()
            await self.broadcast_lobby()


def gen_room_code():
    while True:
        code = "".join(random.choice(ROOM_CODE_CHARS) for _ in range(6))
        if code not in ROOMS:
            return code


async def send_error(ws, message):
    await ws.send(encode_text({"type": "error", "message": message}))


def disable_nagle(ws):
    """Disattiva l'algoritmo di Nagle sulla connessione TCP sottostante.

    Di default il sistema operativo raggruppa i pacchetti piccoli prima di
    inviarli, per usare la rete in modo piu' efficiente: ottimo per
    trasferimenti di file, pessimo per un gioco in tempo reale, dove ogni
    messaggio (mossa, stato) e' piccolo e deve arrivare il prima possibile.
    L'interazione tra l'algoritmo di Nagle e gli ACK ritardati del sistema
    ricevente puo' introdurre decine di millisecondi di attesa "invisibile"
    per ogni messaggio: esattamente il tipo di latenza che va eliminato per
    un feeling reattivo come quello richiesto (vedi commenti su TICK_HZ in
    common.py). Va fatto per connessione, non a livello globale, quindi si
    applica al momento in cui il client si collega.
    """
    try:
        sock = ws.transport.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except (OSError, AttributeError):
        pass  # Piattaforme/transport senza socket TCP diretto (raro): ok, si prosegue senza


async def handle_client(ws):
    disable_nagle(ws)
    player = None
    room = None
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            mtype = msg.get("type")

            if mtype == "create_room":
                name = (msg.get("name") or "Player")[:16]
                room = Room(gen_room_code())
                ROOMS[room.code] = room
                player = Player(uuid.uuid4().hex[:8], name, ws)
                room.add_player(player)
                await ws.send(encode_text({
                    "type": "room_created", "code": room.code, "player_id": player.id,
                    **room.map_payload(),
                }))
                await room.broadcast_lobby()

            elif mtype == "join_room":
                code = (msg.get("code") or "").upper().strip()
                name = (msg.get("name") or "Player")[:16]
                room = ROOMS.get(code)
                if room is None:
                    await send_error(ws, "Codice stanza non trovato.")
                    room = None
                    continue
                if room.state != "LOBBY":
                    await send_error(ws, "Partita gia' in corso, riprova piu' tardi.")
                    room = None
                    continue
                if len(room.players) >= MAX_PLAYERS:
                    await send_error(ws, "Stanza piena (max 5 giocatori).")
                    room = None
                    continue
                player = Player(uuid.uuid4().hex[:8], name, ws)
                room.add_player(player)
                await ws.send(encode_text({
                    "type": "joined", "code": room.code, "player_id": player.id,
                    **room.map_payload(),
                }))
                await room.broadcast_lobby()

            elif mtype == "select_color":
                if not room or not player:
                    continue
                raw = msg.get("colors")
                if not isinstance(raw, list):
                    continue
                # Dedup mantenendo l'ordine (colors[0] = primario), max 2,
                # solo nomi validi.
                seen = []
                for c in raw:
                    if c in COLORS and c not in seen:
                        seen.append(c)
                    if len(seen) >= MAX_PLAYER_COLORS:
                        break
                if not seen:
                    continue
                primary = seen[0]
                others_primary = {
                    p.colors[0] for p in room.players.values()
                    if p.colors and p.id != player.id
                }
                if primary in others_primary:
                    await send_error(ws, "Colore primario gia' scelto da un altro giocatore.")
                    continue
                player.colors = seen
                await room.broadcast_lobby()

            elif mtype == "select_character":
                if not room or not player:
                    continue
                character = msg.get("character")
                if character not in CHARACTERS:
                    continue
                player.character = character
                await room.broadcast_lobby()

            elif mtype == "start_game":
                if not room or not player or not player.host:
                    continue
                if room.state != "LOBBY":
                    continue
                if len(room.players) < MIN_PLAYERS:
                    await send_error(ws, f"Servono almeno {MIN_PLAYERS} giocatori.")
                    continue
                if any(not p.colors for p in room.players.values()):
                    await send_error(ws, "Tutti i giocatori devono scegliere un colore.")
                    continue
                room.loop_task = asyncio.create_task(room.run_round())

            elif mtype == "move":
                if not room or not player:
                    continue
                direction = msg.get("direction")
                if direction in DIRECTIONS:
                    # Invece di limitarsi ad accodare la direzione nella
                    # posizione ATTUALE (gia' "nel futuro" per via del
                    # ritardo di rete del pacchetto), si compensa la
                    # latenza: vedi Room._rewind_move per i dettagli. Un
                    # solo input "in attesa" resta comunque possibile alla
                    # volta: una nuova pressione sostituisce sempre quella
                    # precedente.
                    room._rewind_move(player, direction)

            elif mtype == "rtt_pong":
                # Risposta al ping periodico di misura latenza (vedi
                # Room.update_rtt_pings): usata per stimare quanto
                # "tornare indietro" quando arriva una svolta (vedi
                # Room._rewind_move). Media mobile esponenziale per non
                # farsi destabilizzare da un singolo pacchetto in ritardo.
                if not player:
                    continue
                sent_at = msg.get("t")
                if isinstance(sent_at, (int, float)) and sent_at == player.rtt_ping_sent_at:
                    measured = time.monotonic() - sent_at
                    # Clamp difensivo: oltre 1s e' quasi certamente un
                    # outlier di rete, non un vero ritardo costante.
                    measured = max(0.0, min(measured, 1.0))
                    player.rtt = 0.7 * player.rtt + 0.3 * measured

            elif mtype == "place_mine":
                # Bonus 200 punti: pressione del tasto "1" lato client.
                # Il server resta l'autorita' su quante mine restano
                # e su dove vengono posate.
                if not room or not player:
                    continue
                room.try_place_mine(player)

            elif mtype == "activate_ninja":
                # Bonus 300 punti: pressione del tasto "2" lato client.
                if not room or not player:
                    continue
                room.try_activate_ninja(player)

            elif mtype == "fire_missile":
                # Bonus 400 punti: pressione del tasto "3" lato client.
                if not room or not player:
                    continue
                room.try_fire_missile(player)

            elif mtype == "activate_trap":
                # Bonus 500 punti: pressione del tasto "4" lato client
                # (sia per intrappolare che per far detonare).
                if not room or not player:
                    continue
                room.try_activate_trap(player)

            elif mtype == "place_turret":
                # Bonus 600 punti: pressione del tasto "5" lato client.
                # Utilizzabile una sola volta per giocatore (vedi
                # try_place_turret): il server resta l'autorita'.
                if not room or not player:
                    continue
                room.try_place_turret(player)

            elif mtype == "activate_armor":
                # Bonus 700 punti: pressione del tasto "6" lato client.
                if not room or not player:
                    continue
                room.try_activate_armor(player)

            elif mtype == "activate_lightning":
                # Bonus 800 punti: pressione del tasto "7" lato client.
                if not room or not player:
                    continue
                room.try_activate_lightning(player)

            elif mtype == "activate_pet":
                # Bonus 900 punti: pressione del tasto "8" lato client.
                # Utilizzabile una sola volta per giocatore (vedi
                # try_summon_pet): il server resta l'autorita'.
                if not room or not player:
                    continue
                room.try_summon_pet(player)

            elif mtype == "evolve_turret":
                # Bonus 1000 punti: pressione del tasto "9" lato client.
                # Utilizzabile una sola volta per giocatore, e solo se la
                # torretta e' ancora viva (vedi try_evolve_turret): il
                # server resta l'autorita'.
                if not room or not player:
                    continue
                room.try_evolve_turret(player)

            elif mtype == "place_mortar":
                # Tasto "0" lato client: la PRIMA pressione schiera il
                # mortaio (bonus 1200 punti). Una volta che il mortaio e'
                # gia' stato piazzato, la stessa pressione innesca invece il
                # bombolone (bonus 1400 punti, vedi try_place_superbomb).
                # Una volta che ANCHE il bombolone e' gia' stato piazzato,
                # la stessa pressione fa librare in aria la mongolfiera
                # (bonus 1600 punti, vedi try_launch_balloon). Utilizzabile
                # una sola volta ciascuno per giocatore (vedi
                # try_place_mortar/try_place_superbomb/try_launch_balloon):
                # il server resta l'autorita'.
                if not room or not player:
                    continue
                if player.mortar_placed and player.superbomb_placed:
                    room.try_launch_balloon(player)
                elif player.mortar_placed:
                    room.try_place_superbomb(player)
                else:
                    room.try_place_mortar(player)

            elif mtype == "stop":
                # Il tasto/direzione e' stato rilasciato: il personaggio si
                # ferma subito, non continua da solo nell'ultima direzione
                # premuta. Si ferma alla cella corrente (non completa
                # l'eventuale scivolamento verso la cella successiva),
                # esattamente come ci si aspetta rilasciando il tasto.
                if not room or not player:
                    continue
                player.direction = None
                player.next_direction = None
                player.move_accum = 0.0

            elif mtype == "ping":
                await ws.send(encode_text({"type": "pong"}))

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        if player and room:
            player.connected = False
            was_host = player.host
            room.players.pop(player.id, None)
            if not room.players:
                ROOMS.pop(room.code, None)
            else:
                if was_host:
                    new_host = next(iter(room.players.values()))
                    new_host.host = True
                await room.broadcast_lobby()


async def health_check(connection, request):
    """
    Un GET HTTP normale (dal browser che apre il link, o dagli 'health
    check' delle piattaforme di hosting) riceve la pagina del gioco
    (index.html), se presente accanto a questo file. Le vere richieste
    WebSocket del gioco proseguono invece normalmente.

    NB: dalla versione 13 di 'websockets' la firma di process_request e' 
    process_request(connection, request) -> Response | None (non piu'
    process_request(path, request_headers) -> tuple | None come nelle
    versioni vecchie), e il valore di ritorno deve essere un vero oggetto
    websockets.http11.Response (non una tupla): usare la firma/i tipi
    sbagliati fa fallire silenziosamente l'intercettazione delle richieste
    HTTP normali, che finiscono nella pagina d'errore di default del
    protocollo WebSocket ("Non e' riuscito ad aprire una connessione
    WebSocket") anche quando il server e' online e funzionante.
    """
    upgrade = request.headers.get("Upgrade", "")
    if upgrade.lower() == "websocket":
        return None  # lascia proseguire come WebSocket
    if CLIENT_HTML is not None:
        body = CLIENT_HTML.encode("utf-8")
        headers = Headers([
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ])
        return Response(200, "OK", headers, body)
    body = b"Pac-Man Arena server OK\n"
    headers = Headers([
        ("Content-Type", "text/plain; charset=utf-8"),
        ("Content-Length", str(len(body))),
    ])
    return Response(200, "OK", headers, body)


async def main():
    port = int(os.environ.get("PORT") or (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PORT))
    async with websockets.serve(
        handle_client, "0.0.0.0", port, process_request=health_check,
        # compression=None: i pacchetti di gioco sono piccoli (poche centinaia
        # di byte) e frequentissimi (fino a 60/s per stanza). La compressione
        # permessage-deflate ha un costo CPU fisso per messaggio che, su
        # payload cosi' piccoli, supera quasi sempre il risparmio di banda
        # ottenuto: per un gioco in tempo reale conviene spendere quella CPU
        # per spedire prima, non per comprimere meglio.
        compression=None,
    ):
        print(f"Pac-Man Arena (WebSocket) in ascolto sulla porta {port}")
        await asyncio.Future()  # resta acceso per sempre


if __name__ == "__main__":
    asyncio.run(main())
