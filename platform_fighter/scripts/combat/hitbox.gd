## Hitbox
##
## Volume OFFENSIVO, attivo soltanto durante i frame "active" di un attacco.
## Un solo nodo Hitbox per personaggio: forma e offset vengono riscritti a ogni
## mossa a partire dall'AttackData, cosi' non servono decine di nodi in scena.
##
## PRECISIONE DELLE COLLISIONI
## Il rilevamento e' DOPPIO e volutamente ridondante:
##   1. segnale area_entered  -> intercetta chi entra mentre la hitbox e' viva;
##   2. polling degli overlap -> intercetta chi era GIA' dentro nell'istante in
##      cui la hitbox si accende (caso frequentissimo negli scambi ravvicinati,
##      dove il solo segnale perderebbe il colpo).
## Un array di gia'-colpiti garantisce comunque UN solo hit per attacco.
class_name Hitbox
extends Area2D

signal hit_confirmed(hurtbox: Hurtbox)

var fighter: Node = null
var team: int = 0

var _attack: AttackData = null
var _active: bool = false
var _already_hit: Array[int] = []   # instance_id dei fighter gia' colpiti

@onready var _shape: CollisionShape2D = get_node_or_null("CollisionShape2D")
var _rect: RectangleShape2D
## Posizione di riposo (centro del corpo): gli offset degli AttackData sono
## relativi a questo punto, non ai piedi del personaggio.
var _base_position: Vector2 = Vector2.ZERO

func _ready() -> void:
	_base_position = position
	collision_layer = CombatLayers.bit(CombatLayers.HITBOX)
	collision_mask = CombatLayers.bit(CombatLayers.HURTBOX)
	monitoring = true
	monitorable = false
	add_to_group("hitbox")
	# Shape dedicata e non condivisa: viene ridimensionata a ogni mossa.
	_rect = RectangleShape2D.new()
	_rect.size = Vector2(40, 40)
	if _shape == null:
		_shape = CollisionShape2D.new()
		_shape.name = "CollisionShape2D"
		add_child(_shape)
	_shape.shape = _rect
	_shape.disabled = true
	set_physics_process(false)
	area_entered.connect(_on_area_entered)

## Accende la hitbox con i dati della mossa. `facing` (+1/-1) specchia l'offset.
func activate(attack: AttackData, facing: int) -> void:
	_attack = attack
	_already_hit.clear()
	_rect.size = attack.hitbox_size
	position = _base_position + Vector2(
		attack.hitbox_offset.x * signf(float(facing)), attack.hitbox_offset.y)
	# Assegnazione diretta (non differita): activate() viene chiamata dal
	# _physics_process del personaggio, dove la modifica e' lecita, e i frame
	# attivi devono partire NELLO STESSO frame previsto dal frame data.
	_shape.disabled = false
	_active = true
	set_physics_process(true)
	queue_redraw()

func deactivate() -> void:
	# _active = false ferma SUBITO polling e segnali, quindi la shape puo'
	# essere spenta in differita. E' necessario: deactivate() puo' arrivare da
	# dentro un callback di fisica (un KO notificato da una BlastZone) e in quel
	# momento il motore rifiuta le modifiche dirette alle collisioni.
	_active = false
	_attack = null
	if _shape != null:
		_shape.set_deferred("disabled", true)
	set_physics_process(false)
	queue_redraw()

func is_active() -> bool:
	return _active

func get_attack() -> AttackData:
	return _attack

func _physics_process(_delta: float) -> void:
	# Polling: copre il caso "bersaglio gia' dentro il volume".
	if not _active:
		return
	for area in get_overlapping_areas():
		_try_hit(area)

func _on_area_entered(area: Area2D) -> void:
	if _active:
		_try_hit(area)

func _try_hit(area: Area2D) -> void:
	var hurtbox := area as Hurtbox
	if hurtbox == null or hurtbox.fighter == null or fighter == null:
		return
	if hurtbox.fighter == fighter:
		return                                  # niente auto-danno
	if not hurtbox.is_vulnerable():
		return                                  # bersaglio in i-frame
	var id: int = hurtbox.fighter.get_instance_id()
	if _already_hit.has(id):
		return                                  # un solo colpo per attacco
	if team != 0 and hurtbox.team == team:
		return                                  # friendly fire disattivato
	_already_hit.append(id)
	hit_confirmed.emit(hurtbox)
	# Il calcolo di danno/knockback NON vive qui: e' centralizzato nel manager.
	CombatManager.resolve_hit(self, hurtbox)

# --- Debug visivo ---------------------------------------------------------
# Con "Debug > Visible Collision Shapes" Godot disegna gia' le shape; questo
# rettangolo rosso resta utile nelle build esportate di playtest.
@export var draw_debug: bool = false

func _draw() -> void:
	if not draw_debug or not _active or _attack == null:
		return
	var r := Rect2(-_rect.size * 0.5, _rect.size)
	draw_rect(r, Color(1, 0.2, 0.25, 0.25), true)
	draw_rect(r, Color(1, 0.3, 0.35, 0.9), false, 2.0)
