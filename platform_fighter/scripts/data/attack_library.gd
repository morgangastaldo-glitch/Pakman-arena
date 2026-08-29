## AttackLibrary
##
## Moveset di default costruito da codice (comodo per un prototipo: si tara
## tutto in un file solo). In produzione questi valori diventano file .tres
## assegnati al personaggio dall'inspector: la logica non cambia.
##
## CHIAVI DEL MOVESET
##   [air_]{light|heavy}_{neutral|side|up|down}
## Il PlayerController compone la chiave da: suolo/aria + tasto + direzione.
## Se una chiave non esiste si applica un fallback (vedi resolve()).
##
## FILOSOFIA DI BILANCIAMENTO
##   LIGHT: 4-8% di danno, startup <= 0.10s, knockback che NON uccide da solo.
##          Serve a costruire la percentuale del bersaglio.
##   HEAVY: 12-16% di danno, startup >= 0.20s, knockback che a ~100% uccide.
##          Punibile se whiffato: e' il rischio/ricompensa del match.
class_name AttackLibrary
extends RefCounted

static func _make(
		id: String,
		type: AttackData.Type,
		damage: float,
		base_kb: float,
		kb_scaling: float,
		angle: float,
		startup: float,
		active: float,
		recovery: float,
		offset: Vector2,
		size: Vector2,
		hitlag: float,
		animation: String
	) -> AttackData:
	var a := AttackData.new()
	a.id = id
	a.type = type
	a.damage = damage
	a.base_knockback = base_kb
	a.knockback_scaling = kb_scaling
	a.launch_angle = angle
	a.startup = startup
	a.active = active
	a.recovery = recovery
	a.hitbox_offset = offset
	a.hitbox_size = size
	a.hitlag = hitlag
	a.animation = animation
	a.shake_intensity = 3.0 if type == AttackData.Type.LIGHT else 9.0
	a.hitstun_multiplier = 1.0 if type == AttackData.Type.LIGHT else 1.15
	return a

## Costruisce il moveset completo. Ritorna Dictionary[String, AttackData].
static func default_moveset() -> Dictionary:
	var m: Dictionary = {}
	var L := AttackData.Type.LIGHT
	var H := AttackData.Type.HEAVY

	# --- ATTACCHI LEGGERI A TERRA ---------------------------------------
	# Jab: rapidissimo, quasi nessun knockback, apre le combo.
	m["light_neutral"] = _make("light_neutral", L, 4.0, 110.0, 210.0, 42.0,
		0.05, 0.05, 0.12, Vector2(34, -4), Vector2(46, 40), 0.035, "attack_light")
	# Side light: il "poke" principale, buona portata.
	m["light_side"] = _make("light_side", L, 6.0, 150.0, 300.0, 32.0,
		0.08, 0.06, 0.17, Vector2(44, -2), Vector2(60, 42), 0.045, "attack_light")
	# Up light: anti-aereo, lancia in alto per continuare la pressione.
	m["light_up"] = _make("light_up", L, 5.0, 160.0, 320.0, 78.0,
		0.07, 0.07, 0.18, Vector2(10, -46), Vector2(56, 56), 0.045, "attack_light_up")
	# Down light: spazzata bassa, angolo piatto, spinge lontano dal bordo.
	m["light_down"] = _make("light_down", L, 6.0, 140.0, 290.0, 14.0,
		0.09, 0.06, 0.20, Vector2(40, 18), Vector2(62, 30), 0.045, "attack_light_down")

	# --- ATTACCHI PESANTI / SMASH A TERRA -------------------------------
	# Side heavy: la mossa da KO orizzontale. Startup lungo = leggibile.
	var hs := _make("heavy_side", H, 14.0, 330.0, 640.0, 36.0,
		0.22, 0.09, 0.34, Vector2(56, -2), Vector2(78, 54), 0.10, "attack_heavy")
	hs.self_forward_impulse = 190.0  # affondo in avanti: copre spazio
	m["heavy_side"] = hs
	# Up heavy: KO verticale, ottimo su chi ricade dalle piattaforme.
	m["heavy_up"] = _make("heavy_up", H, 15.0, 350.0, 660.0, 82.0,
		0.24, 0.09, 0.36, Vector2(12, -56), Vector2(66, 74), 0.10, "attack_heavy_up")
	# Down heavy: colpisce entrambi i lati concettualmente, angolo basso.
	m["heavy_down"] = _make("heavy_down", H, 13.0, 300.0, 600.0, 18.0,
		0.20, 0.10, 0.38, Vector2(46, 20), Vector2(86, 34), 0.10, "attack_heavy_down")
	# Neutral heavy: se non si preme direzione si usa comunque il side.
	m["heavy_neutral"] = m["heavy_side"]

	# --- AEREI -----------------------------------------------------------
	# Nair: copertura tutt'intorno, poco impegnativo.
	var nair := _make("air_light_neutral", L, 5.0, 130.0, 250.0, 45.0,
		0.06, 0.10, 0.16, Vector2(0, 0), Vector2(78, 70), 0.04, "attack_air")
	nair.air_only = true
	m["air_light_neutral"] = nair
	# Fair: il colpo di edge-guard per eccellenza.
	var fair := _make("air_light_side", L, 7.0, 160.0, 330.0, 38.0,
		0.08, 0.08, 0.20, Vector2(44, 0), Vector2(64, 52), 0.05, "attack_air")
	fair.air_only = true
	m["air_light_side"] = fair
	# Uair: juggling verso l'alto.
	var uair := _make("air_light_up", L, 6.0, 150.0, 340.0, 85.0,
		0.07, 0.07, 0.18, Vector2(6, -44), Vector2(60, 56), 0.05, "attack_air_up")
	uair.air_only = true
	m["air_light_up"] = uair
	# Dair: SPIKE. Angolo negativo = scaraventa verso il basso (Ring Out sotto).
	var dair := _make("air_light_down", L, 8.0, 210.0, 300.0, -74.0,
		0.11, 0.07, 0.26, Vector2(8, 42), Vector2(56, 52), 0.08, "attack_air_down")
	dair.air_only = true
	m["air_light_down"] = dair
	# Aereo pesante: lento ma con knockback da uccisione anche fuori dal ring.
	var ahs := _make("air_heavy_side", H, 12.0, 290.0, 560.0, 44.0,
		0.19, 0.09, 0.32, Vector2(50, -2), Vector2(72, 58), 0.09, "attack_air_heavy")
	ahs.air_only = true
	ahs.stall_in_air = true  # si "aggrappa" un istante all'aria: piu' leggibile
	m["air_heavy_side"] = ahs
	m["air_heavy_neutral"] = ahs
	m["air_heavy_up"] = _make("air_heavy_up", H, 12.0, 300.0, 590.0, 84.0,
		0.20, 0.08, 0.34, Vector2(8, -52), Vector2(64, 66), 0.09, "attack_air_heavy")
	m["air_heavy_up"].air_only = true
	m["air_heavy_down"] = _make("air_heavy_down", H, 13.0, 250.0, 430.0, -70.0,
		0.22, 0.09, 0.40, Vector2(8, 46), Vector2(60, 58), 0.10, "attack_air_heavy")
	m["air_heavy_down"].air_only = true
	return m

## Risolve la chiave richiesta con una catena di fallback, cosi' un moveset
## incompleto non genera mai un errore a runtime:
##   air_heavy_up -> heavy_up -> heavy_side -> light_neutral
static func resolve(moveset: Dictionary, key: String) -> AttackData:
	if moveset.has(key):
		return moveset[key]
	if key.begins_with("air_"):
		var grounded: String = key.substr(4)
		if moveset.has(grounded):
			return moveset[grounded]
	if key.contains("heavy") and moveset.has("heavy_side"):
		return moveset["heavy_side"]
	if moveset.has("light_neutral"):
		return moveset["light_neutral"]
	return null
