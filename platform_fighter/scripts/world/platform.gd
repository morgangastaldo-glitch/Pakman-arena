## FightPlatform
##
## Piattaforma parametrica: costruisce da sola shape e grafica a partire da
## `size`, cosi' l'arena si compone in pochi secondi senza disegnare collider a
## mano (e senza il classico disallineamento shape/sprite).
##
## DUE TIPI
##  * SOLIDA (one_way = false): il "suolo" principale, collide da ogni lato.
##  * SOTTILE (one_way = true): si attraversa saltandoci dentro dal basso e si
##    scende con GIU' + SALTO. Va nel layer ONE_WAY e nel gruppo
##    "one_way_platform", che e' cio' che il PlayerController interroga.
##
## ANTI-CLIPPING
## `one_way_collision_margin` crea una fascia di tolleranza: se un personaggio
## in caduta veloce finisce leggermente dentro la piattaforma, viene comunque
## respinto in alto invece di attraversarla.
@tool
class_name FightPlatform
extends StaticBody2D

@export var size: Vector2 = Vector2(640, 48):
	set(value):
		size = value
		_rebuild()
@export var one_way: bool = false:
	set(value):
		one_way = value
		_rebuild()
@export var color: Color = Color("3c4a63"):
	set(value):
		color = value
		queue_redraw()
## Spessore della fascia di tolleranza per la collisione one-way.
@export var one_way_margin: float = 12.0:
	set(value):
		one_way_margin = value
		_rebuild()

var _shape_node: CollisionShape2D
var _rect: RectangleShape2D

func _ready() -> void:
	_rebuild()

func _rebuild() -> void:
	if not is_inside_tree():
		return
	if _rect == null:
		_rect = RectangleShape2D.new()
	_rect.size = size
	if _shape_node == null:
		_shape_node = get_node_or_null("CollisionShape2D") as CollisionShape2D
	if _shape_node == null:
		_shape_node = CollisionShape2D.new()
		_shape_node.name = "CollisionShape2D"
		add_child(_shape_node)
	_shape_node.shape = _rect
	_shape_node.one_way_collision = one_way
	_shape_node.one_way_collision_margin = one_way_margin

	collision_layer = CombatLayers.bit(CombatLayers.ONE_WAY if one_way else CombatLayers.SOLID)
	collision_mask = 0   # una piattaforma non cerca nessuno
	if one_way:
		add_to_group("one_way_platform")
	elif is_in_group("one_way_platform"):
		remove_from_group("one_way_platform")
	queue_redraw()

func _draw() -> void:
	var rect := Rect2(-size * 0.5, size)
	draw_rect(rect, color, true)
	# Bordo superiore chiaro: comunica al volo "qui si puo' stare in piedi".
	draw_rect(Rect2(rect.position, Vector2(size.x, 5)), color.lightened(0.45), true)
	if one_way:
		# Tratteggio inferiore = segnale visivo di "attraversabile dal basso".
		var y: float = rect.position.y + size.y
		var x: float = rect.position.x
		while x < rect.position.x + size.x:
			draw_line(Vector2(x, y), Vector2(minf(x + 14.0, rect.position.x + size.x), y),
				color.lightened(0.25), 3.0)
			x += 26.0
	else:
		draw_rect(rect, color.darkened(0.4), false, 2.0)
