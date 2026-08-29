# =============================================================================
#  COMBAT & DAMAGE MANAGER  (autoload: "CombatManager")
# =============================================================================
#
#  Autorita' UNICA sul combattimento. Hitbox e Hurtbox sono volumi "stupidi":
#  quando si toccano chiamano questo manager, che decide danno, traiettoria,
#  stordimento e feedback. Centralizzare qui significa poter cambiare le regole
#  del gioco (o aggiungere il netcode) toccando un solo file.
#
#  ---------------------------------------------------------------------------
#  1. DANNO PERCENTUALE (stile Brawlhalla / Smash)
#  ---------------------------------------------------------------------------
#  Nessuno ha "punti vita". Ogni colpo INCREMENTA una percentuale (0% -> 300%+)
#  che rappresenta quanto il personaggio e' facile da scaraventare via.
#  Si perde uscendo dall'arena (Ring Out), non arrivando a zero HP.
#
#  ---------------------------------------------------------------------------
#  2. KNOCKBACK CUMULATIVO
#  ---------------------------------------------------------------------------
#      kb = (base_kb + kb_scaling * (percent/100) * damage_factor) * peso
#
#      base_kb       : spinta garantita anche a 0% (definisce le combo)
#      kb_scaling    : quanto il colpo "cresce" con la percentuale accumulata
#      damage_factor : 1 + damage/DAMAGE_FACTOR_DIVISOR
#                      i colpi grossi scalano piu' in fretta di quelli piccoli
#      peso          : 2 / (1 + weight) -> weight 1.0 = neutro,
#                      i pesanti volano meno, i leggeri volano di piu'
#
#  La percentuale usata e' quella DOPO aver applicato il danno del colpo
#  corrente: e' la convenzione dei platform fighter e rende gli scambi finali
#  molto piu' esplosivi.
#
#  ---------------------------------------------------------------------------
#  3. HITSTUN (stordimento)
#  ---------------------------------------------------------------------------
#  Proporzionale al knockback effettivo: chi vola lontano resta stordito a
#  lungo. E' cio' che rende possibili le combo e l'edge-guard.
#
#  ---------------------------------------------------------------------------
#  4. HITLAG (freeze frame)
#  ---------------------------------------------------------------------------
#  All'impatto ENTRAMBI i personaggi si congelano per pochi centesimi: e' il
#  trucco che da' "peso" ai colpi. Durante l'hitlag chi subisce puo' ancora
#  inclinare lo stick per la DI (vedi sotto).
#
#  ---------------------------------------------------------------------------
#  5. DI - DIRECTIONAL INFLUENCE
#  ---------------------------------------------------------------------------
#  Chi viene colpito puo' ruotare leggermente il vettore di lancio con la
#  direzione premuta: skill ceiling difensivo, presente sia in Smash che in
#  Brawlhalla. Ruota di massimo MAX_DI_ROTATION gradi, mai la potenza.
# =============================================================================
extends Node

signal hit_landed(attacker: Node, defender: Node, damage: float, knockback: Vector2)
signal fighter_ko(fighter: Node, killer: Node)
## Emesso a ogni impatto: la camera lo usa per lo screen shake.
signal impact(world_position: Vector2, intensity: float)

# --- Costanti di bilanciamento globale ------------------------------------
## Divisore del fattore di danno: piu' basso = i colpi forti scalano di piu'.
const DAMAGE_FACTOR_DIVISOR: float = 20.0
## Tetto di velocita' del knockback: evita il tunneling attraverso le
## piattaforme sottili anche a percentuali assurde (400%+).
const MAX_KNOCKBACK_SPEED: float = 2200.0
## Percentuale oltre la quale non si accumula piu' (evita overflow numerici).
const MAX_PERCENT: float = 999.0
## Secondi di hitstun per unita' di knockback.
const HITSTUN_PER_KNOCKBACK: float = 0.00062
## Stordimento minimo garantito: sotto i ~8 frame anche un jab andato a segno
## non si "sente" e non permette di concatenare nulla.
const MIN_HITSTUN: float = 0.14
const MAX_HITSTUN: float = 1.30
## Rotazione massima concessa dalla DI, in gradi.
const MAX_DI_ROTATION: float = 18.0
## Moltiplicatore di hitlag per chi attacca (di solito subisce meno freeze).
const ATTACKER_HITLAG_RATIO: float = 0.85

## Se true stampa in console il dettaglio di ogni colpo: utilissimo per tarare.
var debug_log: bool = false

# =============================================================================
#  API PRINCIPALE
# =============================================================================

## Chiamata dalla Hitbox quando un colpo va a segno.
func resolve_hit(hitbox: Hitbox, hurtbox: Hurtbox) -> void:
	var attack: AttackData = hitbox.get_attack()
	if attack == null:
		return
	var attacker: Node = hitbox.fighter
	var defender: Node = hurtbox.fighter
	if attacker == null or defender == null:
		return
	if not defender.has_method("apply_hit"):
		push_warning("Il bersaglio %s non implementa apply_hit()" % defender.name)
		return

	# 1) DANNO: la percentuale sale, i punti vita non esistono.
	var percent_before: float = defender.get_percent() if defender.has_method("get_percent") else 0.0
	var percent_after: float = minf(percent_before + attack.damage, MAX_PERCENT)

	# 2) KNOCKBACK: modulo dalla formula cumulativa, direzione dall'angolo.
	var weight: float = defender.get_weight() if defender.has_method("get_weight") else 1.0
	var magnitude: float = compute_knockback_magnitude(percent_after, attack, weight)
	var facing: int = attacker.get_facing() if attacker.has_method("get_facing") else 1
	var direction: Vector2 = attack.get_launch_vector(facing)
	# Se il bersaglio si trova dal lato opposto rispetto al vettore di lancio
	# (scambio incrociato), il vettore viene specchiato: cosi' il colpito non
	# viene mai risucchiato "dentro" chi attacca.
	var side: float = signf(hurtbox.global_position.x - hitbox.global_position.x)
	if side != 0.0 and signf(direction.x) != 0.0 and side != signf(direction.x):
		direction.x = -direction.x
	direction = _apply_directional_influence(direction, defender)
	var knockback: Vector2 = direction * minf(magnitude, MAX_KNOCKBACK_SPEED)

	# 3) HITSTUN proporzionale al knockback reale subito.
	var hitstun: float = compute_hitstun(knockback.length(), attack, defender)

	# 4) HITLAG: freeze frame su entrambi.
	var contact: Vector2 = (hitbox.global_position + hurtbox.global_position) * 0.5
	defender.apply_hit(knockback, attack.damage, hitstun, attack.hitlag, attacker, contact)
	if attacker.has_method("apply_hitlag"):
		attacker.apply_hitlag(attack.hitlag * ATTACKER_HITLAG_RATIO)

	# 5) Feedback: scintilla + shake + segnali per HUD/statistiche.
	_spawn_hit_spark(contact, attack, knockback)
	impact.emit(contact, attack.shake_intensity)
	hit_landed.emit(attacker, defender, attack.damage, knockback)

	if debug_log:
		print("[HIT] %s -> %s | %s | dmg %.1f | %.0f%% -> %.0f%% | kb %.0f px/s | stun %.2fs" % [
			attacker.name, defender.name, attack.id, attack.damage,
			percent_before, percent_after, knockback.length(), hitstun])

## Formula del knockback cumulativo. Esposta pubblicamente cosi' l'HUD o una IA
## possono chiedersi "questo colpo uccide?" senza duplicare la matematica.
func compute_knockback_magnitude(percent: float, attack: AttackData, defender_weight: float) -> float:
	var damage_factor: float = 1.0 + attack.damage / DAMAGE_FACTOR_DIVISOR
	var raw: float = attack.base_knockback + attack.knockback_scaling * (percent / 100.0) * damage_factor
	var weight_factor: float = 2.0 / (1.0 + maxf(defender_weight, 0.1))
	return clampf(raw * weight_factor, 0.0, MAX_KNOCKBACK_SPEED)

## Durata dello stordimento generata da un dato knockback.
func compute_hitstun(knockback_magnitude: float, attack: AttackData, defender: Node) -> float:
	var resistance: float = 1.0
	if defender.has_method("get_hitstun_resistance"):
		resistance = defender.get_hitstun_resistance()
	var stun: float = knockback_magnitude * HITSTUN_PER_KNOCKBACK * attack.hitstun_multiplier * resistance
	return clampf(stun, MIN_HITSTUN, MAX_HITSTUN)

## Stima della percentuale a cui un attacco diventa letale a una data distanza
## dalla blast zone. Serve all'HUD (indicatore "KO!") o a una futura IA.
func estimate_ko_percent(attack: AttackData, defender_weight: float, blast_distance: float) -> float:
	var p: int = 0
	while p < int(MAX_PERCENT):
		var kb: float = compute_knockback_magnitude(float(p), attack, defender_weight)
		# Distanza percorsa stimata: il knockback decade, ~0.45s di volo utile.
		if kb * 0.45 >= blast_distance:
			return float(p)
		p += 5
	return MAX_PERCENT

# =============================================================================
#  INTERNI
# =============================================================================

## Ruota il vettore di lancio in base alla direzione tenuta da chi subisce.
## Conta solo la componente PERPENDICOLARE al lancio: si puo' curvare la
## traiettoria, non annullarla.
func _apply_directional_influence(direction: Vector2, defender: Node) -> Vector2:
	if not defender.has_method("get_input_direction"):
		return direction
	var stick: Vector2 = defender.get_input_direction()
	if stick.length() < 0.25:
		return direction
	stick = stick.normalized()
	# Prodotto vettoriale 2D: quanto lo stick e' "di traverso" al lancio.
	var perpendicular: float = direction.x * stick.y - direction.y * stick.x
	var rotation_deg: float = clampf(perpendicular, -1.0, 1.0) * MAX_DI_ROTATION
	return direction.rotated(deg_to_rad(rotation_deg))

func _spawn_hit_spark(world_position: Vector2, attack: AttackData, knockback: Vector2) -> void:
	var tree := get_tree()
	if tree == null or tree.current_scene == null:
		return
	var spark := HitSpark.new()
	spark.configure(attack.spark_color, 0.55 if attack.is_heavy() else 0.32, knockback.angle())
	spark.global_position = world_position
	tree.current_scene.add_child(spark)

# =============================================================================
#  RING OUT
# =============================================================================

## Notifica di uscita dall'arena: la BlastZone chiama qui, il GameManager
## ascolta il segnale e gestisce vite e respawn.
func notify_ring_out(fighter: Node, killer: Node = null) -> void:
	fighter_ko.emit(fighter, killer)
