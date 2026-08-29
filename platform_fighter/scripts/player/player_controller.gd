# =============================================================================
#  PLAYER CONTROLLER  -  fisica, movimento e macchina a stati del personaggio
# =============================================================================
#
#  Responsabilita': INPUT -> STATO -> VELOCITA' -> MOVIMENTO -> ANIMAZIONE.
#  Non contiene una riga di matematica del combattimento: danno, knockback e
#  hitstun arrivano gia' calcolati dal CombatManager tramite apply_hit().
#
#  ---------------------------------------------------------------------------
#  SENSAZIONE DI CONTROLLO (le scelte che fanno la differenza)
#  ---------------------------------------------------------------------------
#  * Gravita' ASIMMETRICA: piu' bassa in salita, piu' alta in discesa. E' il
#    motivo per cui il salto non e' "fluttuante".
#  * Salto VARIABILE: rilasciando il tasto la velocita' viene tagliata.
#  * COYOTE TIME: si puo' saltare per ~6 frame dopo essere usciti dal bordo.
#  * JUMP BUFFER: premere salto poco prima di atterrare non viene ignorato.
#  * TURN BOOST: invertire direzione accelera piu' del normale -> niente
#    sensazione di pattinare sul ghiaccio.
#  * FAST FALL: giu' in aria per ricadere di scatto (fondamentale nei mixup).
#  * DOPPIO SALTO + drop-through dalle piattaforme sottili.
#
#  ---------------------------------------------------------------------------
#  PRECISIONE DELLE COLLISIONI (anti-clipping)
#  ---------------------------------------------------------------------------
#  Il knockback puo' superare i 2000 px/s: a 60 FPS sono ~35 px di spostamento
#  in un singolo frame, abbastanza per ATTRAVERSARE una piattaforma spessa 24px
#  senza mai toccarla (tunneling). Per questo il movimento non e' una singola
#  move_and_slide() ma viene suddiviso in sotto-passi da pochi pixel
#  (_move_precise), con velocita' comunque limitata da MAX_SPEED.
#  In piu': shape rettangolare (nessuno scivolamento sui bordi), floor snap
#  attivo e margine di sicurezza aumentato.
# =============================================================================
class_name PlayerController
extends CharacterBody2D

# --- Segnali per HUD / GameManager / VFX ----------------------------------
signal percent_changed(percent: float)
signal state_changed(new_state: int)
signal attack_started(attack: AttackData)
signal hit_received(damage: float, percent: float, attacker: Node)
signal jumped(is_air_jump: bool)
signal landed()

## Stati del personaggio. Ogni stato decide quanta liberta' ha il giocatore.
enum State { IDLE, RUN, JUMP, FALL, LAND, ATTACK, HITSTUN, DODGE, KO, RESPAWN }
## Fasi interne di un attacco (frame data).
enum AttackPhase { NONE, STARTUP, ACTIVE, RECOVERY }

# --- Configurazione -------------------------------------------------------
@export_group("Giocatore")
## 1 o 2: determina il prefisso delle azioni di input ("p1_jump", "p2_jump"...).
@export var player_index: int = 1
## 0 = free for all. Team uguali non si colpiscono.
@export var team: int = 0
@export var player_color: Color = Color("4fc3f7")
## Se null viene creato un set di statistiche di default in _ready().
@export var stats: CharacterStats

@export_group("Debug")
## Mostra sopra il personaggio stato corrente e percentuale: il modo piu' rapido
## per capire perche' una mossa non parte durante un playtest.
@export var debug_state_label: bool = false

# --- Costanti di sicurezza fisica -----------------------------------------
## Velocita' massima assoluta (px/s): tetto anti-tunneling.
const MAX_SPEED: float = 2400.0
## Spostamento massimo per sotto-passo di collisione (px).
const SUBSTEP_MAX_DISTANCE: float = 7.0
const MAX_SUBSTEPS: int = 10
## Soglia oltre cui uno stick analogico conta come "premuto".
const INPUT_DEADZONE: float = 0.30
## Durata del blocco di atterraggio (landing lag) dopo un aereo.
const AERIAL_LANDING_LAG: float = 0.10
## Quanto resta disattivata la collisione con le piattaforme sottili.
const DROP_THROUGH_TIME: float = 0.24
## Decadimento della velocita' durante l'hitstun (attrito dell'aria).
const HITSTUN_DRAG: float = 0.985

# --- Stato runtime --------------------------------------------------------
var percent: float = 0.0
var facing: int = 1
var state: int = State.IDLE
var current_attack: AttackData = null
var attack_phase: int = AttackPhase.NONE
var moveset: Dictionary = {}

var _air_jumps_left: int = 0
var _fast_falling: bool = false
var _jump_held: bool = false
var _jump_cut_done: bool = false
var _invulnerable: bool = false

# Timer (tutti in secondi, contano alla rovescia)
var _coyote: float = 0.0
var _jump_buffer: float = 0.0
var _attack_timer: float = 0.0
var _hitstun: float = 0.0
var _hitlag: float = 0.0
var _dodge_timer: float = 0.0
var _dodge_cooldown: float = 0.0
var _drop_timer: float = 0.0
var _land_lock: float = 0.0
var _invuln_timer: float = 0.0

var _was_on_floor: bool = false
var _input := InputSnapshot.new()
var _debug_label: Label = null

@onready var visual: CharacterVisual = get_node_or_null("Visual") as CharacterVisual
@onready var hitbox: Hitbox = get_node_or_null("Hitbox") as Hitbox
@onready var hurtbox: Hurtbox = get_node_or_null("Hurtbox") as Hurtbox

## Fotografia dell'input di un frame: isola la logica dalla sorgente dei
## comandi, cosi' domani una IA o il netcode possono riempirla al posto della
## tastiera senza toccare la macchina a stati.
class InputSnapshot:
	var move: float = 0.0          # -1..1 orizzontale (analogico)
	var aim: float = 0.0           # -1..1 verticale (negativo = su)
	var jump_pressed: bool = false
	var jump_held: bool = false
	var down_held: bool = false
	var light_pressed: bool = false
	var heavy_pressed: bool = false
	var dodge_pressed: bool = false

	func direction() -> Vector2:
		return Vector2(move, aim)

	func clear() -> void:
		move = 0.0
		aim = 0.0
		jump_pressed = false
		jump_held = false
		down_held = false
		light_pressed = false
		heavy_pressed = false
		dodge_pressed = false

# =============================================================================
#  CICLO DI VITA
# =============================================================================

func _ready() -> void:
	if stats == null:
		stats = CharacterStats.new()
	moveset = AttackLibrary.default_moveset()
	add_to_group("player")

	# --- Configurazione fisica del CharacterBody2D ---
	motion_mode = CharacterBody2D.MOTION_MODE_GROUNDED
	up_direction = Vector2.UP
	floor_stop_on_slope = true
	floor_constant_speed = true
	# Snap al terreno: evita il micro-distacco che spezzerebbe is_on_floor().
	floor_snap_length = 8.0
	max_slides = 6
	# Margine di sicurezza generoso: la fisica corregge le compenetrazioni
	# prima che diventino clipping visibile.
	safe_margin = 0.1
	slide_on_ceiling = true

	collision_layer = CombatLayers.bit(CombatLayers.PLAYER)
	# I giocatori NON collidono tra loro (come in Brawlhalla/Smash): si
	# attraversano e interagiscono solo tramite hitbox/hurtbox.
	collision_mask = CombatLayers.world_mask()

	_air_jumps_left = stats.air_jumps

	if hurtbox != null:
		hurtbox.fighter = self
		hurtbox.team = team
	if hitbox != null:
		hitbox.fighter = self
		hitbox.team = team
	if visual != null:
		visual.setup(player_color)
	_set_state(State.FALL)

func _physics_process(delta: float) -> void:
	# L'input si legge SEMPRE, anche in hitlag: serve alla DI del CombatManager.
	_poll_input()

	# --- HITLAG: freeze frame condiviso all'impatto ---
	if _hitlag > 0.0:
		_hitlag -= delta
		# Micro-vibrazione: comunica l'impatto senza muovere il personaggio.
		if visual != null:
			visual.position = Vector2(randf_range(-2.0, 2.0), randf_range(-1.0, 1.0))
		return
	if visual != null:
		visual.position = Vector2.ZERO

	_tick_timers(delta)

	match state:
		State.KO, State.RESPAWN:
			velocity = Vector2.ZERO
			return
		State.HITSTUN:
			_process_hitstun(delta)
		State.DODGE:
			_process_dodge(delta)
		State.ATTACK:
			_process_attack(delta)
		_:
			_process_free(delta)

	_apply_gravity(delta)
	_move_precise(delta)
	_post_move()
	_update_visual()

# =============================================================================
#  INPUT
# =============================================================================

func _action(suffix: String) -> String:
	return "p%d_%s" % [player_index, suffix]

## Legge tastiera/gamepad del giocatore. Le azioni sono definite in
## project.godot con lo stesso layout per P1 e P2 (device 0 e device 1 per i
## controller), quindi qui non c'e' nessun "if tastiera / if joypad".
func _poll_input() -> void:
	_input.clear()
	if state == State.KO or state == State.RESPAWN:
		return
	if not InputMap.has_action(_action("jump")):
		return   # input non ancora configurato: il personaggio resta fermo
	# get_axis restituisce il valore analogico se la sorgente e' uno stick.
	_input.move = Input.get_axis(_action("left"), _action("right"))
	_input.aim = Input.get_axis(_action("up"), _action("down"))
	_input.jump_pressed = Input.is_action_just_pressed(_action("jump"))
	_input.jump_held = Input.is_action_pressed(_action("jump"))
	_input.down_held = _input.aim > INPUT_DEADZONE
	_input.light_pressed = Input.is_action_just_pressed(_action("light"))
	_input.heavy_pressed = Input.is_action_just_pressed(_action("heavy"))
	_input.dodge_pressed = Input.is_action_just_pressed(_action("dodge"))

	if _input.jump_pressed:
		_jump_buffer = stats.jump_buffer_time
	# Il "taglio" del salto scatta nell'istante del rilascio.
	if _jump_held and not _input.jump_held:
		_try_jump_cut()
	_jump_held = _input.jump_held

# =============================================================================
#  TIMER
# =============================================================================

func _tick_timers(delta: float) -> void:
	_coyote = maxf(_coyote - delta, 0.0)
	_jump_buffer = maxf(_jump_buffer - delta, 0.0)
	_dodge_cooldown = maxf(_dodge_cooldown - delta, 0.0)
	_land_lock = maxf(_land_lock - delta, 0.0)

	# Ripristino della collisione con le piattaforme sottili dopo il drop.
	if _drop_timer > 0.0:
		_drop_timer -= delta
		if _drop_timer <= 0.0:
			set_collision_mask_value(CombatLayers.ONE_WAY, true)

	# Invulnerabilita' temporanea (respawn).
	if _invuln_timer > 0.0:
		_invuln_timer -= delta
		if _invuln_timer <= 0.0:
			_set_invulnerable(false)

# =============================================================================
#  STATI LIBERI (idle / run / jump / fall / land)
# =============================================================================

func _process_free(delta: float) -> void:
	var on_floor := is_on_floor()

	# --- Attacchi ---
	if _land_lock <= 0.0:
		if _input.heavy_pressed:
			_start_attack(true)
			return
		if _input.light_pressed:
			_start_attack(false)
			return
		if _input.dodge_pressed and _dodge_cooldown <= 0.0:
			_start_dodge()
			return

	# --- Movimento orizzontale ---
	_apply_horizontal(delta, 1.0)

	# --- Drop-through: GIU' + SALTO su piattaforma sottile ---
	if on_floor and _input.down_held and _jump_buffer > 0.0 and _standing_on_one_way():
		_drop_through()
		return

	# --- Salto / doppio salto ---
	if _jump_buffer > 0.0:
		if on_floor or _coyote > 0.0:
			_do_jump(false)
		elif _air_jumps_left > 0:
			_do_jump(true)

	# --- Fast fall: GIU' in aria mentre si sta gia' scendendo ---
	if not on_floor and _input.down_held and velocity.y > -40.0:
		_fast_falling = true

	# --- Stato visivo ---
	if _land_lock > 0.0:
		_set_state(State.LAND)
	elif on_floor:
		_set_state(State.RUN if absf(velocity.x) > 20.0 else State.IDLE)
	else:
		_set_state(State.JUMP if velocity.y < 0.0 else State.FALL)

## Accelerazione/attrito orizzontale. `control` (0..1) riduce la manovrabilita'
## durante gli attacchi aerei senza duplicare il codice.
func _apply_horizontal(delta: float, control: float) -> void:
	var on_floor := is_on_floor()
	var max_speed: float = stats.max_run_speed if on_floor else stats.max_air_speed
	var accel: float = stats.run_acceleration if on_floor else stats.air_acceleration
	var friction: float = stats.ground_friction if on_floor else stats.air_friction

	if absf(_input.move) > INPUT_DEADZONE and control > 0.0:
		var target: float = _input.move * max_speed
		# Inversione di direzione: accelerazione potenziata = risposta secca.
		if signf(_input.move) != signf(velocity.x) and absf(velocity.x) > 10.0:
			accel *= stats.turn_boost
		velocity.x = move_toward(velocity.x, target, accel * control * delta)
		if _can_turn():
			facing = 1 if _input.move > 0.0 else -1
	else:
		velocity.x = move_toward(velocity.x, 0.0, friction * delta)

func _can_turn() -> bool:
	return state == State.IDLE or state == State.RUN or state == State.JUMP \
		or state == State.FALL or state == State.LAND

# =============================================================================
#  SALTO
# =============================================================================

func _do_jump(is_air_jump: bool) -> void:
	_jump_buffer = 0.0
	_coyote = 0.0
	_jump_cut_done = false
	_fast_falling = false
	if is_air_jump:
		_air_jumps_left -= 1
		velocity.y = stats.get_air_jump_velocity()
		# Il salto in aria "reindirizza": da' una spinta verso la direzione
		# premuta, cosi' il doppio salto e' anche uno strumento di recovery.
		if absf(_input.move) > INPUT_DEADZONE:
			velocity.x = _input.move * maxf(absf(velocity.x), stats.air_jump_horizontal_boost)
			facing = 1 if _input.move > 0.0 else -1
		if visual != null:
			visual.squash(Vector2(0.78, 1.24))
	else:
		velocity.y = stats.get_jump_velocity()
		if visual != null:
			visual.squash(Vector2(0.82, 1.20))
	_set_state(State.JUMP)
	jumped.emit(is_air_jump)

## Salto variabile: rilasciando il tasto in salita si taglia la velocita'.
func _try_jump_cut() -> void:
	if _jump_cut_done:
		return
	if velocity.y < 0.0 and (state == State.JUMP or state == State.ATTACK):
		velocity.y *= stats.jump_cut_factor
		_jump_cut_done = true

# =============================================================================
#  PIATTAFORME SOTTILI (drop-through)
# =============================================================================

## Vero se almeno una delle collisioni di questo frame e' con una piattaforma
## one-way: piu' affidabile di un raycast, usa i dati reali del motore.
func _standing_on_one_way() -> bool:
	for i in get_slide_collision_count():
		var collision := get_slide_collision(i)
		var collider := collision.get_collider()
		if collider is Node and (collider as Node).is_in_group("one_way_platform"):
			return true
	return false

## Attraversa la piattaforma disattivando TEMPORANEAMENTE il solo layer
## ONE_WAY: il terreno solido resta collidibile, quindi non si cade nel vuoto.
func _drop_through() -> void:
	set_collision_mask_value(CombatLayers.ONE_WAY, false)
	_drop_timer = DROP_THROUGH_TIME
	_jump_buffer = 0.0
	velocity.y = maxf(velocity.y, 60.0)   # spintarella per staccarsi subito
	_set_state(State.FALL)

# =============================================================================
#  GRAVITA' E MOVIMENTO
# =============================================================================

func _apply_gravity(delta: float) -> void:
	if is_on_floor():
		return
	var gravity: float
	if velocity.y < 0.0:
		gravity = stats.get_rise_gravity()      # salita morbida
	else:
		gravity = stats.get_fall_gravity()      # discesa secca
	var limit: float = stats.max_fall_speed
	if _fast_falling:
		gravity *= stats.fast_fall_gravity_multiplier
		limit = stats.fast_fall_speed
	# In hitstun il limite di caduta non si applica: il knockback deve poter
	# scaraventare fuori dallo schermo.
	if state == State.HITSTUN:
		velocity.y += gravity * delta
	else:
		velocity.y = minf(velocity.y + gravity * delta, limit)

## Movimento a sotto-passi: garantisce collisioni precise anche a velocita'
## estreme (vedi nota anti-clipping in testa al file).
func _move_precise(delta: float) -> void:
	velocity.x = clampf(velocity.x, -MAX_SPEED, MAX_SPEED)
	velocity.y = clampf(velocity.y, -MAX_SPEED, MAX_SPEED)

	var travel: float = velocity.length() * delta
	var steps: int = clampi(int(ceil(travel / SUBSTEP_MAX_DISTANCE)), 1, MAX_SUBSTEPS)
	if steps <= 1:
		move_and_slide()
		return
	# move_and_slide() usa il delta di fisica: dividendo la velocita' per N e
	# chiamandola N volte si percorre la stessa distanza in N passi corti.
	velocity /= float(steps)
	for i in steps:
		move_and_slide()
	velocity *= float(steps)

## Aggiornamenti che dipendono dall'esito del movimento.
func _post_move() -> void:
	var on_floor := is_on_floor()
	if on_floor:
		_coyote = stats.coyote_time
		_air_jumps_left = stats.air_jumps
		_fast_falling = false
		_jump_cut_done = false
		if not _was_on_floor:
			_on_landed()
	elif _was_on_floor:
		# Appena usciti dal bordo: parte la finestra di coyote time.
		_coyote = stats.coyote_time
	_was_on_floor = on_floor

func _on_landed() -> void:
	var impact_speed: float = absf(velocity.y)
	if visual != null:
		var squash: float = clampf(impact_speed / 900.0, 0.0, 0.45)
		visual.squash(Vector2(1.0 + squash, 1.0 - squash))
	# Un aereo che tocca terra viene annullato con landing lag: e' il costo
	# che rende gli attacchi in aria una scelta e non la risposta a tutto.
	if state == State.ATTACK and current_attack != null and current_attack.air_only:
		_end_attack()
		_land_lock = AERIAL_LANDING_LAG
	if state == State.HITSTUN and _hitstun <= 0.12:
		_hitstun = 0.0
		_set_state(State.IDLE)
	landed.emit()

# =============================================================================
#  ATTACCHI
# =============================================================================

## Compone la chiave del moveset a partire da contesto e direzione:
##   [air_]{light|heavy}_{neutral|side|up|down}
func _select_attack(is_heavy: bool) -> AttackData:
	var direction := "neutral"
	if _input.aim < -INPUT_DEADZONE:
		direction = "up"
	elif _input.aim > INPUT_DEADZONE:
		direction = "down"
	elif absf(_input.move) > INPUT_DEADZONE:
		direction = "side"
	var key := "%s%s_%s" % [
		"" if is_on_floor() else "air_",
		"heavy" if is_heavy else "light",
		direction,
	]
	return AttackLibrary.resolve(moveset, key)

func _start_attack(is_heavy: bool) -> void:
	var attack := _select_attack(is_heavy)
	if attack == null:
		return
	current_attack = attack
	attack_phase = AttackPhase.STARTUP
	_attack_timer = attack.startup
	# Ci si gira verso la direzione premuta un istante prima di partire.
	if absf(_input.move) > INPUT_DEADZONE:
		facing = 1 if _input.move > 0.0 else -1
	if attack.self_forward_impulse > 0.0 and is_on_floor():
		velocity.x = facing * attack.self_forward_impulse
	if attack.stall_in_air and not is_on_floor():
		velocity.y = minf(velocity.y, 0.0) * 0.2   # si "aggrappa" all'aria
	_set_state(State.ATTACK)
	attack_started.emit(attack)

func _process_attack(delta: float) -> void:
	# Durante l'attacco il controllo aereo e' ridotto, quello a terra nullo:
	# ogni mossa e' un impegno, non un movimento gratuito.
	if is_on_floor():
		velocity.x = move_toward(velocity.x, 0.0, stats.ground_friction * 0.6 * delta)
	else:
		_apply_horizontal(delta, stats.attack_air_control)

	_attack_timer -= delta
	if _attack_timer > 0.0:
		return

	match attack_phase:
		AttackPhase.STARTUP:
			# I frame attivi iniziano ORA: si accende la hitbox.
			attack_phase = AttackPhase.ACTIVE
			_attack_timer = current_attack.active
			if hitbox != null:
				hitbox.activate(current_attack, facing)
		AttackPhase.ACTIVE:
			attack_phase = AttackPhase.RECOVERY
			_attack_timer = current_attack.recovery
			if hitbox != null:
				hitbox.deactivate()
		AttackPhase.RECOVERY:
			_end_attack()

func _end_attack() -> void:
	if hitbox != null:
		hitbox.deactivate()
	current_attack = null
	attack_phase = AttackPhase.NONE
	_attack_timer = 0.0
	if state == State.ATTACK:
		_set_state(State.IDLE if is_on_floor() else State.FALL)

# =============================================================================
#  SCHIVATA
# =============================================================================

func _start_dodge() -> void:
	_dodge_timer = stats.dodge_duration
	_dodge_cooldown = stats.dodge_duration + stats.dodge_cooldown
	var direction := Vector2(_input.move, _input.aim)
	if is_on_floor():
		# Schivata a terra: rotolata nella direzione premuta, o sul posto.
		velocity.x = signf(direction.x) * stats.dodge_ground_speed if absf(direction.x) > INPUT_DEADZONE else 0.0
	else:
		# Air dodge direzionale: se non si preme nulla si frena in aria.
		if direction.length() > INPUT_DEADZONE:
			velocity = direction.normalized() * stats.dodge_air_speed
		else:
			velocity = Vector2.ZERO
	_set_state(State.DODGE)

func _process_dodge(delta: float) -> void:
	_dodge_timer -= delta
	var elapsed: float = stats.dodge_duration - _dodge_timer
	# Finestra di invulnerabilita': NON copre tutta la schivata, altrimenti
	# sarebbe un'opzione senza rischio.
	var should_be_invulnerable: bool = elapsed >= stats.dodge_invuln_start \
		and elapsed <= stats.dodge_invuln_end
	if should_be_invulnerable != _invulnerable and _invuln_timer <= 0.0:
		_set_invulnerable(should_be_invulnerable)
	# Frenata progressiva verso la fine della schivata.
	velocity.x = move_toward(velocity.x, 0.0, stats.ground_friction * 0.5 * delta)
	if _dodge_timer <= 0.0:
		if _invuln_timer <= 0.0:
			_set_invulnerable(false)
		_set_state(State.IDLE if is_on_floor() else State.FALL)

# =============================================================================
#  DANNO SUBITO  (API chiamata dal CombatManager)
# =============================================================================

## Applica un colpo gia' RISOLTO dal CombatManager: qui non si calcola nulla,
## si subisce e basta. Cosi' la stessa funzione va bene per il gioco locale,
## per una IA o per un pacchetto di rete.
func apply_hit(knockback: Vector2, damage: float, hitstun: float, hitlag: float,
		attacker: Node, contact_point: Vector2) -> void:
	if _invulnerable or state == State.KO or state == State.RESPAWN:
		return
	percent = minf(percent + damage, CombatManager.MAX_PERCENT)
	percent_changed.emit(percent)

	# Il colpo interrompe qualunque cosa si stesse facendo.
	if state == State.ATTACK:
		_end_attack()
	if state == State.DODGE:
		_dodge_timer = 0.0
		_set_invulnerable(false)

	velocity = knockback
	_hitstun = hitstun
	_hitlag = hitlag
	_fast_falling = false
	# Ci si gira verso chi ha colpito: leggibilita' immediata dello scambio.
	if attacker is Node2D:
		facing = 1 if (attacker as Node2D).global_position.x > global_position.x else -1
	# Il knockback ricarica i salti: si ha sempre una chance di recovery.
	_air_jumps_left = stats.air_jumps
	_set_state(State.HITSTUN)
	if visual != null:
		visual.flash(0.16)
		visual.squash(Vector2(1.25, 0.75))
	hit_received.emit(damage, percent, attacker)

## Freeze frame applicato anche a chi colpisce.
func apply_hitlag(duration: float) -> void:
	_hitlag = maxf(_hitlag, duration)

func _process_hitstun(delta: float) -> void:
	_hitstun -= delta
	# Attrito leggero: il volo decade in modo naturale senza sembrare frenato.
	velocity.x *= HITSTUN_DRAG
	if _hitstun <= 0.0:
		_set_state(State.IDLE if is_on_floor() else State.FALL)

# =============================================================================
#  RING OUT / RESPAWN  (API chiamata dal GameManager)
# =============================================================================

func enter_ko() -> void:
	_set_state(State.KO)
	velocity = Vector2.ZERO
	current_attack = null
	attack_phase = AttackPhase.NONE
	if hitbox != null:
		hitbox.deactivate()
	_set_invulnerable(true)
	visible = false
	set_physics_process(true)   # resta vivo per gestire il respawn

func respawn(spawn_position: Vector2, invulnerability: float = 2.0) -> void:
	global_position = spawn_position
	velocity = Vector2.ZERO
	percent = 0.0
	percent_changed.emit(percent)
	_hitstun = 0.0
	_hitlag = 0.0
	_fast_falling = false
	_air_jumps_left = stats.air_jumps
	_drop_timer = 0.0
	set_collision_mask_value(CombatLayers.ONE_WAY, true)
	visible = true
	_invuln_timer = invulnerability
	_set_invulnerable(true)
	_set_state(State.FALL)

func _set_invulnerable(value: bool) -> void:
	_invulnerable = value
	if hurtbox != null:
		hurtbox.set_vulnerable(not value)
	if visual != null:
		visual.set_invulnerable(value)

# =============================================================================
#  API PUBBLICA (letta da CombatManager, HUD, GameManager)
# =============================================================================

func get_percent() -> float:
	return percent

func get_weight() -> float:
	return stats.weight

func get_facing() -> int:
	return facing

func get_hitstun_resistance() -> float:
	return stats.hitstun_resistance

## Direzione attualmente premuta: usata per la DI durante l'hitlag.
func get_input_direction() -> Vector2:
	return _input.direction()

func is_invulnerable() -> bool:
	return _invulnerable

func get_state_name() -> String:
	return State.keys()[state]

# =============================================================================
#  STATO E PRESENTAZIONE
# =============================================================================

func _set_state(new_state: int) -> void:
	if state == new_state:
		return
	state = new_state
	state_changed.emit(new_state)

## TRIGGER VISIVI: unico punto in cui lo stato logico diventa animazione.
## Aggiungere uno sprite reale significa solo creare l'animazione con questo
## nome dentro l'AnimatedSprite2D del nodo Visual.
func _update_visual() -> void:
	if visual == null:
		return
	visual.set_facing(facing)
	var anim := "idle"
	match state:
		State.IDLE:
			anim = "idle"
		State.RUN:
			anim = "run"
		State.JUMP:
			anim = "jump"
		State.FALL:
			anim = "fall"
		State.LAND:
			anim = "land"
		State.ATTACK:
			anim = current_attack.animation if current_attack != null else "attack_light"
		State.HITSTUN:
			anim = "hitstun"
		State.DODGE:
			anim = "dodge"
		State.KO, State.RESPAWN:
			anim = "ko"
	visual.play_state(anim)
	_update_debug_label()

## Etichetta di debug creata solo se richiesta (nessun costo se disattivata).
func _update_debug_label() -> void:
	if not debug_state_label:
		if _debug_label != null:
			_debug_label.visible = false
		return
	if _debug_label == null:
		_debug_label = Label.new()
		_debug_label.name = "DebugLabel"
		_debug_label.position = Vector2(-46, -104)
		_debug_label.add_theme_font_size_override("font_size", 13)
		add_child(_debug_label)
	_debug_label.visible = true
	_debug_label.text = "%s  %d%%" % [get_state_name(), int(percent)]
