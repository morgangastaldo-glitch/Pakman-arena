## BlastZone
##
## Zona di RING OUT. Non e' un muro: e' un'Area2D che, quando il corpo di un
## personaggio la tocca, dichiara l'eliminazione. Quattro istanze (sopra, sotto,
## sinistra, destra) delimitano lo spazio giocabile.
##
## PERCHE' UN'AREA E NON UN CONTROLLO SULLE COORDINATE
## L'Area2D usa la stessa fisica del resto: funziona con qualsiasi forma di
## arena, si vede nel debug delle collisioni e si puo' spostare a mano
## nell'editor per tarare quanto e' "perdonante" la mappa.
class_name BlastZone
extends Area2D

## Dimensione della zona; la shape viene creata automaticamente.
@export var size: Vector2 = Vector2(400, 2400):
	set(value):
		size = value
		_rebuild()

var _shape_node: CollisionShape2D
var _rect: RectangleShape2D

func _ready() -> void:
	monitoring = true
	monitorable = false
	collision_layer = CombatLayers.bit(CombatLayers.BLAST)
	# Cerca SOLO i corpi dei giocatori.
	collision_mask = CombatLayers.bit(CombatLayers.PLAYER)
	add_to_group("blast_zone")
	_rebuild()
	body_entered.connect(_on_body_entered)

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

func _on_body_entered(body: Node2D) -> void:
	if not body.is_in_group("player"):
		return
	# Un personaggio gia' KO non deve essere eliminato due volte.
	if body.has_method("get_state_name") and body.get_state_name() == "KO":
		return
	CombatManager.notify_ring_out(body, null)
