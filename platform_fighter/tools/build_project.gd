# =============================================================================
#  BUILD PROJECT  -  generatore di input map e scene
# =============================================================================
#
#  Eseguire UNA VOLTA dalla cartella del progetto:
#      godot --headless --import
#      godot --headless res://tools/build_project.tscn
#
#  Cosa fa:
#   1. registra in project.godot le azioni di input di P1 e P2
#      (tastiera + gamepad, device 0 e device 1);
#   2. genera scenes/Player.tscn e scenes/Arena.tscn.
#
#  Perche' generarle da codice invece di scriverle a mano: i file .tscn e la
#  sezione [input] prodotti dal motore sono garantiti validi, e questo script
#  resta la documentazione eseguibile di com'e' fatta l'arena. Dopo la prima
#  generazione si puo' benissimo continuare a lavorare nell'editor.
# =============================================================================
extends Node

const PLAYER_SCENE_PATH := "res://scenes/Player.tscn"
const ARENA_SCENE_PATH := "res://scenes/Arena.tscn"

func _ready() -> void:
	print("== Configurazione input ==")
	_build_input_map()
	print("== Generazione scene ==")
	var player_scene := _build_player_scene()
	_build_arena_scene(player_scene)
	print("Fatto.")
	get_tree().quit()

# -----------------------------------------------------------------------------
#  INPUT MAP
# -----------------------------------------------------------------------------

func _key(code: Key) -> InputEventKey:
	var e := InputEventKey.new()
	# physical_keycode = posizione fisica del tasto: il layout AZERTY/QWERTZ
	# continua a funzionare senza rimappare nulla.
	e.physical_keycode = code
	return e

func _pad_button(index: int, device: int) -> InputEventJoypadButton:
	var e := InputEventJoypadButton.new()
	e.button_index = index
	e.device = device        # 0 = primo controller, 1 = secondo controller
	return e

func _pad_axis(axis: int, value: float, device: int) -> InputEventJoypadMotion:
	var e := InputEventJoypadMotion.new()
	e.axis = axis
	e.axis_value = value
	e.device = device
	return e

func _add_action(name: String, events: Array) -> void:
	ProjectSettings.set_setting("input/" + name, {
		"deadzone": 0.25,
		"events": events,
	})

func _build_input_map() -> void:
	# --- GIOCATORE 1: mano sinistra (WASD + Spazio + F/G + Shift) ---
	_add_action("p1_left", [_key(KEY_A),
		_pad_axis(JOY_AXIS_LEFT_X, -1.0, 0), _pad_button(JOY_BUTTON_DPAD_LEFT, 0)])
	_add_action("p1_right", [_key(KEY_D),
		_pad_axis(JOY_AXIS_LEFT_X, 1.0, 0), _pad_button(JOY_BUTTON_DPAD_RIGHT, 0)])
	_add_action("p1_up", [_key(KEY_W),
		_pad_axis(JOY_AXIS_LEFT_Y, -1.0, 0), _pad_button(JOY_BUTTON_DPAD_UP, 0)])
	_add_action("p1_down", [_key(KEY_S),
		_pad_axis(JOY_AXIS_LEFT_Y, 1.0, 0), _pad_button(JOY_BUTTON_DPAD_DOWN, 0)])
	_add_action("p1_jump", [_key(KEY_SPACE), _pad_button(JOY_BUTTON_A, 0)])
	_add_action("p1_light", [_key(KEY_F), _pad_button(JOY_BUTTON_X, 0)])
	_add_action("p1_heavy", [_key(KEY_G), _pad_button(JOY_BUTTON_B, 0)])
	_add_action("p1_dodge", [_key(KEY_SHIFT), _pad_button(JOY_BUTTON_RIGHT_SHOULDER, 0)])

	# --- GIOCATORE 2: mano destra (frecce + tastierino numerico) ---
	# In alternativa al tastierino (utile sui portatili): H/J/K/L.
	_add_action("p2_left", [_key(KEY_LEFT),
		_pad_axis(JOY_AXIS_LEFT_X, -1.0, 1), _pad_button(JOY_BUTTON_DPAD_LEFT, 1)])
	_add_action("p2_right", [_key(KEY_RIGHT),
		_pad_axis(JOY_AXIS_LEFT_X, 1.0, 1), _pad_button(JOY_BUTTON_DPAD_RIGHT, 1)])
	_add_action("p2_up", [_key(KEY_UP),
		_pad_axis(JOY_AXIS_LEFT_Y, -1.0, 1), _pad_button(JOY_BUTTON_DPAD_UP, 1)])
	_add_action("p2_down", [_key(KEY_DOWN),
		_pad_axis(JOY_AXIS_LEFT_Y, 1.0, 1), _pad_button(JOY_BUTTON_DPAD_DOWN, 1)])
	_add_action("p2_jump", [_key(KEY_KP_0), _key(KEY_L), _pad_button(JOY_BUTTON_A, 1)])
	_add_action("p2_light", [_key(KEY_KP_1), _key(KEY_J), _pad_button(JOY_BUTTON_X, 1)])
	_add_action("p2_heavy", [_key(KEY_KP_2), _key(KEY_K), _pad_button(JOY_BUTTON_B, 1)])
	_add_action("p2_dodge", [_key(KEY_KP_3), _key(KEY_H), _pad_button(JOY_BUTTON_RIGHT_SHOULDER, 1)])

	# --- Globali ---
	_add_action("restart_match", [_key(KEY_R),
		_pad_button(JOY_BUTTON_START, 0), _pad_button(JOY_BUTTON_START, 1)])

	# Nomi dei layer di fisica: rende leggibile l'inspector.
	ProjectSettings.set_setting("layer_names/2d_physics/layer_1", "solid")
	ProjectSettings.set_setting("layer_names/2d_physics/layer_2", "one_way")
	ProjectSettings.set_setting("layer_names/2d_physics/layer_3", "player")
	ProjectSettings.set_setting("layer_names/2d_physics/layer_4", "hitbox")
	ProjectSettings.set_setting("layer_names/2d_physics/layer_5", "hurtbox")
	ProjectSettings.set_setting("layer_names/2d_physics/layer_6", "blast_zone")
	# 60 tick fissi: il frame data degli attacchi e' espresso in secondi ma il
	# gioco resta deterministico e identico su ogni macchina.
	ProjectSettings.set_setting("physics/common/physics_ticks_per_second", 60)

	var err := ProjectSettings.save()
	if err != OK:
		push_error("Impossibile salvare project.godot: %d" % err)
	else:
		print("  project.godot aggiornato (17 azioni di input).")

# -----------------------------------------------------------------------------
#  UTILITY DI COSTRUZIONE SCENE
# -----------------------------------------------------------------------------

## Assegna la proprieta' owner a tutta la gerarchia: senza owner corretto i
## nodi NON verrebbero salvati dentro il .tscn.
func _set_owner_recursive(node: Node, owner_node: Node) -> void:
	for child in node.get_children():
		child.owner = owner_node
		# Le istanze di sotto-scene si salvano come riferimento: i loro figli
		# appartengono alla scena originale e non vanno toccati.
		if child.scene_file_path.is_empty():
			_set_owner_recursive(child, owner_node)

func _save_scene(root: Node, path: String) -> PackedScene:
	var packed := PackedScene.new()
	var err := packed.pack(root)
	if err != OK:
		push_error("pack() fallito per %s: %d" % [path, err])
		return null
	err = ResourceSaver.save(packed, path)
	if err != OK:
		push_error("save() fallito per %s: %d" % [path, err])
		return null
	print("  creata %s" % path)
	# L'albero e' servito solo per il pack: liberarlo tiene pulito l'output.
	root.free()
	return load(path)

func _rect_shape(size: Vector2) -> RectangleShape2D:
	var s := RectangleShape2D.new()
	s.size = size
	return s

# -----------------------------------------------------------------------------
#  SCENA DEL PERSONAGGIO
# -----------------------------------------------------------------------------
#
#  Fighter (CharacterBody2D)      <- fisica + macchina a stati
#  |- CollisionShape2D            <- corpo che collide col mondo
#  |- Visual (CharacterVisual)    <- sprite/segnaposto, animazioni
#  |- Hurtbox (Area2D)            <- volume vulnerabile
#  |  \- CollisionShape2D
#  \- Hitbox (Area2D)             <- volume offensivo (forma dinamica)
#     \- CollisionShape2D
#
#  L'origine del personaggio e' ai PIEDI (y = 0): posizionarlo sulle
#  piattaforme e' immediato e la logica di atterraggio resta leggibile.
func _build_player_scene() -> PackedScene:
	var body_size := Vector2(34, 58)
	var center := Vector2(0, -body_size.y * 0.5)

	var root := PlayerController.new()
	root.name = "Fighter"

	var body_shape := CollisionShape2D.new()
	body_shape.name = "CollisionShape2D"
	body_shape.shape = _rect_shape(body_size)
	body_shape.position = center
	root.add_child(body_shape)

	var visual := CharacterVisual.new()
	visual.name = "Visual"
	visual.body_size = body_size
	root.add_child(visual)

	var hurtbox := Hurtbox.new()
	hurtbox.name = "Hurtbox"
	var hurt_shape := CollisionShape2D.new()
	hurt_shape.name = "CollisionShape2D"
	# Leggermente piu' generosa del corpo: i colpi "di striscio" registrano.
	hurt_shape.shape = _rect_shape(body_size + Vector2(6, 4))
	hurt_shape.position = center
	hurtbox.add_child(hurt_shape)
	root.add_child(hurtbox)

	var hitbox := Hitbox.new()
	hitbox.name = "Hitbox"
	# La hitbox parte dal centro del corpo: gli offset degli AttackData sono
	# relativi a questo punto.
	hitbox.position = center
	var hit_shape := CollisionShape2D.new()
	hit_shape.name = "CollisionShape2D"
	hit_shape.shape = _rect_shape(Vector2(40, 40))
	hit_shape.disabled = true
	hitbox.add_child(hit_shape)
	root.add_child(hitbox)

	_set_owner_recursive(root, root)
	return _save_scene(root, PLAYER_SCENE_PATH)

# -----------------------------------------------------------------------------
#  SCENA DELL'ARENA
# -----------------------------------------------------------------------------
func _build_arena_scene(player_scene: PackedScene) -> void:
	var root := Node2D.new()
	root.name = "Arena"

	# --- Sfondo ---
	var bg_layer := CanvasLayer.new()
	bg_layer.name = "Background"
	bg_layer.layer = -10
	var bg := ColorRect.new()
	bg.name = "Sky"
	bg.set_anchors_preset(Control.PRESET_FULL_RECT)
	bg.color = Color("11141d")
	bg.mouse_filter = Control.MOUSE_FILTER_IGNORE
	bg_layer.add_child(bg)
	root.add_child(bg_layer)

	# --- Geometria dell'arena ---
	var world := Node2D.new()
	world.name = "World"
	root.add_child(world)
	world.add_child(_make_platform("MainPlatform", Vector2(0, 180), Vector2(760, 56), false))
	world.add_child(_make_platform("PlatformLeft", Vector2(-330, 10), Vector2(250, 24), true))
	world.add_child(_make_platform("PlatformRight", Vector2(330, 10), Vector2(250, 24), true))
	world.add_child(_make_platform("PlatformTop", Vector2(0, -150), Vector2(270, 24), true))

	# --- Punti di respawn ---
	var spawns := Node2D.new()
	spawns.name = "SpawnPoints"
	root.add_child(spawns)
	var spawn_positions := [Vector2(-220, 60), Vector2(220, 60), Vector2(0, -260), Vector2(-330, -90)]
	for i in spawn_positions.size():
		var marker := Marker2D.new()
		marker.name = "Spawn%d" % (i + 1)
		marker.position = spawn_positions[i]
		marker.add_to_group("spawn_point", true)
		spawns.add_child(marker)

	# --- Zone di Ring Out ---
	# Superficie giocabile: x in [-380, 380], suolo a y = 152.
	# Le blast zone stanno molto piu' in la': serve spazio per le recovery.
	var blasts := Node2D.new()
	blasts.name = "BlastZones"
	root.add_child(blasts)
	blasts.add_child(_make_blast_zone("BlastLeft", Vector2(-1150, -150), Vector2(400, 2600)))
	blasts.add_child(_make_blast_zone("BlastRight", Vector2(1150, -150), Vector2(400, 2600)))
	blasts.add_child(_make_blast_zone("BlastTop", Vector2(0, -1050), Vector2(2700, 400)))
	blasts.add_child(_make_blast_zone("BlastBottom", Vector2(0, 900), Vector2(2700, 400)))

	# --- Giocatori ---
	var fighters := Node2D.new()
	fighters.name = "Fighters"
	root.add_child(fighters)
	var p1 := player_scene.instantiate()
	p1.name = "Player1"
	p1.position = Vector2(-220, 60)
	p1.player_index = 1
	p1.player_color = Color("4fc3f7")
	fighters.add_child(p1)
	var p2 := player_scene.instantiate()
	p2.name = "Player2"
	p2.position = Vector2(220, 60)
	p2.player_index = 2
	p2.player_color = Color("ff7043")
	p2.facing = -1
	fighters.add_child(p2)

	# --- Camera, regia, HUD ---
	var camera := ArenaCamera.new()
	camera.name = "ArenaCamera"
	camera.position = Vector2(0, -40)
	root.add_child(camera)

	var manager := GameManager.new()
	manager.name = "GameManager"
	root.add_child(manager)

	var hud := FightHUD.new()
	hud.name = "HUD"
	root.add_child(hud)

	_set_owner_recursive(root, root)
	# Le istanze di Player.tscn devono avere owner ma NON i loro figli.
	p1.owner = root
	p2.owner = root
	_save_scene(root, ARENA_SCENE_PATH)

func _make_platform(node_name: String, pos: Vector2, size: Vector2, one_way: bool) -> FightPlatform:
	var platform := FightPlatform.new()
	platform.name = node_name
	platform.position = pos
	platform.size = size
	platform.one_way = one_way
	platform.color = Color("4a5b7a") if one_way else Color("36445d")
	var shape := CollisionShape2D.new()
	shape.name = "CollisionShape2D"
	shape.shape = _rect_shape(size)
	shape.one_way_collision = one_way
	shape.one_way_collision_margin = 12.0
	platform.add_child(shape)
	if one_way:
		platform.add_to_group("one_way_platform", true)
	return platform

func _make_blast_zone(node_name: String, pos: Vector2, size: Vector2) -> BlastZone:
	var zone := BlastZone.new()
	zone.name = node_name
	zone.position = pos
	zone.size = size
	var shape := CollisionShape2D.new()
	shape.name = "CollisionShape2D"
	shape.shape = _rect_shape(size)
	zone.add_child(shape)
	zone.add_to_group("blast_zone", true)
	return zone
