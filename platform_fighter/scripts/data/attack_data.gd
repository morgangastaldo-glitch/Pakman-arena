## AttackData
##
## Descrive UN singolo attacco: frame data, danno, geometria della hitbox e
## proprieta' di knockback. E' una Resource, quindi ogni mossa e' un dato e non
## codice: si bilancia il gioco senza ricompilare la logica.
##
## SISTEMA DI FRAME DATA (in secondi, indipendente dal framerate)
##   startup  -> anticipo prima che la hitbox si accenda (telegrafo visivo)
##   active   -> finestra in cui la hitbox puo' colpire
##   recovery -> coda in cui si e' vulnerabili e non si puo' agire
## Regola di design: piu' danno/knockback => startup e recovery piu' lunghi.
class_name AttackData
extends Resource

## LIGHT: veloce, poco knockback, serve ad accumulare percentuale.
## HEAVY: lento, telegrafato, e' la mossa che manda in Ring Out.
enum Type { LIGHT, HEAVY }

@export_group("Identita")
@export var id: String = "light_neutral"
@export var type: Type = Type.LIGHT
## Nome dell'animazione da riprodurre (vedi CharacterVisual.play_state).
@export var animation: String = "attack_light"

@export_group("Frame data (secondi)")
@export var startup: float = 0.06
@export var active: float = 0.06
@export var recovery: float = 0.14

@export_group("Danno e knockback")
## Percentuale aggiunta al bersaglio (NON toglie punti vita).
@export var damage: float = 4.0
## Knockback di base: la spinta che il colpo da' anche a 0%.
@export var base_knockback: float = 120.0
## Quanto il knockback cresce con la percentuale accumulata dal bersaglio.
@export var knockback_scaling: float = 280.0
## Angolo di lancio in gradi: 0 = orizzontale in avanti, 90 = verso l'alto,
## valori negativi = verso il basso (spike / meteor smash).
@export_range(-90.0, 135.0, 1.0) var launch_angle: float = 40.0
## Moltiplicatore sulla durata dello stordimento generato.
@export var hitstun_multiplier: float = 1.0
## Freeze frame all'impatto (hitlag): fermano ENTRAMBI i personaggi e sono
## il 90% della "sensazione di peso" di un colpo.
@export var hitlag: float = 0.05

@export_group("Geometria hitbox (spazio locale, X = davanti)")
@export var hitbox_offset: Vector2 = Vector2(34, 0)
@export var hitbox_size: Vector2 = Vector2(46, 44)

@export_group("Movimento della mossa")
## Spinta in avanti data a chi attacca allo startup (lunge/affondo).
@export var self_forward_impulse: float = 0.0
## Se true la mossa blocca la caduta per un istante (utile per gli aerei).
@export var stall_in_air: bool = false
## Se true la mossa e' utilizzabile solo in aria.
@export var air_only: bool = false

@export_group("Feedback")
@export var shake_intensity: float = 4.0
@export var spark_color: Color = Color(1.0, 0.92, 0.55)

## Direzione di lancio in coordinate di Godot (Y verso il basso),
## gia' orientata secondo il verso in cui guarda chi attacca.
func get_launch_vector(facing: int) -> Vector2:
	var rad: float = deg_to_rad(launch_angle)
	# -sin perche' in 2D di Godot la Y positiva punta verso il basso.
	return Vector2(cos(rad) * signf(float(facing)), -sin(rad)).normalized()

func is_heavy() -> bool:
	return type == Type.HEAVY

## Durata totale della mossa (usata dall'IA/debug per capire quanto si e' esposti).
func total_duration() -> float:
	return startup + active + recovery
