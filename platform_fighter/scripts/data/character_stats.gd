## CharacterStats
##
## Tutti i numeri che definiscono "come si sente" un personaggio, in una
## Resource riusabile: si duplica il .tres per creare un nuovo fighter
## (pesante/lento, leggero/veloce) senza toccare una riga di codice.
##
## NOTA SUL SALTO RESPONSIVO
## Invece di esporre "gravity" e "jump_force" (che obbligano a tarare a tentativi),
## si espongono i parametri realmente percepiti dal giocatore:
##   - quanto e' ALTO il salto (px)
##   - quanto tempo impiega a SALIRE
##   - quanto tempo impiega a SCENDERE
## Gravita' e velocita' iniziale sono derivate dalla cinematica:
##   v0 = 2h / t_su          g = 2h / t^2
## Mettendo t_discesa < t_salita si ottiene la curva asimmetrica tipica di
## Smash/Brawlhalla: si sale morbidi e si ricade secchi, mai "fluttuanti".
class_name CharacterStats
extends Resource

@export_group("Identita")
@export var display_name: String = "Fighter"
## Peso: 1.0 = medio. >1 pesante (vola meno lontano), <1 leggero (vola di piu').
## Entra direttamente nella formula di knockback del CombatManager.
@export_range(0.5, 2.5, 0.05) var weight: float = 1.0

@export_group("Movimento a terra")
@export var max_run_speed: float = 340.0
@export var run_acceleration: float = 2800.0
## Attrito applicato quando non si preme nulla: alto = stop secco.
@export var ground_friction: float = 3400.0
## Moltiplicatore di accelerazione quando si inverte la direzione:
## rende il cambio di direzione immediato senza alzare l'accelerazione base.
@export var turn_boost: float = 2.0

@export_group("Movimento in aria")
@export var max_air_speed: float = 300.0
@export var air_acceleration: float = 1600.0
@export var air_friction: float = 500.0
## Quanto controllo resta durante i frame di un attacco aereo (0 = nessuno).
@export_range(0.0, 1.0, 0.05) var attack_air_control: float = 0.35

@export_group("Salto")
@export var jump_height: float = 136.0
@export var jump_time_to_peak: float = 0.34
@export var jump_time_to_descent: float = 0.24
## Salti extra in aria (1 = doppio salto classico).
@export var air_jumps: int = 1
@export var air_jump_height: float = 122.0
## Rilasciando il tasto in salita la velocita' viene tagliata: salto variabile.
@export_range(0.0, 1.0, 0.05) var jump_cut_factor: float = 0.45
## Spinta orizzontale regalata dal salto in aria verso la direzione premuta.
@export var air_jump_horizontal_boost: float = 160.0
## Frame di grazia dopo essere usciti dalla piattaforma (coyote time).
@export var coyote_time: float = 0.10
## Se premi salto poco prima di toccare terra, il salto viene comunque eseguito.
@export var jump_buffer_time: float = 0.12

@export_group("Caduta")
@export var max_fall_speed: float = 900.0
## Velocita' massima in fast fall (giu' in aria).
@export var fast_fall_speed: float = 1450.0
@export var fast_fall_gravity_multiplier: float = 2.2

@export_group("Schivata / Dodge")
@export var dodge_duration: float = 0.42
@export var dodge_invuln_start: float = 0.05
@export var dodge_invuln_end: float = 0.28
@export var dodge_ground_speed: float = 520.0
@export var dodge_air_speed: float = 460.0
## Cooldown dopo una schivata (evita lo spam).
@export var dodge_cooldown: float = 0.55

@export_group("Difesa")
## Moltiplicatore di hitstun subito: <1 = esce prima dallo stordimento.
@export_range(0.2, 2.0, 0.05) var hitstun_resistance: float = 1.0

# --- Valori derivati (cinematica del salto) -------------------------------

## Velocita' verticale iniziale del salto da terra (negativa: Y cresce in basso).
func get_jump_velocity() -> float:
	return -2.0 * jump_height / maxf(jump_time_to_peak, 0.01)

## Velocita' verticale iniziale del salto in aria.
func get_air_jump_velocity() -> float:
	return -2.0 * air_jump_height / maxf(jump_time_to_peak, 0.01)

## Gravita' in salita (piu' bassa: l'ascesa e' morbida e controllabile).
func get_rise_gravity() -> float:
	return 2.0 * jump_height / pow(maxf(jump_time_to_peak, 0.01), 2.0)

## Gravita' in discesa (piu' alta: la ricaduta e' secca, niente "luna").
func get_fall_gravity() -> float:
	return 2.0 * jump_height / pow(maxf(jump_time_to_descent, 0.01), 2.0)
