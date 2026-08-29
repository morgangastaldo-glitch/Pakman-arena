## HitSpark
##
## Effetto d'impatto procedurale (nessun asset richiesto): un lampo a stella
## orientato lungo la direzione del knockback, che si espande e svanisce.
## Costruito da codice cosi' il prototipo gira anche senza texture.
class_name HitSpark
extends Node2D

var _color: Color = Color(1, 0.9, 0.5)
var _size: float = 0.35
var _life: float = 0.0
var _max_life: float = 0.22

func configure(color: Color, size: float, angle: float) -> void:
	_color = color
	_size = size
	rotation = angle
	_max_life = 0.16 + size * 0.16

func _ready() -> void:
	z_index = 50
	set_process(true)

func _process(delta: float) -> void:
	_life += delta
	if _life >= _max_life:
		queue_free()
		return
	queue_redraw()

func _draw() -> void:
	var t: float = clampf(_life / _max_life, 0.0, 1.0)
	var eased: float = 1.0 - pow(1.0 - t, 3.0)      # espansione con ease-out
	var radius: float = lerpf(10.0, 90.0 * _size + 26.0, eased)
	var alpha: float = 1.0 - t
	var col := Color(_color.r, _color.g, _color.b, alpha)
	# Nucleo
	draw_circle(Vector2.ZERO, radius * 0.35, Color(1, 1, 1, alpha * 0.9))
	# Stella a quattro punte allungata sull'asse del knockback
	var points := PackedVector2Array([
		Vector2(radius * 1.6, 0),
		Vector2(0, radius * 0.42),
		Vector2(-radius * 0.7, 0),
		Vector2(0, -radius * 0.42),
	])
	draw_colored_polygon(points, col)
