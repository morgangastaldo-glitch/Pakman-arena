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
import sys
import uuid

import websockets

from common import (
    TICK_DT, COUNTDOWN_SECONDS, ROUND_SECONDS, KILLER_INTERVAL_SECONDS,
    MAX_PLAYERS, MIN_PLAYERS, NORMAL_SPEED, KILLER_SPEED_MULT,
    COLORS, CHARACTERS, DIRECTIONS, is_wall, ROOM_CODE_CHARS,
    pick_random_maze,
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
        self.initial_survivor_count = 0
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

    def pick_new_map(self):
        map_data = pick_random_maze()
        self.maze = map_data["maze"]
        self.maze_w = map_data["w"]
        self.maze_h = map_data["h"]
        self.maze_name = map_data["name"]
        self.spawn_points = map_data["spawn_points"]
        self.theme = map_data["theme"]

    def map_payload(self):
        return {
            "maze": self.maze, "maze_w": self.maze_w, "maze_h": self.maze_h,
            "maze_name": self.maze_name, "theme": self.theme,
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
        self.killer_id = None

    def start_killer_phase(self):
        self.state = "PLAYING"
        self.timer_left = ROUND_SECONDS
        alive_ids = [p.id for p in self.players.values() if p.alive]
        killer_id = random.choice(alive_ids)
        self.killer_id = killer_id
        self.players[killer_id].is_killer = True
        self.initial_survivor_count = len(alive_ids) - 1
        self.killer_timer = KILLER_INTERVAL_SECONDS

    def rotate_killer(self):
        """Sceglie un nuovo killer casuale tra i giocatori vivi. Chiamato
        ogni KILLER_INTERVAL_SECONDS per tutta la durata del round."""
        alive_ids = [p.id for p in self.players.values() if p.alive]
        if not alive_ids:
            return
        if self.killer_id in self.players:
            self.players[self.killer_id].is_killer = False
        new_killer_id = random.choice(alive_ids)
        self.killer_id = new_killer_id
        self.players[new_killer_id].is_killer = True
        self.killer_timer = KILLER_INTERVAL_SECONDS

    # ---------- game tick ----------

    def update_movement(self):
        prev_positions = {p.id: (p.x, p.y) for p in self.players.values()}
        for p in self.players.values():
            if not p.alive:
                continue

            # Appena la direzione "in coda" diventa percorribile dalla cella
            # attuale, diventa la direzione corrente: e' cosi' che nel
            # Pac-Man originale una svolta premuta con un attimo di anticipo
            # (mentre si e' ancora contro il muro sbagliato) non va persa,
            # ma scatta un istante dopo, non appena possibile.
            if p.next_direction is not None:
                ndx, ndy = DIRECTIONS[p.next_direction]
                if not is_wall(self.maze, self.maze_w, self.maze_h, p.x + ndx, p.y + ndy):
                    p.direction = p.next_direction
                    p.next_direction = None

            if p.direction is None:
                continue

            dx, dy = DIRECTIONS[p.direction]
            nx, ny = p.x + dx, p.y + dy
            if is_wall(self.maze, self.maze_w, self.maze_h, nx, ny):
                # Contro un muro: azzera l'accumulo invece di lasciarlo
                # crescere all'infinito. Senza questo, appena la strada si
                # liberava (es. dopo che il killer cambia e la fisica
                # riprende) il giocatore "teletrasportava" di piu' celle in
                # un colpo solo, altro sintomo dei comandi imprecisi.
                p.move_accum = 0.0
                continue

            speed = NORMAL_SPEED * (KILLER_SPEED_MULT if p.is_killer else 1.0)
            p.move_accum += speed * TICK_DT
            if p.move_accum >= 1.0:
                p.move_accum -= 1.0
                p.x, p.y = nx, ny
        return prev_positions

    def check_collisions(self, prev_positions):
        killer = self.players.get(self.killer_id)
        if not killer or not killer.alive:
            return
        for p in self.players.values():
            if p.id == killer.id or not p.alive:
                continue
            same_cell = (p.x == killer.x and p.y == killer.y)
            swapped = (
                prev_positions[p.id] == (killer.x, killer.y)
                and prev_positions[killer.id] == (p.x, p.y)
            )
            if same_cell or swapped:
                p.alive = False

    def check_win(self):
        survivors = [p for p in self.players.values() if p.alive and not p.is_killer]
        if len(survivors) == 0:
            return [self.killer_id], "killer_wins"
        if len(survivors) == 1 and self.initial_survivor_count > 1:
            return [survivors[0].id], "last_survivor"
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

    def reset_to_lobby(self):
        self.state = "LOBBY"
        self.killer_id = None
        self.killer_timer = 0.0
        for p in self.players.values():
            p.alive = True
            p.is_killer = False
            p.direction = None

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
                    await self.broadcast(self.state_snapshot())
                    await self.broadcast({
                        "type": "round_end",
                        "winners": winners,
                        "reason": reason,
                        "names": {p.id: p.name for p in self.players.values()},
                    })
                    break

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


async def handle_client(ws):
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
        handle_client, "0.0.0.0", port, process_request=health_check
    ):
        print(f"Pac-Man Arena (WebSocket) in ascolto sulla porta {port}")
        await asyncio.Future()  # resta acceso per sempre


if __name__ == "__main__":
    asyncio.run(main())
