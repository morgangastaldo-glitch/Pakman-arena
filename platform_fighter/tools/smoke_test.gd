# Banco di prova automatico: carica l'arena, simula input reali e verifica
# fisica, collisioni e combattimento.  Uso:
#   godot --headless res://tools/smoke_test.tscn
extends Node

var arena: Node
var p1: PlayerController
var p2: PlayerController
var failures: int = 0
var checks: int = 0

const GROUND_Y := 152.0      # superficie della piattaforma principale
const TOP_PLATFORM_Y := -162.0
## Colonna d'aria libera da piattaforme sottili: i test di caduta devono
## poter arrivare fino al suolo senza appoggiarsi ai bordi laterali.
const CLEAR_X := 170.0

func _ready() -> void:
	arena = load("res://scenes/Arena.tscn").instantiate()
	add_child(arena)
	await get_tree().process_frame
	await get_tree().process_frame
	p1 = arena.get_node("Fighters/Player1")
	p2 = arena.get_node("Fighters/Player2")
	await _run_all()
	print("\n===== RISULTATO: %d/%d controlli superati =====" % [checks - failures, checks])
	get_tree().quit(1 if failures > 0 else 0)

func frames(n: int) -> void:
	for i in n:
		await get_tree().physics_frame

func check(label: String, condition: bool, detail: String = "") -> void:
	checks += 1
	if condition:
		print("  [OK]   %s %s" % [label, detail])
	else:
		failures += 1
		print("  [FAIL] %s %s" % [label, detail])

func press(action: String) -> void:
	Input.action_press(action)

func release(action: String) -> void:
	Input.action_release(action)

func tap(action: String, hold_frames: int = 3) -> void:
	press(action)
	await frames(hold_frames)
	release(action)

func park(player: PlayerController, pos: Vector2) -> void:
	player.global_position = pos
	player.velocity = Vector2.ZERO
	await frames(2)

func _run_all() -> void:
	await _test_ground_collision()
	await _test_run_and_friction()
	await _test_jump_height()
	await _test_double_jump()
	await _test_one_way_landing()
	await _test_drop_through()
	await _test_fast_fall()
	await _test_light_attack()
	await _test_knockback_scaling()
	await _test_heavy_vs_light()
	await _test_hitstun_lock()
	await _test_dodge_invulnerability()
	await _test_ring_out_and_respawn()

# --- FISICA ---------------------------------------------------------------

func _test_ground_collision() -> void:
	print("\n-- Collisione col terreno --")
	await park(p1, Vector2(CLEAR_X, -100))
	await frames(60)
	check("il personaggio atterra e resta sulla piattaforma",
		p1.is_on_floor() and absf(p1.global_position.y - GROUND_Y) < 1.0,
		"y=%.2f (atteso %.0f)" % [p1.global_position.y, GROUND_Y])

func _test_run_and_friction() -> void:
	print("\n-- Corsa, accelerazione e attrito --")
	await park(p1, Vector2(CLEAR_X, GROUND_Y))
	var start_x := p1.global_position.x
	press("p1_right")
	await frames(30)
	var speed := p1.velocity.x
	release("p1_right")
	check("accelera fino alla velocita' massima", absf(speed - p1.stats.max_run_speed) < 12.0,
		"v=%.1f (max %.1f)" % [speed, p1.stats.max_run_speed])
	check("si sposta a destra", p1.global_position.x > start_x + 100.0,
		"dx=%.1f" % (p1.global_position.x - start_x))
	check("guarda a destra", p1.get_facing() == 1)
	await frames(12)
	check("l'attrito lo ferma in fretta", absf(p1.velocity.x) < 20.0, "v=%.1f" % p1.velocity.x)

func _test_jump_height() -> void:
	print("\n-- Salto (altezza e responsivita') --")
	await park(p1, Vector2(CLEAR_X, GROUND_Y))
	await frames(5)
	press("p1_jump")
	var apex := p1.global_position.y
	for i in 60:
		await get_tree().physics_frame
		apex = minf(apex, p1.global_position.y)
		if p1.velocity.y > 0.0 and i > 5:
			break
	release("p1_jump")
	var height := GROUND_Y - apex
	check("l'altezza del salto rispetta jump_height", absf(height - p1.stats.jump_height) < 22.0,
		"h=%.1f (atteso %.0f)" % [height, p1.stats.jump_height])
	check("la gravita' di discesa e' maggiore di quella di salita",
		p1.stats.get_fall_gravity() > p1.stats.get_rise_gravity(),
		"salita=%.0f discesa=%.0f" % [p1.stats.get_rise_gravity(), p1.stats.get_fall_gravity()])
	await frames(60)

func _test_double_jump() -> void:
	print("\n-- Doppio salto --")
	await park(p1, Vector2(CLEAR_X, GROUND_Y))
	await frames(5)
	await tap("p1_jump")
	await frames(10)
	var before := p1._air_jumps_left
	await tap("p1_jump")
	await frames(2)
	check("il salto in aria e' disponibile e viene consumato",
		before == 1 and p1._air_jumps_left == 0)
	check("il doppio salto spinge di nuovo verso l'alto", p1.velocity.y < -100.0,
		"vy=%.1f" % p1.velocity.y)
	await frames(70)
	check("i salti si ricaricano all'atterraggio",
		p1.is_on_floor() and p1._air_jumps_left == p1.stats.air_jumps)

func _test_one_way_landing() -> void:
	print("\n-- Piattaforma sottile: atterraggio dall'alto (anti-clipping) --")
	await park(p1, Vector2(0, TOP_PLATFORM_Y - 220.0))
	await frames(70)
	check("atterra sulla piattaforma sottile senza attraversarla",
		p1.is_on_floor() and absf(p1.global_position.y - TOP_PLATFORM_Y) < 1.5,
		"y=%.2f (attesa %.0f)" % [p1.global_position.y, TOP_PLATFORM_Y])

func _test_drop_through() -> void:
	print("\n-- Piattaforma sottile: discesa con GIU' + SALTO --")
	# (si parte dal test precedente: p1 e' sulla piattaforma alta)
	press("p1_down")
	await frames(2)
	await tap("p1_jump", 2)
	await frames(20)
	release("p1_down")
	check("attraversa la piattaforma scendendo",
		p1.global_position.y > TOP_PLATFORM_Y + 30.0,
		"y=%.1f" % p1.global_position.y)
	await frames(90)
	check("ricade sulla piattaforma principale e la collisione torna attiva",
		p1.is_on_floor() and absf(p1.global_position.y - GROUND_Y) < 1.5,
		"y=%.2f" % p1.global_position.y)
	check("la maschera di collisione one-way e' stata ripristinata",
		p1.get_collision_mask_value(CombatLayers.ONE_WAY))

func _test_fast_fall() -> void:
	print("\n-- Fast fall --")
	await park(p1, Vector2(CLEAR_X, -300))
	await frames(20)
	var normal_speed := p1.velocity.y
	await park(p1, Vector2(CLEAR_X, -300))
	press("p1_down")
	await frames(20)
	var fast_speed := p1.velocity.y
	release("p1_down")
	check("il fast fall accelera la discesa", fast_speed > normal_speed * 1.4,
		"normale=%.0f fast=%.0f" % [normal_speed, fast_speed])
	await frames(60)

# --- COMBATTIMENTO --------------------------------------------------------

func _setup_duel(distance: float = 52.0) -> void:
	await park(p1, Vector2(0, GROUND_Y))
	await park(p2, Vector2(distance, GROUND_Y))
	p2.percent = 0.0
	p2._hitstun = 0.0
	p2._hitlag = 0.0
	p2._set_state(PlayerController.State.IDLE)
	await frames(4)

func _test_light_attack() -> void:
	print("\n-- Attacco leggero: danno percentuale e stordimento --")
	await _setup_duel()
	press("p1_right")   # side light
	await frames(2)
	await tap("p1_light", 2)
	release("p1_right")
	# Si attende l'istante esatto del colpo: l'hitstun di un leggero dura
	# pochi frame, campionarlo tardi misurerebbe un bersaglio gia' libero.
	for i in 40:
		await get_tree().physics_frame
		if p2.percent > 0.0:
			break
	await frames(2)
	check("la percentuale del bersaglio aumenta (niente punti vita)", p2.percent > 0.0,
		"%.0f%%" % p2.percent)
	check("il bersaglio entra in hitstun", p2.state == PlayerController.State.HITSTUN,
		"stato=%s" % p2.get_state_name())
	check("il bersaglio viene respinto", p2.velocity.length() > 50.0,
		"kb=%.0f px/s" % p2.velocity.length())
	check("chi attacca non subisce danno", p1.percent == 0.0)
	await frames(60)

func _test_knockback_scaling() -> void:
	print("\n-- Knockback cumulativo (cresce con la percentuale) --")
	# Confronto diretto sulla formula, senza rumore di fisica.
	var attack: AttackData = AttackLibrary.resolve(p1.moveset, "heavy_side")
	var kb_0 := CombatManager.compute_knockback_magnitude(0.0, attack, 1.0)
	var kb_100 := CombatManager.compute_knockback_magnitude(100.0, attack, 1.0)
	var kb_200 := CombatManager.compute_knockback_magnitude(200.0, attack, 1.0)
	check("a 100%% respinge molto piu' che a 0%%", kb_100 > kb_0 * 2.5,
		"%.0f -> %.0f px/s" % [kb_0, kb_100])
	check("la crescita continua oltre il 100%%", kb_200 > kb_100 * 1.5,
		"200%% = %.0f px/s" % kb_200)
	var heavy_char := CombatManager.compute_knockback_magnitude(100.0, attack, 1.8)
	var light_char := CombatManager.compute_knockback_magnitude(100.0, attack, 0.7)
	check("il peso del personaggio conta", heavy_char < kb_100 and light_char > kb_100,
		"pesante=%.0f medio=%.0f leggero=%.0f" % [heavy_char, kb_100, light_char])
	var stun_low := CombatManager.compute_hitstun(kb_0, attack, p2)
	var stun_high := CombatManager.compute_hitstun(kb_200, attack, p2)
	check("l'hitstun cresce col knockback", stun_high > stun_low,
		"%.2fs -> %.2fs" % [stun_low, stun_high])

func _test_heavy_vs_light() -> void:
	print("\n-- Leggero vs Pesante (danno e respingimento reali) --")
	await _setup_duel()
	press("p1_right")
	await frames(2)
	await tap("p1_light", 2)
	release("p1_right")
	for i in 40:
		await get_tree().physics_frame
		if p2.percent > 0.0:
			break
	await frames(2)
	var light_damage := p2.percent
	var light_kb := p2.velocity.length()
	await frames(70)

	await _setup_duel()
	press("p1_right")
	await frames(2)
	await tap("p1_heavy", 2)
	release("p1_right")
	for i in 60:
		await get_tree().physics_frame
		if p2.percent > 0.0:
			break
	await frames(2)
	var heavy_damage := p2.percent
	var heavy_kb := p2.velocity.length()
	check("il pesante fa piu' danno del leggero", heavy_damage > light_damage,
		"leggero=%.0f%% pesante=%.0f%%" % [light_damage, heavy_damage])
	check("il pesante respinge molto di piu'", heavy_kb > light_kb * 1.5,
		"leggero=%.0f pesante=%.0f px/s" % [light_kb, heavy_kb])
	await frames(70)

func _test_hitstun_lock() -> void:
	print("\n-- Lo stordito non risponde ai comandi --")
	await _setup_duel()
	press("p1_right")
	await frames(2)
	await tap("p1_light", 2)
	release("p1_right")
	for i in 40:
		await get_tree().physics_frame
		if p2.percent > 0.0:
			break
	check("il bersaglio e' stordito", p2.state == PlayerController.State.HITSTUN)
	# P2 tenta di muoversi mentre e' stordito.
	var vx_before := p2.velocity.x
	press("p2_left")
	await frames(4)
	release("p2_left")
	check("l'input non altera la traiettoria durante l'hitstun",
		signf(p2.velocity.x) == signf(vx_before),
		"vx %.0f -> %.0f" % [vx_before, p2.velocity.x])
	await frames(80)

func _test_dodge_invulnerability() -> void:
	print("\n-- Schivata: frame di invulnerabilita' --")
	await _setup_duel()
	# P2 schiva mentre P1 attacca: il colpo deve passare a vuoto.
	await tap("p2_dodge", 2)
	await frames(3)
	press("p1_right")
	await frames(1)
	await tap("p1_light", 2)
	release("p1_right")
	await frames(10)
	check("il colpo non tocca chi sta schivando", p2.percent == 0.0,
		"%.0f%%" % p2.percent)
	check("la hurtbox e' disattivata durante gli i-frame",
		not p2.get_node("Hurtbox").is_vulnerable() or p2.state != PlayerController.State.DODGE)
	# Finita la schivata la vulnerabilita' deve tornare.
	await frames(40)
	check("dopo la schivata si torna colpibili",
		p2.get_node("Hurtbox").is_vulnerable() and not p2.is_invulnerable())
	await _setup_duel()
	press("p1_right")
	await frames(2)
	await tap("p1_light", 2)
	release("p1_right")
	for i in 40:
		await get_tree().physics_frame
		if p2.percent > 0.0:
			break
	check("lo stesso colpo va a segno a schivata finita", p2.percent > 0.0,
		"%.0f%%" % p2.percent)
	await frames(60)

func _test_ring_out_and_respawn() -> void:
	print("\n-- Ring Out e respawn --")
	var manager := arena.get_node("GameManager") as GameManager
	var stocks_before := manager.get_stocks(p2)
	p2.global_position = Vector2(1250, 0)   # dentro la blast zone destra
	await frames(6)
	check("uscire dall'arena costa una vita", manager.get_stocks(p2) == stocks_before - 1,
		"%d -> %d" % [stocks_before, manager.get_stocks(p2)])
	check("il personaggio eliminato sparisce", not p2.visible)
	await frames(100)
	check("respawn dentro l'arena", p2.visible and absf(p2.global_position.x) < 600.0,
		"pos=%s" % str(p2.global_position.round()))
	check("la percentuale si azzera al respawn", p2.percent == 0.0)
	check("respawn invulnerabile", p2.is_invulnerable())
