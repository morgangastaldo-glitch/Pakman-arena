## CharacterVisual
##
## Livello di PRESENTAZIONE del personaggio, separato dalla logica: il
## PlayerController non sa nulla di sprite, colori o animazioni, chiama solo
## `play_state("run")` e questo nodo decide cosa mostrare.
##
## COME SI COLLEGA ALLE VERE ANIMAZIONI
## Se sotto questo nodo esiste un AnimatedSprite2D con una animazione dal nome
## richiesto, viene riprodotta quella. Altrimenti si disegna un segnaposto
## colorato (rettangolo + occhio che indica il verso). Cosi' il prototipo e'
## giocabile SUBITO e, quando arrivano gli sprite, non si tocca la logica:
##   nomi attesi -> idle, run, jump, fall, land, attack_light, attack_heavy,
##                  attack_air, hitstun, dodge, ko
##
## In piu' gestisce la "juice" essenziale: squash & stretch, flash bianco al
## colpo, sfarfallio durante l'invulnerabilita'.
class_name CharacterVisual
extends Node2D

## Colori del segnaposto per stato: leggere lo stato del gioco a colpo d'occhio
## e' ORO durante il playtest.
const STATE_COLORS := {
	"idle": Color(1, 1, 1),
	"run": Color(0.92, 0.98, 1),
	"jump": Color(0.80, 0.95, 1),
	"fall": Color(0.72, 0.86, 1),
	"land": Color(0.95, 1, 0.95),
	"attack_light": Color(1, 0.93, 0.6),
	"attack_light_up": Color(1, 0.93, 0.6),
	"attack_light_down": Color(1, 0.93, 0.6),
	"attack_heavy": Color(1, 0.62, 0.35),
	"attack_heavy_up": Color(1, 0.62, 0.35),
	"attack_heavy_down": Color(1, 0.62, 0.35),
	"attack_air": Color(1, 0.85, 0.55),
	"attack_air_up": Color(1, 0.85, 0.55),
	"attack_air_down": Color(1, 0.85, 0.55),
	"attack_air_heavy": Color(1, 0.6, 0.3),
	"hitstun": Color(1, 0.42, 0.45),
	"dodge": Color(0.6, 0.85, 1),
	"ko": Color(0.35, 0.35, 0.4),
}

@export var body_size: Vector2 = Vector2(34, 58)
## Tinta base del giocatore (viene sovrascritta dal PlayerController).
@export var base_color: Color = Color("4fc3f7")

var _anim: AnimatedSprite2D = null
var _state_name: String = "idle"
var _facing: int = 1
var _flash: float = 0.0
var _squash: Vector2 = Vector2.ONE
var _invulnerable: bool = false
var _blink: float = 0.0

func _ready() -> void:
	# L'AnimatedSprite2D e' OPZIONALE: se c'e' lo si usa, altrimenti segnaposto.
	_anim = get_node_or_null("AnimatedSprite2D") as AnimatedSprite2D
	set_process(true)

func setup(color: Color) -> void:
	base_color = color
	queue_redraw()

# --- API chiamata dal PlayerController ------------------------------------

## Cambia stato visivo. Idempotente: chiamarla ogni frame non costa nulla.
func play_state(state_name: String) -> void:
	if state_name == _state_name:
		return
	_state_name = state_name
	if _anim != null and _anim.sprite_frames != null and _anim.sprite_frames.has_animation(state_name):
		_anim.play(state_name)
	queue_redraw()

func set_facing(facing: int) -> void:
	_facing = 1 if facing >= 0 else -1
	if _anim != null:
		_anim.flip_h = _facing < 0
	queue_redraw()

## Lampo bianco all'impatto.
func flash(duration: float = 0.12) -> void:
	_flash = maxf(_flash, duration)

## Deformazione elastica: (1.2, 0.8) = schiacciato, (0.8, 1.2) = allungato.
func squash(scale_factor: Vector2) -> void:
	_squash = scale_factor

func set_invulnerable(value: bool) -> void:
	_invulnerable = value
	if not value:
		modulate.a = 1.0

func _process(delta: float) -> void:
	# Ritorno elastico dello squash verso la scala neutra.
	_squash = _squash.lerp(Vector2.ONE, clampf(delta * 12.0, 0.0, 1.0))
	scale = Vector2(_squash.x * signf(float(_facing)), _squash.y)
	if _flash > 0.0:
		_flash = maxf(_flash - delta, 0.0)
		queue_redraw()
	# Sfarfallio da invulnerabilita' (respawn / i-frame di dodge).
	if _invulnerable:
		_blink += delta
		modulate.a = 0.35 if fmod(_blink, 0.16) < 0.08 else 1.0
	queue_redraw()

func _draw() -> void:
	if _anim != null and _anim.sprite_frames != null and _anim.sprite_frames.get_animation_names().size() > 0:
		return  # ci pensano gli sprite veri
	var tint: Color = STATE_COLORS.get(_state_name, Color.WHITE)
	var body_color: Color = base_color * tint
	if _flash > 0.0:
		body_color = body_color.lerp(Color.WHITE, clampf(_flash * 8.0, 0.0, 1.0))
	# Corpo (origine ai piedi del personaggio: piu' comodo da allineare)
	var rect := Rect2(Vector2(-body_size.x * 0.5, -body_size.y), body_size)
	draw_rect(rect, body_color, true)
	draw_rect(rect, body_color.darkened(0.55), false, 2.0)
	# Occhio: indica sempre il verso, fondamentale per leggere i side attack.
	draw_circle(Vector2(body_size.x * 0.22, -body_size.y * 0.72), 4.5, Color(0.08, 0.08, 0.12))
	# Piedi: barretta scura che aiuta a percepire il contatto col terreno.
	draw_rect(Rect2(Vector2(-body_size.x * 0.5, -4), Vector2(body_size.x, 4)), body_color.darkened(0.7), true)
