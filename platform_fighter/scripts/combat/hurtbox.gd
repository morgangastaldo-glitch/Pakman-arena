## Hurtbox
##
## Volume VULNERABILE del personaggio. E' completamente passivo: non cerca
## nulla, si limita a farsi trovare dalle Hitbox altrui (monitorable = true,
## monitoring = false), che e' anche la configurazione piu' economica per il
## motore fisico.
##
## Durante i frame di invulnerabilita' (dodge, respawn, KO) l'area smette di
## essere monitorabile: il personaggio sparisce dal sistema di combattimento
## pur continuando a muoversi normalmente.
class_name Hurtbox
extends Area2D

## Il fighter proprietario (impostato dal PlayerController in _ready).
var fighter: Node = null
## Team per il friendly fire (0 = free for all).
var team: int = 0
## Sorgente di verita' della vulnerabilita'. Le proprieta' fisiche vengono
## aggiornate in differita (vedi set_vulnerable), quindi non si puo' leggere
## `monitorable` per sapere se il personaggio e' colpibile ADESSO.
var _vulnerable: bool = true

func _ready() -> void:
	collision_layer = CombatLayers.bit(CombatLayers.HURTBOX)
	collision_mask = 0          # non cerca nessuno
	monitoring = false          # non emette segnali: risparmio di CPU
	monitorable = true          # ma puo' essere trovato dalle hitbox
	add_to_group("hurtbox")

## Attiva/disattiva la vulnerabilita' (i-frame di dodge, respawn, KO).
##
## Il cambio di stato arriva spesso DA DENTRO un callback di fisica (un KO
## notificato da una BlastZone, un colpo risolto in area_entered): in quel
## momento il motore sta gia' iterando le sue query e modificare shape o
## monitorable direttamente genera l'errore "Can't change this state while
## flushing queries". Per questo si usa set_deferred e si tiene un flag
## interno come sorgente di verita' immediata.
func set_vulnerable(value: bool) -> void:
	_vulnerable = value
	# Basta rendere l'area non "monitorabile": nessuna hitbox potra' piu'
	# trovarla. Disabilitare anche la shape sarebbe ridondante e, chiamato da
	# dentro una query fisica, il motore lo rifiuterebbe comunque.
	set_deferred("monitorable", value)

func is_vulnerable() -> bool:
	return _vulnerable
