## FightHUD
##
## Interfaccia costruita interamente da codice: un pannello per giocatore con
## percentuale (che cambia colore e "sussulta" a ogni colpo) e vite residue.
## Essendo generata a runtime resta sincronizzata anche aggiungendo un terzo o
## quarto giocatore alla scena.
class_name FightHUD
extends CanvasLayer

const PERCENT_LOW := Color(1, 1, 1)
const PERCENT_MID := Color(1, 0.85, 0.35)
const PERCENT_HIGH := Color(1, 0.35, 0.28)
## Percentuale a cui il colore raggiunge il rosso pieno.
const PERCENT_DANGER := 150.0

var _panels: Dictionary = {}   # instance_id -> Dictionary con i nodi
var _root: HBoxContainer
var _banner: Label

func _ready() -> void:
	layer = 10
	_build_root()
	call_deferred("_bind_players")

func _build_root() -> void:
	var margin := MarginContainer.new()
	margin.set_anchors_preset(Control.PRESET_BOTTOM_WIDE)
	margin.offset_top = -132
	margin.add_theme_constant_override("margin_bottom", 18)
	margin.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(margin)

	_root = HBoxContainer.new()
	_root.alignment = BoxContainer.ALIGNMENT_CENTER
	_root.add_theme_constant_override("separation", 48)
	_root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	margin.add_child(_root)

	_banner = Label.new()
	_banner.set_anchors_preset(Control.PRESET_CENTER_TOP)
	_banner.offset_top = 60
	_banner.offset_left = -400
	_banner.offset_right = 400
	_banner.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_banner.add_theme_font_size_override("font_size", 42)
	_banner.visible = false
	add_child(_banner)

func _bind_players() -> void:
	var manager := get_tree().get_first_node_in_group("game_manager") as GameManager
	for player in get_tree().get_nodes_in_group("player"):
		_add_panel(player, manager)
	if manager != null:
		manager.stocks_changed.connect(_on_stocks_changed)
		manager.match_ended.connect(_on_match_ended)

func _add_panel(player: Node, manager: GameManager) -> void:
	var box := VBoxContainer.new()
	box.alignment = BoxContainer.ALIGNMENT_CENTER
	box.mouse_filter = Control.MOUSE_FILTER_IGNORE

	var name_label := Label.new()
	name_label.text = "P%d" % player.player_index
	name_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	name_label.add_theme_font_size_override("font_size", 20)
	name_label.add_theme_color_override("font_color", player.player_color)
	box.add_child(name_label)

	var percent_label := Label.new()
	percent_label.text = "0%"
	percent_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	percent_label.add_theme_font_size_override("font_size", 52)
	box.add_child(percent_label)

	var stock_label := Label.new()
	stock_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	stock_label.add_theme_font_size_override("font_size", 22)
	stock_label.text = "● ".repeat(manager.stocks_per_player if manager != null else 3)
	box.add_child(stock_label)

	_root.add_child(box)
	_panels[player.get_instance_id()] = {
		"percent": percent_label,
		"stock": stock_label,
		"box": box,
	}
	player.percent_changed.connect(_on_percent_changed.bind(player))
	_on_percent_changed(player.get_percent(), player)

func _on_percent_changed(percent: float, player: Node) -> void:
	var panel: Dictionary = _panels.get(player.get_instance_id(), {})
	if panel.is_empty():
		return
	var label: Label = panel["percent"]
	label.text = "%d%%" % int(percent)
	# Gradiente bianco -> giallo -> rosso: comunica il pericolo senza numeri.
	var t: float = clampf(percent / PERCENT_DANGER, 0.0, 1.0)
	var color: Color = PERCENT_LOW.lerp(PERCENT_MID, minf(t * 2.0, 1.0))
	if t > 0.5:
		color = PERCENT_MID.lerp(PERCENT_HIGH, (t - 0.5) * 2.0)
	label.add_theme_color_override("font_color", color)
	# Piccolo "pop" a ogni danno subito.
	label.pivot_offset = label.size * 0.5
	label.scale = Vector2(1.35, 1.35)
	create_tween().tween_property(label, "scale", Vector2.ONE, 0.18) \
		.set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)

func _on_stocks_changed(player: Node, remaining: int) -> void:
	var panel: Dictionary = _panels.get(player.get_instance_id(), {})
	if panel.is_empty():
		return
	var label: Label = panel["stock"]
	label.text = "● ".repeat(maxi(remaining, 0)) if remaining > 0 else "OUT"

func _on_match_ended(winner: Node) -> void:
	_banner.visible = true
	if winner != null:
		_banner.text = "P%d VINCE!   (R per rigiocare)" % winner.player_index
		_banner.add_theme_color_override("font_color", winner.player_color)
	else:
		_banner.text = "PAREGGIO   (R per rigiocare)"
