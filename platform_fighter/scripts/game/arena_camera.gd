## ArenaCamera
##
## Camera dinamica da platform fighter: inquadra il baricentro dei giocatori
## vivi e allarga/stringe lo zoom in base a quanto sono distanti, restando
## dentro i limiti dell'arena. Aggiunge lo screen shake sugli impatti.
##
## Tutto e' smorzato (lerp): una camera che segue in modo rigido rende
## illeggibili i combattimenti veloci.
class_name ArenaCamera
extends Camera2D

@export var follow_speed: float = 6.0
@export var zoom_speed: float = 4.0
## Margine attorno ai giocatori: quanto "respiro" lasciare ai bordi.
@export var padding: Vector2 = Vector2(520, 380)
@export var min_zoom: float = 0.62
@export var max_zoom: float = 1.15
## Rettangolo oltre il quale la camera non si sposta (limiti dell'arena).
@export var bounds_size: Vector2 = Vector2(2600, 1800)
@export var shake_decay: float = 9.0

var _shake: float = 0.0
var _target_position: Vector2 = Vector2.ZERO
var _target_zoom: float = 1.0

func _ready() -> void:
	make_current()
	CombatManager.impact.connect(_on_impact)
	_target_position = global_position
	_target_zoom = zoom.x

func _process(delta: float) -> void:
	var players := get_tree().get_nodes_in_group("player")
	if players.is_empty():
		return

	# Bounding box dei giocatori visibili (i KO non trascinano la camera).
	var min_p := Vector2.INF
	var max_p := -Vector2.INF
	var count := 0
	for p in players:
		if not (p is Node2D) or not (p as Node2D).visible:
			continue
		var pos: Vector2 = (p as Node2D).global_position
		min_p = min_p.min(pos)
		max_p = max_p.max(pos)
		count += 1
	if count == 0:
		return

	var center: Vector2 = (min_p + max_p) * 0.5
	var spread: Vector2 = (max_p - min_p).abs() + padding
	var viewport: Vector2 = Vector2(get_viewport_rect().size)
	# Zoom necessario per contenere entrambi gli assi.
	var needed: float = minf(viewport.x / maxf(spread.x, 1.0), viewport.y / maxf(spread.y, 1.0))
	_target_zoom = clampf(needed, min_zoom, max_zoom)

	# La camera non esce dai limiti dell'arena.
	var half: Vector2 = bounds_size * 0.5
	_target_position = Vector2(
		clampf(center.x, -half.x, half.x),
		clampf(center.y, -half.y, half.y))

	var weight: float = clampf(delta * follow_speed, 0.0, 1.0)
	global_position = global_position.lerp(_target_position, weight)
	var z: float = lerpf(zoom.x, _target_zoom, clampf(delta * zoom_speed, 0.0, 1.0))
	zoom = Vector2(z, z)

	# Screen shake: offset casuale che decade in fretta.
	if _shake > 0.0:
		_shake = maxf(_shake - shake_decay * delta, 0.0)
		offset = Vector2(randf_range(-_shake, _shake), randf_range(-_shake, _shake))
	else:
		offset = offset.lerp(Vector2.ZERO, clampf(delta * 10.0, 0.0, 1.0))

func _on_impact(_world_position: Vector2, intensity: float) -> void:
	_shake = maxf(_shake, intensity)
