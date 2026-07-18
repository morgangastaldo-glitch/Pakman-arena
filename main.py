"""
Pac-Man Arena 1vAll - server WebSocket

Questo file contiene la STESSA IDENTICA logica di gioco di server.py
(lobby, countdown, scelta del killer, movimento, collisioni, condizioni
di vittoria, timer, codici stanza): nessuna regola e' stata cambiata.

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

Avvio locale:      python3 server_web.py [porta]   (default 8765)
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
    TICK_DT, COUNTDOWN_SECONDS, ROUND_SECONDS, KILLER_INTERVAL_SECONDS,
    MAX_PLAYERS, MIN_PLAYERS, NORMAL_SPEED, KILLER_SPEED_MULT,
    COLORS, CHARACTERS, DIRECTIONS, is_wall, ROOM_CODE_CHARS,
    pick_random_maze,
    BONUS_THRESHOLDS, BOOST_MULT, BOOST_SECONDS, GHOST_SECONDS,
    SPAWN_PROTECT_SECONDS, LASER_INTERVAL_SECONDS, LASER_FIRST_DELAY_SECONDS,
    PORTAL_COOLDOWN_SECONDS,
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
        self.is_killer = False
        self.connected = True
        # ---- sistema punti / bonus (azzerato ad ogni round) ----
        self.points = 0
        self.lives = 1                 # a 15 punti diventa 2: il killer non elimina, fa respawnare
        self.claimed = set()           # soglie bonus gia' riscattate in questo round
        self.boost_left = 0.0          # secondi rimanenti di velocita' x2 (bonus 30 punti)
        self.ghost_left = 0.0          # secondi rimanenti di modalita' fantasma (bonus 50 punti)
        self.prot_left = 0.0           # invulnerabilita' dal killer dopo un respawn
        self.has_laser = False         # bonus 100 punti: laser frontale a intermittenza
        self.laser_cd = 0.0            # countdown al prossimo colpo di laser
        self.portal_cd = 0.0           # anti ping-pong dopo un teletrasporto
        # Ultima direzione di marcia nota: e' il "lato frontale" da cui parte
        # il laser anche se in questo istante si e' fermi contro un muro.
        self.facing = "right"

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
            "alive": self.alive, "is_killer": self.is_killer,
            # ---- nuovi campi per HUD/rendering ----
            "points": self.points, "lives": self.lives,
            "ghost": self.ghost_left > 0,
            "boost": self.boost_left > 0,
            "prot": self.prot_left > 0,
            "laser": self.has_laser,
        }


class Room:
    def __init__(self, code):
        self.code = code
        self.players: dict[str, Player] = {}
        self.state = "LOBBY"  # LOBBY, COUNTDOWN, PLAYING, ENDED
        self.countdown_left = 0.0
        self.timer_left = 0.0
        self.killer_id = None
        self.killer_timer = 0.0
        self.loop_task = None
        self.last_result = None
        # Il killer NON e' piu' l'unica causa di morte (c'e' anche il laser)
        # e la vittoria e' "ultimo giocatore vivo": per decidere il titolo di
        # fine round teniamo traccia di com'e' avvenuta l'ultima eliminazione.
        self.last_kill = None
        # Eventi una-tantum (uccisioni, bonus, laser, teletrasporti, pallini
        # mangiati) accumulati durante il tick e trasmessi subito dopo: sono
        # cio' che permette al client effetti/suoni precisi senza dover
        # "indovinare" confrontando snapshot consecutivi.
        self.events = []
        # Mappa corrente della stanza: viene ripescata a caso tra le 10
        # disponibili a OGNI inizio round (vedi run_round), cosi' ogni
        # partita puo' capitare su una mappa diversa per forma/colore/misura.
        map_data = pick_random_maze()
        self.maze = map_data["maze"]
        self.maze_w = map_data["w"]
        self.maze_h = map_data["h"]
        self.maze_name = map_data["name"]
        self.spawn_points = map_data["spawn_points"]
        self.theme = map_data["theme"]
        self.compute_portals()
        self.reset_pellets()

    def pick_new_map(self):
        map_data = pick_random_maze()
        self.maze = map_data["maze"]
        self.maze_w = map_data["w"]
        self.maze_h = map_data["h"]
        self.maze_name = map_data["name"]
        self.spawn_points = map_data["spawn_points"]
        self.theme = map_data["theme"]
        self.compute_portals()
        self.reset_pellets()

    def reset_pellets(self):
        """Ricrea l'insieme dei pallini: ogni cella libera della mappa ne
        contiene uno e vale 1 punto. Il server e' l'autorita' (prima erano
        solo decorativi lato client): cosi' i punti sono uguali per tutti."""
        self.pellets = {
            (x, y)
            for y, row in enumerate(self.maze)
            for x, ch in enumerate(row)
            if ch == "."
        }

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
            p.is_killer = False
            # reset del sistema punti/bonus per il nuovo round
            p.points = 0
            p.lives = 1
            p.claimed = set()
            p.boost_left = 0.0
            p.ghost_left = 0.0
            p.prot_left = 0.0
            p.has_laser = False
            p.laser_cd = 0.0
            p.portal_cd = 0.0
            p.facing = "right"
        self.killer_id = None

    def start_killer_phase(self):
        self.state = "PLAYING"
        self.timer_left = ROUND_SECONDS
        alive_ids = [p.id for p in self.players.values() if p.alive]
        killer_id = random.choice(alive_ids)
        self.killer_id = killer_id
        self.players[killer_id].is_killer = True
        # Un killer invisibile sarebbe ingiocabile per gli altri: se il
        # prescelto era in modalita' fantasma, il fantasma svanisce.
        self.players[killer_id].ghost_left = 0.0
        self.killer_timer = KILLER_INTERVAL_SECONDS

    def rotate_killer(self):
        """Sceglie un nuovo killer casuale tra i giocatori vivi. Chiamato
        ogni KILLER_INTERVAL_SECONDS per tutta la durata del round, e subito
        se il killer in carica viene eliminato dal laser."""
        alive_ids = [p.id for p in self.players.values() if p.alive]
        if not alive_ids:
            return
        if self.killer_id in self.players:
            self.players[self.killer_id].is_killer = False
        new_killer_id = random.choice(alive_ids)
        self.killer_id = new_killer_id
        self.players[new_killer_id].is_killer = True
        self.players[new_killer_id].ghost_left = 0.0  # il killer e' sempre visibile
        self.killer_timer = KILLER_INTERVAL_SECONDS

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
            if p.boost_left > 0:
                p.boost_left = max(0.0, p.boost_left - TICK_DT)
            if p.ghost_left > 0:
                p.ghost_left = max(0.0, p.ghost_left - TICK_DT)
            if p.prot_left > 0:
                p.prot_left = max(0.0, p.prot_left - TICK_DT)
            if p.portal_cd > 0:
                p.portal_cd = max(0.0, p.portal_cd - TICK_DT)

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
                    if p.is_killer:
                        speed *= KILLER_SPEED_MULT
                    if p.boost_left > 0:
                        speed *= BOOST_MULT  # bonus 30 punti: velocita' x2
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
        p.points += 1
        self.push_event({"kind": "pellet", "cells": [[p.x, p.y]], "by": p.id})
        self.check_bonuses(p)

    def check_bonuses(self, p):
        """Riscatta i traguardi appena superati (una volta sola per round)."""
        for threshold, kind in BONUS_THRESHOLDS:
            if p.points < threshold or threshold in p.claimed:
                continue
            p.claimed.add(threshold)
            if kind == "extra_life":
                p.lives += 1
            elif kind == "speed":
                p.boost_left = BOOST_SECONDS
            elif kind == "ghost":
                # Il killer in carica resta comunque visibile lato client;
                # per lui il bonus e' di fatto "in pausa" finche' e' killer
                # (vedi rotate_killer, che azzera ghost_left al passaggio).
                p.ghost_left = GHOST_SECONDS
            elif kind == "laser":
                p.has_laser = True
                p.laser_cd = LASER_FIRST_DELAY_SECONDS
            self.push_event({
                "kind": "bonus", "player": p.id,
                "bonus": kind, "points": threshold,
            })

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
        """Rimette in gioco chi aveva una vita extra: nello spawn piu'
        lontano dal killer, con qualche secondo di protezione."""
        killer = self.players.get(self.killer_id)
        spots = self.spawn_points[:]
        if killer and killer.alive:
            spots.sort(key=lambda s: -(abs(s[0] - killer.x) + abs(s[1] - killer.y)))
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
        """Unica via per togliere una vita: usata sia dal tocco del killer
        sia dal laser. Con vite extra si respawna, altrimenti si e' fuori.
        Se il laser elimina il killer in carica, ne viene scelto subito uno
        nuovo tra i vivi."""
        self.last_kill = {"cause": cause, "killer": self.killer_id}
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
            if victim.id == self.killer_id:
                self.rotate_killer()
        self.push_event({
            "kind": "kill", "victim": victim.id, "cause": cause,
            "by": shooter_id, "at": died_at,
            "respawn": respawned, "lives": max(victim.lives, 0),
        })

    def check_collisions(self, prev_positions):
        killer = self.players.get(self.killer_id)
        if not killer or not killer.alive:
            return
        for p in list(self.players.values()):
            if p.id == killer.id or not p.alive:
                continue
            # Il tocco del killer non puo' nulla contro la modalita' fantasma
            # (bonus 50 punti) ne' contro la protezione post-respawn. Il
            # laser invece ignora entrambe (vedi fire_laser).
            if p.ghost_left > 0 or p.prot_left > 0:
                continue
            same_cell = (p.x == killer.x and p.y == killer.y)
            swapped = (
                prev_positions.get(p.id) == (killer.x, killer.y)
                and prev_positions.get(killer.id) == (p.x, p.y)
            )
            if same_cell or swapped:
                self.kill_player(p, "killer")

    def update_lasers(self):
        """Bonus 100 punti: ogni LASER_INTERVAL_SECONDS parte un colpo dal
        lato frontale del personaggio."""
        for p in list(self.players.values()):
            if not p.alive or not p.has_laser:
                continue
            p.laser_cd -= TICK_DT
            if p.laser_cd > 0:
                continue
            p.laser_cd = LASER_INTERVAL_SECONDS
            self.fire_laser(p)

    def fire_laser(self, shooter):
        """Raggio istantaneo: percorre tutta la distanza libera davanti al
        giocatore e si infrange sul primo muro o sul primo giocatore colpito.
        Elimina QUALSIASI giocatore (fantasmi e protetti inclusi)."""
        dx, dy = DIRECTIONS.get(shooter.facing, (1, 0))
        x, y = shooter.x, shooter.y
        last_free = (shooter.x, shooter.y)
        hit = []
        while True:
            x += dx
            y += dy
            if is_wall(self.maze, self.maze_w, self.maze_h, x, y):
                break
            last_free = (x, y)
            victims = [
                q for q in self.players.values()
                if q.alive and q.id != shooter.id and q.x == x and q.y == y
            ]
            if victims:
                hit = victims
                break
        self.push_event({
            "kind": "laser", "shooter": shooter.id,
            "from": [shooter.x, shooter.y],
            "to": [last_free[0], last_free[1]],
            "dir": shooter.facing,
            "hit": [v.id for v in hit],
        })
        for v in hit:
            self.kill_player(v, "laser", shooter.id)

    def check_win(self):
        """FIX richiesto: il round NON finisce piu' alla prima eliminazione.
        Si continua finche' resta UN SOLO giocatore vivo: quello e' il
        vincitore (che sia l'ultimo fuggitivo o il killer che ha eliminato
        tutti). Con le vite extra e il laser chiunque puo' essere eliminato,
        quindi il conteggio giusto e' sui vivi totali, non sui fuggitivi."""
        alive = [p for p in self.players.values() if p.alive]
        if len(alive) == 0:
            # Puo' capitare solo in casi limite (es. disconnessioni):
            # si chiude il round senza vincitori "veri".
            winners = [self.killer_id] if self.killer_id in self.players else []
            return winners, "killer_wins"
        if len(alive) == 1:
            w = alive[0]
            lk = self.last_kill
            if lk and lk["cause"] == "killer" and w.id == lk["killer"]:
                reason = "killer_wins"
            else:
                reason = "last_survivor"
            return [w.id], reason
        return None, None

    def state_snapshot(self):
        return {
            "type": "state",
            "phase": self.state.lower(),
            "countdown": round(max(self.countdown_left, 0), 1),
            "timer": round(max(self.timer_left, 0), 1),
            "killer_timer": round(max(self.killer_timer, 0), 1),
            "killer_id": self.killer_id,
            "players": [p.to_public() for p in self.players.values()],
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
        self.killer_id = None
        self.killer_timer = 0.0
        self.last_kill = None
        self.events = []
        for p in self.players.values():
            p.alive = True
            p.is_killer = False
            p.direction = None
            p.boost_left = 0.0
            p.ghost_left = 0.0
            p.prot_left = 0.0
            p.has_laser = False

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
            # muovere subito, ancora prima che il killer venga rivelato.
            prev = self.update_movement()
            self.check_collisions(prev)  # no-op finche' non c'e' un killer

            if self.state == "COUNTDOWN":
                self.countdown_left -= TICK_DT
                if self.countdown_left <= 0:
                    self.start_killer_phase()

            elif self.state == "PLAYING":
                self.timer_left -= TICK_DT
                self.killer_timer -= TICK_DT
                self.update_lasers()  # bonus 100 punti: colpi a intermittenza
                if self.killer_timer <= 0:
                    self.rotate_killer()
                winners, reason = self.check_win()
                if winners is None and self.timer_left <= 0:
                    survivors = [p.id for p in self.players.values()
                                 if p.alive and not p.is_killer]
                    winners = survivors if survivors else [self.killer_id]
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
