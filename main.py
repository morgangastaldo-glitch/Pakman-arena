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

import websockets

from common import (
    TICK_DT, COUNTDOWN_SECONDS, ROUND_SECONDS,
    MAX_PLAYERS, MIN_PLAYERS, NORMAL_SPEED, ASSASSIN_SPEED_MULT,
    COLORS, CHARACTERS, DIRECTIONS, is_wall, ROOM_CODE_CHARS,
    pick_random_maze, choose_power_pellet_cells, bfs_path,
    BONUS_THRESHOLDS, GHOST_SECONDS,
    PELLET_POINTS, POWER_PELLET_POINTS, POWER_PELLET_COUNT,
    PELLET_RESPAWN_SECONDS, SUPER_ASSASSIN_THRESHOLD,
    SUPER_ASSASSIN_DURATION_SECONDS, LASER_DURATION_SECONDS,
    SPAWN_PROTECT_SECONDS, LASER_INTERVAL_SECONDS, LASER_FIRST_DELAY_SECONDS,
    LASER_PROJECTILE_SPEED, LASER_BOUNCE_DISTANCE, MINES_COUNT,
    PORTAL_COOLDOWN_SECONDS,
    MISSILE_SPEED_MULT, MISSILES_COUNT, MISSILE_RETARGET_SECONDS,
    TRAP_THRESHOLD, TRAP_DURATION_SECONDS, TRAP_RANGE,
)

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
        self.lives = 1                 # a 50 punti diventa 2: un'eliminazione non ti fa uscire, fa respawnare
        self.claimed = set()           # soglie bonus gia' riscattate in questo round
        self.ghost_left = 0.0          # (bonus fantasma rimosso dal gioco: resta sempre a 0)
        # A SUPER_ASSASSIN_THRESHOLD punti (300): invisibile agli altri,
        # piu' veloce (1.1x rispetto a 1.0 dei giocatori normali) e uccide
        # chiunque al solo contatto. Si attiva una sola volta per round
        # (vedi check_bonuses) e dura solo SUPER_ASSASSIN_DURATION_SECONDS
        # (30s), poi si spegne da sola (vedi il countdown in
        # update_movement); si spegne anche prima se il giocatore viene
        # ucciso (vedi kill_player).
        self.is_assassin = False
        self.assassin_left = 0.0       # secondi rimanenti da super assassino (bonus 300 punti, dura 30s)
        self.prot_left = 0.0           # invulnerabilita' temporanea dopo un respawn
        self.has_laser = False         # bonus 150 punti: laser frontale a intermittenza (1 colpo/secondo)
        self.laser_cd = 0.0            # countdown al prossimo colpo di laser
        self.laser_left = 0.0          # secondi rimanenti col laser attivo (dura 60s)
        self.has_bounce = False        # (non piu' assegnato da alcun bonus, resta sempre False)
        self.has_mines = False         # bonus 200 punti: puo' sganciare mine
        self.mines_left = 0            # mine ancora disponibili in questo round
        self.portal_cd = 0.0           # anti ping-pong dopo un teletrasporto
        # Ultima direzione di marcia nota: e' il "lato frontale" da cui parte
        # il laser anche se in questo istante si e' fermi contro un muro.
        self.facing = "right"
        # ---- bonus 400 punti: missile guidato (tasto "2") ----
        self.has_missile = False
        self.missiles_left = 0
        # ---- bonus 500 punti: trappola (tasto "3") ----
        self.has_trap = False          # bonus sbloccato (una volta per round)
        self.trap_target = None        # id della vittima che QUESTO giocatore ha intrappolato
        self.trapped_left = 0.0        # se > 0, QUESTO giocatore e' intrappolato (immobile)
        self.trapped_by = None         # id di chi lo ha intrappolato (per pulizia alla scadenza/morte)
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
            "ghost": self.ghost_left > 0,
            "assassin": self.is_assassin,
            "prot": self.prot_left > 0,
            "laser": self.has_laser,
            "bounce": self.has_bounce,
            "mines": self.has_mines,
            "mines_left": self.mines_left,
            "missile": self.has_missile,
            "missiles_left": self.missiles_left,
            "trap": self.has_trap,
            "trapped": self.trapped_left > 0,
            "trapped_left": round(self.trapped_left, 1) if self.trapped_left > 0 else 0,
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
        """Due portali ai lati opposti della mappa: per ciascuna colonna
        interna estrema (x=1 a sinistra, x=w-2 a destra) sceglie la cella
        libera piu' vicina alla meta' verticale. Entrare in uno teletrasporta
        all'altro (vedi try_portal)."""
        def best_open_y(x):
            best = None
            mid = self.maze_h // 2
            for y in range(1, self.maze_h - 1):
                if self.maze[y][x] == ".":
                    d = abs(y - mid)
                    if best is None or d < best[0]:
                        best = (d, y)
            return best[1] if best else None
        left_y = best_open_y(1)
        right_y = best_open_y(self.maze_w - 2)
        if left_y is not None and right_y is not None:
            self.portals = [(1, left_y), (self.maze_w - 2, right_y)]
        else:
            self.portals = []

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
            # reset del sistema punti/bonus per il nuovo round
            p.points = 0
            p.lives = 1
            p.claimed = set()
            p.is_assassin = False
            p.assassin_left = 0.0
            p.ghost_left = 0.0
            p.prot_left = 0.0
            p.has_laser = False
            p.laser_cd = 0.0
            p.laser_left = 0.0
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
            p.kills = 0
        self.lasers = []
        self.mines = []
        self.missiles = []

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
            # Bonus 150 punti (laser) e 300 punti (super assassino): durano
            # solo un tempo limitato (60s / 30s), poi si disattivano da soli.
            if p.laser_left > 0:
                p.laser_left = max(0.0, p.laser_left - TICK_DT)
                if p.laser_left <= 0 and p.has_laser:
                    p.has_laser = False
                    self.push_event({"kind": "laser_expired", "player": p.id})
            if p.assassin_left > 0:
                p.assassin_left = max(0.0, p.assassin_left - TICK_DT)
                if p.assassin_left <= 0 and p.is_assassin:
                    p.is_assassin = False
                    self.push_event({"kind": "assassin_off", "player": p.id})

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
            if p.next_direction is not None:
                ndx, ndy = DIRECTIONS[p.next_direction]
                if not is_wall(self.maze, self.maze_w, self.maze_h, p.x + ndx, p.y + ndy):
                    if p.direction != p.next_direction:
                        p.move_accum = 0.0
                    p.direction = p.next_direction
                    p.next_direction = None

            if p.direction is not None:
                # Il "lato frontale" del personaggio (da cui parte il laser)
                # e' l'ultima direzione di marcia, anche da fermi.
                p.facing = p.direction
                dx, dy = DIRECTIONS[p.direction]
                nx, ny = p.x + dx, p.y + dy
                if is_wall(self.maze, self.maze_w, self.maze_h, nx, ny):
                    # Contro un muro: azzera l'accumulo invece di lasciarlo
                    # crescere all'infinito (evita "teletrasporti" quando la
                    # strada si libera).
                    p.move_accum = 0.0
                else:
                    speed = NORMAL_SPEED
                    if p.is_assassin:
                        # Il super assassino (300 punti) e' piu' veloce dei
                        # giocatori normali (stesso moltiplicatore 1.0 -> 1.1).
                        speed *= ASSASSIN_SPEED_MULT
                    p.move_accum += speed * TICK_DT
                    if p.move_accum >= 1.0:
                        p.move_accum -= 1.0
                        p.x, p.y = nx, ny
                        # La svolta in coda, se presente, viene gia' gestita
                        # a inizio tick (vedi sopra): qui non serve
                        # riapplicarla, resta solo l'avanzamento di cella.

            # Pallini e portali si valutano sulla cella in cui ci si trova
            # ORA (anche da fermi: copre lo spawn su un pallino).
            self.eat_pellet(p)
            self.try_portal(p)
        return prev_positions

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

        Il super assassino (300 punti) e' un traguardo a parte rispetto a
        BONUS_THRESHOLDS (soglia fissa SUPER_ASSASSIN_THRESHOLD, non
        configurabile per-mappa), ma segue la stessa regola "una volta sola
        per round": viene riscattato qui insieme agli altri, si attiva per
        SUPER_ASSASSIN_DURATION_SECONDS e poi si spegne da solo (vedi il
        countdown in update_movement), senza piu' riattivarsi anche se i
        punti restano sopra soglia."""
        for threshold, kind in BONUS_THRESHOLDS:
            if p.points < threshold or threshold in p.claimed:
                continue
            p.claimed.add(threshold)
            if kind == "extra_life":
                p.lives += 1
            elif kind == "laser":
                p.has_laser = True
                p.laser_cd = LASER_FIRST_DELAY_SECONDS
                p.laser_left = LASER_DURATION_SECONDS
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
        if (
            p.alive
            and p.points >= SUPER_ASSASSIN_THRESHOLD
            and SUPER_ASSASSIN_THRESHOLD not in p.claimed
        ):
            p.claimed.add(SUPER_ASSASSIN_THRESHOLD)
            p.is_assassin = True
            p.assassin_left = SUPER_ASSASSIN_DURATION_SECONDS
            self.push_event({
                "kind": "assassin_on", "player": p.id,
                "bonus": "assassin", "points": SUPER_ASSASSIN_THRESHOLD,
            })
        # Bonus 500 punti: sblocca la trappola e intrappola SUBITO il nemico
        # piu' vicino (una volta sola per round, come il super assassino).
        if (
            p.alive
            and p.points >= TRAP_THRESHOLD
            and TRAP_THRESHOLD not in p.claimed
        ):
            p.claimed.add(TRAP_THRESHOLD)
            p.has_trap = True
            self.push_event({
                "kind": "bonus", "player": p.id,
                "bonus": "trap", "points": TRAP_THRESHOLD,
            })
            self.try_auto_trap(p)

    def try_portal(self, p):
        """Se il giocatore e' su un portale (e non e' appena arrivato da un
        teletrasporto), lo sposta al portale opposto mantenendo la direzione."""
        if not self.portals or p.portal_cd > 0:
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
        self.push_event({
            "kind": "teleport", "player": p.id,
            "from": [src[0], src[1]], "to": [dest[0], dest[1]],
        })

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

    def kill_player(self, victim, cause, shooter_id=None):
        """Unica via per togliere una vita: usata dal tocco del super
        assassino, dal laser e dalle mine. Con vite extra si respawna,
        altrimenti si e' fuori.

        Chi uccide ruba tutti i punti della vittima, che vengono sommati ai
        propri (vedi richiesta: "ogni volta che un avversario uccide
        qualcuno gli ruba i punti")."""
        self.last_kill = {"cause": cause, "by": shooter_id}
        killer_player = self.players.get(shooter_id) if shooter_id else None
        stolen = 0
        if killer_player is not None and killer_player.id != victim.id:
            stolen = victim.points
            if stolen > 0:
                victim.points = 0
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
        invisibile agli altri, piu' veloce, e uccide chiunque tocchi. Due
        super assassini non si uccidono a vicenda toccandosi."""
        lethal = [p for p in self.players.values() if p.alive and p.is_assassin]
        if not lethal:
            return
        killed_ids = set()
        for L in lethal:
            if L.id in killed_ids or not L.alive:
                continue
            for p in list(self.players.values()):
                if p.id == L.id or not p.alive or p.id in killed_ids:
                    continue
                if p.is_assassin:
                    continue  # due super assassini non si eliminano a vicenda
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
                    self.kill_player(p, "assassin", shooter_id=L.id)
                    killed_ids.add(p.id)

    def update_lasers(self):
        """Bonus 150 punti: ogni LASER_INTERVAL_SECONDS (1 secondo) parte un
        singolo colpo (proiettile) dal lato frontale del personaggio. Il
        super assassino (300 punti, invisibile agli altri) NON spara: il
        proiettile e' visibile a tutti e rivelerebbe subito la sua
        posizione, vanificando l'invisibilita'."""
        for p in list(self.players.values()):
            if not p.alive or not p.has_laser or p.is_assassin:
                continue
            p.laser_cd -= TICK_DT
            if p.laser_cd > 0:
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
                    for v in victims:
                        self.kill_player(v, "laser", lz["owner"])
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
        pressione del tasto "1" lato client."""
        if not player.alive or not player.has_mines or player.mines_left <= 0:
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
        (proprietario escluso), ignorando protezioni, come il laser."""
        if not self.mines:
            return
        remaining = []
        for m in self.mines:
            victims = [
                q for q in self.players.values()
                if q.alive and q.id != m["owner"] and q.x == m["x"] and q.y == m["y"]
            ]
            if victims:
                for v in victims:
                    self.kill_player(v, "mine", m["owner"])
                self.push_event({"kind": "mine_boom", "id": m["id"], "x": m["x"], "y": m["y"]})
            else:
                remaining.append(m)
        self.mines = remaining

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

    # ---- bonus 400 punti: missile guidato (tasto "2") ----

    def try_fire_missile(self, player):
        """Spara un missile (finche' ne restano) verso il nemico piu' vicino
        in questo istante. Il missile e' 'guidato': segue i corridoi via
        pathfinding (vedi move_missiles), non attraversa mai i muri, e si
        aggancia di nuovo al bersaglio piu' vicino se quello originale muore
        prima dell'impatto."""
        if not player.alive or not player.has_missile or player.missiles_left <= 0:
            return
        target = self.nearest_alive(player.x, player.y, {player.id})
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
            if target is None or not target.alive:
                target = self.nearest_alive(mz["x"], mz["y"], {mz["owner"]})
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
                    victims = [
                        q for q in self.players.values()
                        if q.alive and q.id != mz["owner"] and q.x == nx and q.y == ny
                    ]
                    if victims:
                        for v in victims:
                            self.kill_player(v, "missile", mz["owner"])
                        destroyed = True

            if destroyed:
                self.push_event({"kind": "missile_end", "id": mz["id"], "x": mz["x"], "y": mz["y"]})
            else:
                survivors.append(mz)
        self.missiles = survivors

    # ---- bonus 500 punti: trappola (tasto "3") ----

    def try_auto_trap(self, player):
        """Intrappola SUBITO il nemico piu' vicino a chi ha appena sbloccato
        il bonus (500 punti): resta bloccato sul posto per
        TRAP_DURATION_SECONDS, finche' non viene fatto detonare in tempo
        (try_detonate_trap) oppure la trappola scade da sola."""
        target = self.nearest_alive(player.x, player.y, {player.id})
        if target is None:
            return
        target.trapped_left = TRAP_DURATION_SECONDS
        target.trapped_by = player.id
        player.trap_target = target.id
        self.push_event({
            "kind": "trap_start", "player": player.id, "victim": target.id,
            "seconds": TRAP_DURATION_SECONDS,
        })

    def try_detonate_trap(self, player):
        """Tasto '3': se il nemico che questo giocatore ha intrappolato e'
        ancora bloccato ed e' abbastanza vicino (TRAP_RANGE celle), lo
        distrugge con una piccola esplosione (perde una vita)."""
        if not player.alive or not player.has_trap or not player.trap_target:
            return
        victim = self.players.get(player.trap_target)
        if victim is None or not victim.alive or victim.trapped_left <= 0:
            player.trap_target = None
            return
        dist = max(abs(victim.x - player.x), abs(victim.y - player.y))
        if dist > TRAP_RANGE:
            return
        self.push_event({"kind": "trap_boom", "x": victim.x, "y": victim.y})
        self.kill_player(victim, "trap", player.id)
        player.trap_target = None

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
            "missiles": [
                {"id": mz["id"], "x": mz["x"], "y": mz["y"], "owner": mz["owner"], "target": mz["target"]}
                for mz in self.missiles
            ],
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
        for p in self.players.values():
            p.alive = True
            p.direction = None
            p.is_assassin = False
            p.assassin_left = 0.0
            p.ghost_left = 0.0
            p.prot_left = 0.0
            p.has_laser = False
            p.laser_left = 0.0
            p.has_bounce = False
            p.has_mines = False
            p.mines_left = 0
            p.has_missile = False
            p.missiles_left = 0
            p.has_trap = False
            p.trap_target = None
            p.trapped_left = 0.0
            p.trapped_by = None
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
            self.update_pellet_respawns()
            self.check_collisions(prev)  # no-op finche' nessuno e' super assassino

            if self.state == "COUNTDOWN":
                self.countdown_left -= TICK_DT
                if self.countdown_left <= 0:
                    self.begin_playing()

            elif self.state == "PLAYING":
                self.timer_left -= TICK_DT
                self.update_lasers()  # bonus 150 punti: un colpo al secondo, dura 60s
                self.move_lasers()    # avanza i proiettili laser in volo (con eventuale rimbalzo)
                self.check_mines()    # bonus 200 punti: fa esplodere le mine calpestate
                self.move_missiles()  # bonus 400 punti: avanza i missili guidati verso il bersaglio
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
                    player.next_direction = direction

            elif mtype == "place_mine":
                # Bonus 200 punti: pressione del tasto "1" lato client.
                # Il server resta l'autorita' su quante mine restano
                # e su dove vengono posate.
                if not room or not player:
                    continue
                room.try_place_mine(player)

            elif mtype == "fire_missile":
                # Bonus 400 punti: pressione del tasto "2" lato client.
                if not room or not player:
                    continue
                room.try_fire_missile(player)

            elif mtype == "detonate_trap":
                # Bonus 500 punti: pressione del tasto "3" lato client.
                if not room or not player:
                    continue
                room.try_detonate_trap(player)

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


async def health_check(path, request_headers):
    """
    Un GET HTTP normale (dal browser che apre il link, o dagli 'health
    check' delle piattaforme di hosting) riceve la pagina del gioco
    (index.html), se presente accanto a questo file. Le vere richieste
    WebSocket del gioco proseguono invece normalmente.
    """
    if "Upgrade" in request_headers and request_headers["Upgrade"].lower() == "websocket":
        return None  # lascia proseguire come WebSocket
    if CLIENT_HTML is not None:
        return (200, [("Content-Type", "text/html; charset=utf-8")], CLIENT_HTML.encode("utf-8"))
    return (200, [("Content-Type", "text/plain")], b"Pac-Man Arena server OK\n")


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
