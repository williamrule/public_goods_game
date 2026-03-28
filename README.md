# Public Goods Provision with Endogenous Groups

This repository contains an oTree implementation of a 2x2 public-goods experiment with both exogenous and endogenous group formation.

## Project structure
The main experiment code is located in the `public_goods_game/` folder.

## Main contents
- `public_goods_game/pg_exogenous/` — exogenous treatment logic
- `public_goods_game/pg_endogenous/` — endogenous treatment logic
- `public_goods_game/settings.py` — session configuration
- `public_goods_game/requirements.txt` — dependencies
- `public_goods_game/README.md` — detailed project documentation

## Quick start
```bash
cd public_goods_game
pip install -r requirements.txt
otree devserver
