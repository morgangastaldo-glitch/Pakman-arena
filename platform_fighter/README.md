# Platform Fighter Prototype (Godot 4.3 + GDScript)

Prototipo avanzato di platform fighter 2D in stile **Brawlhalla / Super Smash Bros**:
danno percentuale, knockback cumulativo, hitstun, hitlag, DI, piattaforme sottili
attraversabili, Ring Out e HUD, per **due giocatori in locale** (stessa tastiera
oppure due gamepad).

---

## Perche' Godot 4 e non Unity

Per un platform fighter 2D **Godot 4.3 con GDScript** e' la scelta migliore:

| Criterio | Godot 4 | Unity |
|---|---|---|
| Fisica 2D | motore 2D nativo, in pixel: niente scala fisica finta ne' `Rigidbody2D` da domare | 2D costruito sopra un motore 3D, con `PhysicsMaterial` e scala in metri da compensare |
| `CharacterBody2D` | `move_and_slide()` cinematico, deterministico, pensato per i platformer | serve un character controller custom o Kinematic scritto a mano |
| Hitbox / Hurtbox | `Area2D` con layer/maschere dedicate, gia' separati dai corpi fisici | `Collider2D` + `isTrigger`, con piu' rischio di collisioni indesiderate |
| Iterazione | ricarica a caldo degli script, avvio del progetto in un secondo | tempi di compilazione domain-reload molto piu' lunghi |
| Frame data | tick di fisica fisso a 60 Hz, semplice da rendere deterministico | fixed timestep configurabile ma piu' facile da sbagliare |

Un fighter si tara **per tentativi, migliaia di volte**: il ciclo di iterazione corto
di Godot vale piu' di qualsiasi altra feature. Unity resta preferibile se servono
console proprietarie o un ecosistema di asset store gia' pagato: in quel caso
l'architettura di questi script (dati, componenti, manager) si traduce 1:1 in C#.

---

## Avvio rapido

```bash
# 1. generare input map e scene (una sola volta)
godot --headless --import
godot --headless res://tools/build_project.tscn

# 2. giocare
godot res://scenes/Arena.tscn        # oppure: aprire il progetto nell'editor e premere F5

# 3. verifica automatica di fisica e combattimento (36 controlli)
godot --headless res://tools/smoke_test.tscn
```

Aprendo il progetto nell'editor le scene sono gia' pronte: `tools/build_project.tscn`
serve solo a rigenerarle o a ricreare le azioni di input da zero.

---

## Comandi

| Azione | Giocatore 1 | Giocatore 2 | Gamepad (P1 = device 0, P2 = device 1) |
|---|---|---|---|
| Movimento / mira | `A` `D` `W` `S` | frecce | stick sinistro + D-pad |
| Salto / doppio salto | `Spazio` | `Tastierino 0` oppure `L` | `A` |
| Attacco leggero | `F` | `Tastierino 1` oppure `J` | `X` |
| Attacco pesante | `G` | `Tastierino 2` oppure `K` | `B` |
| Schivata | `Shift sx` | `Tastierino 3` oppure `H` | `R1` |
| Riavvia match | `R` | `R` | `Start` |

* **Doppio salto**: salto di nuovo in aria; da' anche una spinta verso la direzione premuta (recovery).
* **Fast fall**: `GIU'` mentre si scende.
* **Drop-through**: `GIU'` + `SALTO` stando su una piattaforma sottile.
* **Direzione + attacco**: neutro / laterale / alto / basso danno mosse diverse, a terra e in aria (il basso in aria e' uno **spike**).
* **DI**: tenendo una direzione mentre si viene colpiti si curva la traiettoria di volo (fino a 18 gradi).

---

## Come e' impostata la scena

```
Arena (Node2D)                        <- res://scenes/Arena.tscn
├── Background (CanvasLayer, layer -10)
│   └── Sky (ColorRect a schermo pieno)
├── World (Node2D)
│   ├── MainPlatform  (FightPlatform, 760x56 @ (0,180))   -> layer 1 "solid"
│   ├── PlatformLeft  (FightPlatform one-way, 250x24 @ (-330,10))  -> layer 2 "one_way"
│   ├── PlatformRight (FightPlatform one-way, 250x24 @ (330,10))
│   └── PlatformTop   (FightPlatform one-way, 270x24 @ (0,-150))
├── SpawnPoints (Node2D)
│   └── Spawn1..4 (Marker2D, gruppo "spawn_point")
├── BlastZones (Node2D)               <- RING OUT
│   ├── BlastLeft   (BlastZone 400x2600 @ (-1150,-150))
│   ├── BlastRight  (BlastZone 400x2600 @ (1150,-150))
│   ├── BlastTop    (BlastZone 2700x400 @ (0,-1050))
│   └── BlastBottom (BlastZone 2700x400 @ (0,900))
├── Fighters (Node2D)
│   ├── Player1 (istanza di Player.tscn, player_index = 1)
│   └── Player2 (istanza di Player.tscn, player_index = 2)
├── ArenaCamera (Camera2D dinamica, segue il baricentro + screen shake)
├── GameManager (Node: vite, Ring Out, respawn, vittoria)
└── HUD (CanvasLayer: percentuali e vite, costruito da codice)
```

```
Fighter (CharacterBody2D)             <- res://scenes/Player.tscn
├── CollisionShape2D   (RectangleShape2D 34x58, centro a (0,-29))
├── Visual             (CharacterVisual: segnaposto o AnimatedSprite2D)
├── Hurtbox (Area2D)   -> layer 5 "hurtbox", volume vulnerabile
│   └── CollisionShape2D (40x62)
└── Hitbox  (Area2D)   -> layer 4 "hitbox", forma riscritta a ogni mossa
    └── CollisionShape2D (disabilitata a riposo)
```

**L'origine del personaggio e' ai piedi (y = 0)**: appoggiarlo su una piattaforma
significa dargli la stessa Y della superficie, senza calcoli mentali.

### Layer di fisica (project.godot, tabella in `scripts/core/combat_layers.gd`)

| # | Nome | Chi lo usa | Chi lo cerca |
|---|---|---|---|
| 1 | `solid` | piattaforma principale, muri | corpo dei giocatori |
| 2 | `one_way` | piattaforme sottili | corpo dei giocatori (disattivato durante il drop-through) |
| 3 | `player` | corpo dei personaggi | blast zone |
| 4 | `hitbox` | volumi offensivi | — |
| 5 | `hurtbox` | volumi vulnerabili | hitbox |
| 6 | `blast_zone` | zone di Ring Out | — |

I giocatori **non collidono tra loro** (come in Brawlhalla): interagiscono solo
tramite hitbox/hurtbox. Le piattaforme non cercano nessuno (`collision_mask = 0`):
e' sempre il personaggio a cercare il mondo.

### Aggiungere una piattaforma o cambiare l'arena

`FightPlatform` e' parametrico e `@tool`: si trascina in scena, si imposta `size`,
si spunta `one_way` e la forma di collisione + la grafica si aggiornano da sole
nell'editor. Le blast zone funzionano allo stesso modo con `size`.
Per un'arena piu' "perdonante" si allontanano le blast zone; per una piu' punitiva
si avvicinano.

---

## I due script principali

### 1. `scripts/player/player_controller.gd`
Fisica, macchina a stati (`IDLE, RUN, JUMP, FALL, LAND, ATTACK, HITSTUN, DODGE, KO, RESPAWN`)
e trigger visivi. Contiene: gravita' asimmetrica derivata da altezza/tempi di salto,
salto variabile, coyote time, jump buffer, turn boost, doppio salto, fast fall,
drop-through, schivata con i-frame, frame data degli attacchi (startup/active/recovery)
e movimento a sotto-passi anti-clipping.

### 2. `scripts/combat/combat_manager.gd` (autoload `CombatManager`)
Autorita' unica del combattimento: danno percentuale, formula del knockback
cumulativo, hitstun, hitlag, DI, scintille d'impatto, screen shake, Ring Out.

```
kb = (base_kb + kb_scaling * (percent/100) * (1 + damage/20)) * (2 / (1 + peso))
hitstun = kb * 0.00062 * moltiplicatore_mossa   (minimo 0.14 s, massimo 1.30 s)
```

Dati e componenti di contorno: `character_stats.gd`, `attack_data.gd`,
`attack_library.gd`, `hitbox.gd`, `hurtbox.gd`, `platform.gd`, `blast_zone.gd`,
`game_manager.gd`, `hud.gd`, `arena_camera.gd`, `character_visual.gd`.

---

## Trigger visivi e sprite veri

`CharacterVisual.play_state(nome)` e' l'unico punto di contatto tra logica e grafica.
Finche' non ci sono asset disegna un segnaposto colorato per stato (utilissimo in
playtest). Per passare agli sprite: aggiungere un **AnimatedSprite2D** come figlio del
nodo `Visual` con animazioni chiamate

```
idle, run, jump, fall, land, hitstun, dodge, ko,
attack_light, attack_light_up, attack_light_down,
attack_heavy, attack_heavy_up, attack_heavy_down,
attack_air, attack_air_up, attack_air_down, attack_air_heavy
```

Nessuna modifica al codice: se l'animazione esiste viene usata, altrimenti resta il
segnaposto. Il nome dell'animazione di ogni mossa e' un campo dell'`AttackData`.

---

## Precisione delle collisioni (niente clipping)

1. **Movimento a sotto-passi** (`_move_precise`): a oltre 2000 px/s un frame a 60 FPS
   vale ~35 px, abbastanza per attraversare una piattaforma spessa 24 px. La velocita'
   viene divisa in passi da massimo 7 px.
2. **Tetto di velocita'**: `MAX_SPEED` sul personaggio e `MAX_KNOCKBACK_SPEED` sul colpo.
3. **`one_way_collision_margin = 12`**: fascia di tolleranza che respinge verso l'alto
   chi si trova appena dentro una piattaforma sottile invece di lasciarlo passare.
4. **Shape rettangolare + floor snap**: nessuno scivolamento sui bordi, `is_on_floor()`
   non sfarfalla.
5. **Drop-through selettivo**: si disattiva solo il layer `one_way` per 0.24 s, mai il
   terreno solido.
6. **Modifiche differite alle collisioni**: hitbox e hurtbox usano `set_deferred` quando
   il cambio di stato arriva da dentro una query fisica.

Tutti questi punti sono verificati dai test automatici.

---

## Test automatici

`godot --headless res://tools/smoke_test.tscn` carica l'arena, **simula input reali**
e verifica 36 condizioni: atterraggio, corsa/attrito, altezza del salto, doppio salto,
atterraggio sulle piattaforme sottili, drop-through e ripristino della collisione,
fast fall, danno percentuale, hitstun, knockback cumulativo e peso, leggero vs pesante,
blocco dei comandi durante lo stordimento, i-frame della schivata, Ring Out e respawn.
Esce con codice 1 se un controllo fallisce (utile in CI).

---

## Dove mettere le mani per bilanciare

| Cosa | Dove |
|---|---|
| Peso, velocita', altezza/tempi di salto, schivata | `scripts/data/character_stats.gd` (o un `.tres` per personaggio) |
| Danno, knockback, angolo, frame data, hitbox di ogni mossa | `scripts/data/attack_library.gd` |
| Formula globale di knockback, hitstun, hitlag, DI | costanti in cima a `scripts/combat/combat_manager.gd` |
| Vite, ritardo e invulnerabilita' di respawn | inspector del nodo `GameManager` |
| Dimensione dell'arena e distanza delle blast zone | nodi `World` e `BlastZones` in `Arena.tscn` |

Per un nuovo personaggio: duplicare `CharacterStats` in un `.tres`, assegnarlo al campo
`stats` del `Fighter` e, se serve un moveset diverso, sostituire `moveset` in `_ready()`
con un altro set di `AttackData`.
