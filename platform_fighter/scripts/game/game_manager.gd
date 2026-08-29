## GameManager
##
## Regia del match: vite (stock), Ring Out, respawn, condizione di vittoria.
## Non conosce la fisica ne' la matematica dei colpi: reagisce ai segnali del
## CombatManager e comanda i PlayerController tramite la loro API pubblica.
class_name GameManager
extends Node

signal stocks_changed(player: Node, stocks: int)
signal match_ended(winner: Node)
signal player_respawned(player: Node)

@export var stocks_per_player: int = 3
@export var respawn_delay: float = 1.1
@export var respawn_invulnerability: float = 2.0
## Se true, R riavvia il match a fine partita.
@export var allow_restart: bool = true

var players: Array[Node] = []
var stocks: Dictionary = {}          # instance_id -> vite rimaste
var _last_attacker: Dictionary = {}  # instance_id vittima -> chi l'ha colpita
var _spawn_points: Array[Node2D] = []
var _match_over: bool = false

func _ready() -> void:
	add_to_group("game_manager")
	CombatManager.fighter_ko.connect(_on_fighter_ko)
	CombatManager.hit_landed.connect(_on_hit_landed)
	# I giocatori si registrano nel gruppo in _ready: si aspetta un frame per
	# essere certi che l'intera scena sia costruita.
	call_deferred("_collect_scene")

func _collect_scene() -> void:
	players.clear()
	for node in get_tree().get_nodes_in_group("player"):
		players.append(node)
	players.sort_custom(func(a, b): return a.player_index < b.player_index)
	for node in get_tree().get_nodes_in_group("spawn_point"):
		if node is Node2D:
			_spawn_points.append(node)
	for p in players:
		stocks[p.get_instance_id()] = stocks_per_player
		stocks_changed.emit(p, stocks_per_player)

func _unhandled_input(event: InputEvent) -> void:
	# La guardia su InputMap evita l'errore se il progetto viene aperto prima
	# di aver generato le azioni con tools/build_project.tscn.
	if not InputMap.has_action("restart_match"):
		return
	if allow_restart and event.is_action_pressed("restart_match"):
		get_tree().reload_current_scene()

## Memorizza l'ultimo aggressore: serve ad attribuire il KO anche quando la
## vittima esce dall'arena qualche istante dopo il colpo.
func _on_hit_landed(attacker: Node, defender: Node, _damage: float, _kb: Vector2) -> void:
	_last_attacker[defender.get_instance_id()] = attacker

func _on_fighter_ko(fighter: Node, killer: Node) -> void:
	if _match_over or not stocks.has(fighter.get_instance_id()):
		return
	var id: int = fighter.get_instance_id()
	stocks[id] = maxi(stocks[id] - 1, 0)
	stocks_changed.emit(fighter, stocks[id])
	var credited: Node = killer if killer != null else _last_attacker.get(id, null)
	if credited != null:
		print("KO! %s eliminato da %s (vite rimaste: %d)" % [fighter.name, credited.name, stocks[id]])
	else:
		print("KO! %s si e' auto-eliminato (vite rimaste: %d)" % [fighter.name, stocks[id]])

	fighter.enter_ko()
	if stocks[id] <= 0:
		_check_match_end()
		return
	_respawn_after_delay(fighter)

func _respawn_after_delay(fighter: Node) -> void:
	await get_tree().create_timer(respawn_delay).timeout
	if not is_instance_valid(fighter) or _match_over:
		return
	fighter.respawn(_pick_spawn_point(fighter), respawn_invulnerability)
	player_respawned.emit(fighter)

## Sceglie il punto di respawn piu' lontano dagli avversari vivi: evita di
## rinascere dentro un attacco caricato.
func _pick_spawn_point(fighter: Node) -> Vector2:
	if _spawn_points.is_empty():
		return Vector2(0, -320)
	var best: Vector2 = _spawn_points[0].global_position
	var best_distance: float = -1.0
	for point in _spawn_points:
		var nearest: float = INF
		for other in players:
			if other == fighter or not other.visible:
				continue
			nearest = minf(nearest, point.global_position.distance_to(other.global_position))
		if nearest == INF:
			nearest = 99999.0
		if nearest > best_distance:
			best_distance = nearest
			best = point.global_position
	return best

func _check_match_end() -> void:
	var alive: Array[Node] = []
	for p in players:
		if stocks.get(p.get_instance_id(), 0) > 0:
			alive.append(p)
	if alive.size() <= 1:
		_match_over = true
		var winner: Node = alive[0] if alive.size() == 1 else null
		match_ended.emit(winner)
		print("MATCH FINITO. Vincitore: %s" % (winner.name if winner != null else "nessuno"))

func get_stocks(player: Node) -> int:
	return stocks.get(player.get_instance_id(), 0)

func is_match_over() -> bool:
	return _match_over
