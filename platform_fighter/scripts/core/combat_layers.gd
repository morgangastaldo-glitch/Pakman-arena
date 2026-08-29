## CombatLayers
##
## Tabella centrale dei layer di fisica 2D del gioco.
## Tenere i numeri in UN SOLO posto evita il classico bug del platform fighter
## in cui una hitbox non colpisce perche' qualcuno ha spuntato la casella
## sbagliata nell'inspector.
##
## Convenzione (identica ai "layer_names/2d_physics/*" in project.godot):
##   1 SOLID    -> terreno pieno, muri, la piattaforma principale
##   2 ONE_WAY  -> piattaforme sottili attraversabili dal basso (drop-through)
##   3 PLAYER   -> corpo fisico dei personaggi (NON collide con gli altri player)
##   4 HITBOX   -> volume offensivo attivo solo durante i frame "active"
##   5 HURTBOX  -> volume vulnerabile del personaggio
##   6 BLAST    -> zone di Ring Out fuori dall'arena
class_name CombatLayers
extends RefCounted

const SOLID: int = 1
const ONE_WAY: int = 2
const PLAYER: int = 3
const HITBOX: int = 4
const HURTBOX: int = 5
const BLAST: int = 6

## Converte l'indice di layer (1-32) nella maschera di bit usata da Godot.
static func bit(layer_index: int) -> int:
	return 1 << (layer_index - 1)

## Maschera del "mondo" solido: tutto cio' su cui un personaggio puo' stare.
static func world_mask() -> int:
	return bit(SOLID) | bit(ONE_WAY)
